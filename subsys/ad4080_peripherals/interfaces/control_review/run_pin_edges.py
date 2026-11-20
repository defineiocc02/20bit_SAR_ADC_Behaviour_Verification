# =============================================================================
# 文件名     : run_pin_edges.py
# 模块名     : run_pin_edges
# 版本       : v1.0
# 功能       : 用 Verilator 执行冻结 DSP 的 GPIO0 SPI 引脚逐沿验证。
# 设计说明   :
#   * 默认从固定审计提交解包。联调时可显式输入单独的 SPI 候选文件，
#     输出实际来源摘要。通过与失败实验都将保留已完成场景的 CSV。
#   * 仅在用户提供的外部 scratch 编译，失败后保留编译和仿真日志。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from run import BASELINE, RTL, SOURCES, command_to_log


TOP = "cfg_spi_pin_edges_tb"


# =============================================================================
# 1. 编译输入和真实引脚模拟器
# =============================================================================
def run_checks(args):
    repo, scratch = args.repo.resolve(), args.scratch.resolve()
    if not (repo / ".git").exists() or scratch == repo or repo in scratch.parents:
        raise ValueError("需指定 Git 根和仓库以外的 scratch")
    tool = shutil.which(args.verilator)
    if not tool:
        raise ValueError("找不到 Verilator")
    git = ["git", "-C", str(repo)]
    commit = subprocess.check_output(
        git + ["show", "-s", "--format=%H", args.revision], text=True
    ).strip()
    if len(commit) != 40:
        raise ValueError("无法读取固定基础提交")
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="cfg-pins-", dir=scratch))
    snapshot = subprocess.check_output(git + ["archive", commit, RTL])
    subprocess.run(["tar", "-xf", "-"], input=snapshot, check=True, cwd=work)
    if args.spi_candidate is not None:
        # 只覆盖本次临时拷贝中的 SPI 叶子。寄存器及 GPIO 仍用指定提交。
        shutil.copyfile(args.spi_candidate, work / RTL / "spi_slave.sv")
    if args.dsp_candidate_dir is not None:
        # 联调未提交的统一 DSP 样本时一次性复制全部列入的叶子并逐一记录摘要。
        for name in SOURCES:
            leaf = f"{name}.sv"
            shutil.copyfile(args.dsp_candidate_dir / leaf, work / RTL / leaf)
        # DSP 同目录 include（例如补偿系数表）随候选叶子一起绑定。
        for header in args.dsp_candidate_dir.glob("*.svh"):
            shutil.copyfile(header, work / RTL / header.name)
    tb = work / f"{TOP}.sv"
    shutil.copyfile(Path(__file__).with_name(tb.name), tb)

    env = os.environ.copy()
    tool_root = Path(tool).absolute().parent.parent
    if (tool_root / "include" / "verilated.h").is_file():
        env["VERILATOR_ROOT"] = str(tool_root)
    if not shutil.which("python"):
        (work / "python").symlink_to(sys.executable)
        env["PATH"] = os.pathsep.join((str(work), env.get("PATH", "")))
    inputs = [work / RTL / f"{name}.sv" for name in SOURCES]
    print(f"run={work}\nbaseline={commit}\nspi_candidate={args.spi_candidate is not None}"
          f"\ndsp_candidate={args.dsp_candidate_dir is not None}\nlvds_profile={args.lvds_profile}"
          f"\ncrc_stream={args.crc_stream}\ninit_poll={args.init_poll}", flush=True)
    for path in (tb, *inputs, *sorted((work / RTL).glob("*.svh"))):
        print(f"source_sha256 {hashlib.sha256(path.read_bytes()).hexdigest()} {path.name}", flush=True)

    # 并行生成对象的 PCH 在本机 Clang 预编译头配置中不可用，选单编译单元。
    defines = ["+define+CFG_SPI_LVDS_PROFILE"] if args.lvds_profile else []
    if args.crc_stream:
        defines.append("+define+CFG_SPI_CRC_STREAM")
    if args.init_poll:
        defines.append("+define+CFG_SPI_INIT_POLL")
    command_to_log([
        tool, *defines, "--binary", "--timing", "--assert", "--compiler", "clang",
        "-CFLAGS", "-std=c++20", "-j", "2", "-Wno-fatal",
        "-MAKEFLAGS", "VM_PARALLEL_BUILDS=0 OPT_FAST=-O0",
        "-I"+str(work/RTL), "--top-module", TOP, str(tb), *(str(path) for path in inputs),
    ], work, env, work / "compile.log")

    # 先保全观测再判断退出码。反例实验的源摘要和物理引脚输出同样可复核。
    log = work / "simulate.log"
    with log.open("w") as out:
        result = subprocess.run([str(work / "obj_dir" / f"V{TOP}")], cwd=work, env=env,
                                stdout=out, stderr=subprocess.STDOUT)
    rows = log.read_text().splitlines()
    columns = [line.split(",", 1)[1] for line in rows if line.startswith("PIN_COLUMNS,")]
    cases = [line.split(",", 1)[1] for line in rows if line.startswith("PIN_CASE,")]
    if len(columns) != 1 or not cases:
        raise RuntimeError(f"未形成引脚证据，见 {log}")
    csv_path = work / "gpio0_edges.csv"
    csv_path.write_text("\n".join((*columns, *cases)) + "\n")
    print(f"cases_csv={csv_path}")
    summary = [line for line in rows if line.startswith("CFG_SPI_PIN_EDGES_COMPLETE")]
    for line in summary:
        print(line)
    lvds = [line for line in rows if line.startswith("LVDS_CFG_SPI_COMPLETE")]
    for line in lvds:
        print(line)
    stream = [line for line in rows if line.startswith("CFG_SPI_STREAM_CRC_COMPLETE")]
    for line in stream:
        print(line)
    # 长短流 CRC 是另一个可选择的独立证据，不把缺席的专项当作通过。
    poll = [line for line in rows if line.startswith("CFG_SPI_RESET_POLL_COMPLETE")]
    for line in poll:
        print(line)
    finished = bool(summary and summary[0].endswith("failures=0") and
                    (not args.lvds_profile or lvds) and (not args.crc_stream or stream) and
                    (not args.init_poll or poll))
    return 0 if result.returncode == 0 and finished else 1


# =============================================================================
# 2. CLI
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="验证真实 DSP GPIO0 SPI 模式 3 引脚时序")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--revision", default=BASELINE, help="缺省核对产品冻结的同一 DSP 提交")
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument("--spi-candidate", type=Path)
    candidate.add_argument("--dsp-candidate-dir", type=Path)
    parser.add_argument("--lvds-profile", action="store_true")
    parser.add_argument("--crc-stream", action="store_true")
    parser.add_argument("--init-poll", action="store_true")
    parser.add_argument("--verilator", default=os.getenv("VERILATOR", "verilator"))
    try:
        return run_checks(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"pin_edges 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
