# =============================================================================
# 文件名     : run.py
# 模块名     : control_review
# 版本       : v1.0
# 功能       : 在独立临时目录复放冻结 DSP 控制面并保留开源仿真日志。
# 设计说明   :
#   * 先核对完整提交 ID。只从 Git 对象归档独立 DSP RTL，避免当前工作树
#     的内容进入仿真。缺少原提交、工具或完整日志时退出失败。
#   * 编译、生成文件和测试副本进入用户指定的 scratch，不写主仓。
#   * 测试成功表示基线行为已复放，其中 COUNTEREXAMPLE 仍代表待关闭项。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# 各入口共用同一已发布 DSP 源提交，防止台架与产品绑定读到不同的代码。
BASELINE = (Path(__file__).resolve().parents[2] / "evidence/dsp-revision.txt").read_text(encoding="ascii").strip()
RTL = "subsys/ad4080_dsp/rtl"
SOURCES = (
    "ad4080_top", "spi_slave", "ad4080_regs", "ad4080_dsp_chain", "sys_error_corr",
    "sinc1_decim", "sinc5_decim", "sinc5_comp", "event_detect", "fifo_capture",
    "result_fifo", "gpio_ctrl",
)


# =============================================================================
# 1. 冻结源码与本地构建
# =============================================================================
def command_to_log(command, cwd, env, log):
    """将一次工具调用的 stdout/stderr 写入 scratch 日志，失败时输出日志尾。"""
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        raise RuntimeError(f"工具退出 {result.returncode}，见 {log}\n" + "\n".join(tail))


def run(args):
    repo, scratch = args.repo.resolve(), args.scratch.resolve()
    if not (repo / ".git").exists():
        raise ValueError("--repo 须指向冻结源仓的工作树根目录")
    if scratch == repo or repo in scratch.parents:
        raise ValueError("scratch 须位于源仓之外")
    if args.jobs < 1:
        raise ValueError("jobs 须为正整数")
    tool = shutil.which(args.verilator)
    if tool is None:
        raise ValueError(f"找不到 Verilator {args.verilator}")
    git = ["git", "-C", str(repo)]
    pinned = subprocess.check_output(git + ["show", "-s", "--format=%H", BASELINE], text=True).strip()
    if pinned != BASELINE:
        raise ValueError(f"冻结基线身份不符，期望 {BASELINE}，实际 {pinned}")

    # 同一 scratch 可有多个并行实验。每次使用新目录，确保无旧网表、编译缓存或日志串入。
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="spi-control-", dir=scratch))
    source = work / "source"
    source.mkdir()
    archive = subprocess.check_output(git + ["archive", "--format=tar", BASELINE, RTL])
    subprocess.run(["tar", "-xf", "-", "-C", str(source)], input=archive, check=True, cwd=work)
    # 本机某些 Verilator 生成器查找 python 命令。临时别名限于该 run，保留原环境。
    env = os.environ.copy()
    if shutil.which("python") is None:
        shim = work / "tool-shims"
        shim.mkdir()
        (shim / "python").symlink_to(sys.executable)
        env["PATH"] = os.pathsep.join((str(shim), env.get("PATH", "")))
    # 保留工具目录的可用别名。Makefile 会直接引用 VERILATOR_ROOT 中的 include 路径。
    tool_root = Path(tool).absolute().parent.parent
    if (tool_root / "include" / "verilated.h").is_file():
        env["VERILATOR_ROOT"] = str(tool_root)
    version = subprocess.check_output([tool, "--version"], cwd=work, env=env, text=True).strip()
    print(f"baseline={BASELINE}\n{version}\nrun={work}")
    here = Path(__file__).resolve().parent
    power = here.parents[1] / "power"
    suites = {
        "baseline": ("control_review_tb", ("OBS watermark_host ", "CONTROL_REVIEW_COMPLETE"), SOURCES, ()),
        "bridge": ("power_bridge_tb", ("OBS bridge_gpio ", "POWER_BRIDGE_COMPLETE"),
                   ("spi_slave", "ad4080_regs"),
                   (here / "ad4080_csr_power_bridge.sv", power / "ad4080_pwr_supervisor.sv",
                    power / "ad4080_pwr_reset_sync.sv")),
    }
    selected = suites if args.suite == "all" else (args.suite,)
    for suite in selected:
        top, markers, rtl, package_deps = suites[suite]
        tb = here / f"{top}.sv"
        files = []
        # 新外围/测试模型也复制到本次独立 ASCII 构建路径，避免 Makefile 路径转义。
        for path in (tb, *package_deps):
            copied = work / path.name
            shutil.copyfile(path, copied)
            files.append(str(copied))
        obj = work / f"obj_{suite}"
        compile_command = [
            tool, "--binary", "--timing", "--assert", "--compiler", "clang", "-CFLAGS", "-std=c++20",
            "-j", str(args.jobs), "-Wno-fatal", "-MAKEFLAGS", "VM_PARALLEL_BUILDS=0 OPT_FAST=-O0",
            "--Mdir", str(obj), "-I"+str(source/RTL), "--top-module", top, *files,
            *(str(source / RTL / f"{name}.sv") for name in rtl),
        ]
        command_to_log(compile_command, work, env, work / f"{suite}.compile.log")
        sim_log = work / f"{suite}.simulate.log"
        command_to_log([str(obj / f"V{top}")], work, env, sim_log)
        transcript = sim_log.read_text(encoding="utf-8")
        # 除终点外，还要求本轮的两个专项判据确实运行至其证据行。
        if not all(marker in transcript for marker in markers):
            raise RuntimeError(f"专项或终点断言未完成，见 {sim_log}")
        print(transcript, end="")


# =============================================================================
# 2. 参数及退出约定
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="复放 AD4080 工程基线的配置 SPI 和事件控制")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--verilator", default=os.environ.get("VERILATOR", "verilator"))
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--suite", choices=("all", "baseline", "bridge"), default="all")
    try:
        run(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"control_review 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
