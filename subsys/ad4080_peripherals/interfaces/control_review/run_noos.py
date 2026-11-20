# =============================================================================
# 文件名     : run_noos.py
# 模块名     : run_noos
# 版本       : v1.0
# 功能       : 使用固定的 ADI no-OS 驱动构建 C 到 RTL 配置 SPI 回放。
# 设计说明   :
#   * 只从指定归档读取列出的原文件，逐个比对 SHA-256，厂商源仅进入
#     scratch。驱动按 C 编译，平台 shim 和开源 RTL 仿真按 C++ 链接。
#   * 冻结独立 DSP 的 SPI/CSR 从 Git 对象取入。未知源码、路径错误、
#     编译失败或缺定向结论均退出失败。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
from run import BASELINE, RTL, command_to_log


ADI_COMMIT = "0e6ed1dea135ef541ad6323fc9fcad116ffce90b"
ADI_FILES = {
    "drivers/adc/ad4080/ad4080.c": "0b30e7424acf70ddb81fab8f203eb10dfefc4adf1b6348904a4be60498954c4d",
    "drivers/adc/ad4080/ad4080.h": "e513771273f961a08c8da4388e66df1ad62f28cb8027cc1114d9284bedee2a27",
    "util/no_os_util.c": "1d4b345545badbbc3d34deaaba9f77c3674e2ec26dce6547f11e448bb1a5f588",
    "include/no_os_util.h": "52d029d32f9ab536297849b723586da9146e40900b62276560eb9e7c656f2427",
    "include/no_os_spi.h": "ce2d6ca8f3dd8afe3e0d806e26f7ad4d05d8d52ec0dae957009f705e43e9bea8",
    "include/no_os_gpio.h": "6618cec6018cde4ef0cbf7ceac815d8131f23e43b5f770902aadbd6452f2bd19",
    "include/no_os_delay.h": "75d1455b6fd271ea0674059cfc244374a33c798e8705327e2b08812866d2c626",
}
MARKERS = (
    "OBS noos_init ", "OBS noos_data_profile ", "OBS noos_mode ", "OBS noos_filter ",
    "OBS noos_gpio ", "COUNTEREXAMPLE noos_unmodified_default_4000 ",
    "OBS noos_fifo_recipe ", "OBS noos_strict ", "NOOS_HOST_COMPLETE ", "NOOS_SPI_RTL_COMPLETE ",
)


# =============================================================================
# 1. 不可变输入
# =============================================================================
def unpack_driver(archive, out):
    """逐文件校验供应商归档，保存未改的 C 和编译头文件以供本次运行。"""
    out.mkdir()
    with tarfile.open(archive, "r:gz") as vendor:
        for relative, expected in ADI_FILES.items():
            member = vendor.getmember(f"no-OS-{ADI_COMMIT}/{relative}")
            if not member.isfile():
                raise ValueError(f"{relative} 须为常规文件")
            with vendor.extractfile(member) as source:
                data = source.read()
            if sha256(data).hexdigest() != expected:
                raise ValueError(f"ADI 文件哈希不符：{relative}")
            (out / Path(relative).name).write_bytes(data)


def run_harness(args):
    repo, scratch = args.repo.resolve(), args.scratch.resolve()
    if not (repo / ".git").exists() or scratch == repo or repo in scratch.parents:
        raise ValueError("需指定冻结 Git 工作树根和工作树之外的 scratch")
    tool = shutil.which(args.verilator)
    compiler = shutil.which("clang")
    if not tool or not compiler:
        raise ValueError("无法找到 Verilator 或 Clang")
    git = ["git", "-C", str(repo)]
    commit = subprocess.check_output(git + ["show", "-s", "--format=%H", BASELINE], text=True).strip()
    if commit != BASELINE:
        raise ValueError("冻结提交与验证配方不符")
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="adi-noos-", dir=scratch))
    vendor = work / "adi"
    unpack_driver(args.adi_archive, vendor)

    # 只归档需要编译的两份冻结 RTL，项目适配器与监督器从当前交付包取入。
    archive = subprocess.check_output(git + [
        "archive", BASELINE, f"{RTL}/spi_slave.sv", f"{RTL}/ad4080_regs.sv",
    ])
    subprocess.run(["tar", "-xf", "-"], input=archive, cwd=work, check=True)
    if args.dsp_candidate_dir is not None:
        # 联调候选与最终 pin 均给真实编译 SHA，厂商 C 始终按归档哈希验证。
        for name in ("spi_slave.sv", "ad4080_regs.sv"):
            shutil.copyfile(args.dsp_candidate_dir / name, work / RTL / name)
    here = Path(__file__).resolve().parent
    power = here.parents[1] / "power"
    local_inputs = (
        here / "noos_spi_rtl.sv", here / "ad4080_csr_power_bridge.sv",
        here / "noos_spi_sim.cpp", here / "noos_host_recipe.c",
        power / "ad4080_pwr_supervisor.sv", power / "ad4080_pwr_reset_sync.sv",
    )
    for path in local_inputs:
        shutil.copyfile(path, work / path.name)

    # 复用 tool path 中的 ASCII 别名，以免 Makefile 展开到本机带宽字符路径。
    env = os.environ.copy()
    alias_root = Path(tool).absolute().parent.parent
    if (alias_root / "include" / "verilated.h").is_file():
        env["VERILATOR_ROOT"] = str(alias_root)
    if not shutil.which("python"):
        (work / "python").symlink_to(sys.executable)
        env["PATH"] = os.pathsep.join((str(work), env.get("PATH", "")))

    # 厂商 .c 文件由 C 编译器独立翻译。应用和 shim 只通过原头文件及 ABI 与其链接。
    vendor_object = work / "adi_ad4080.o"
    utility_object = work / "adi_no_os_util.o"
    host_object = work / "noos_host_recipe.o"
    c_sources = ((vendor / "ad4080.c", vendor_object, ()), (vendor / "no_os_util.c", utility_object, ()),
                 (work / "noos_host_recipe.c", host_object, ("-DAD4080_NOOS_SIM",)))
    for src, obj, options in c_sources:
        command_to_log([compiler, "-std=gnu11", "-I", str(vendor), *options, "-c", str(src), "-o", str(obj)],
                       work, env, work / f"{obj.stem}.compile.log")
    object_dir = work / "obj"
    link = f"{vendor_object} {utility_object} {host_object}"
    verilog = [str(work / p.name) for p in local_inputs if p.suffix == ".sv"]
    verilog += [str(work / RTL / f"{part}.sv") for part in ("spi_slave", "ad4080_regs")]
    command_to_log([
        tool, "--cc", "--exe", "--build", "--assert", "-Wno-fatal", "--compiler", "clang",
        "-j", "2", "-MAKEFLAGS", "VM_PARALLEL_BUILDS=0 OPT_FAST=-O0",
        "--Mdir", str(object_dir), "--top-module", "noos_spi_rtl",
        "-CFLAGS", f"-std=c++20 -I{vendor}", "-LDFLAGS", link,
        *verilog, str(work / "noos_spi_sim.cpp"),
    ], work, env, work / "rtl.compile.log")
    sim_log = work / "simulate.log"
    command_to_log([str(object_dir / "Vnoos_spi_rtl")], work, env, sim_log)
    transcript = sim_log.read_text()
    if not all(marker in transcript for marker in MARKERS):
        raise RuntimeError(f"缺少定向结果，见 {sim_log}")
    version = subprocess.check_output([tool, "--version"], cwd=work, env=env, text=True).strip()
    print(f"baseline={BASELINE}\nadi_commit={ADI_COMMIT}\nrun={work}\ntool={version}")
    print(f"dsp_candidate={args.dsp_candidate_dir is not None}")
    for name in ("spi_slave.sv", "ad4080_regs.sv"):
        print(f"dsp_sha256 {sha256((work / RTL / name).read_bytes()).hexdigest()} {name}")
    for relative, expected in ADI_FILES.items():
        print(f"adi_sha256 {expected} {relative}")
    print(transcript, end="")


# =============================================================================
# 2. 本地复跑入口
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="复跑 ADI no-OS 真实配置 SPI 流程")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--adi-archive", required=True, type=Path)
    parser.add_argument("--dsp-candidate-dir", type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--verilator", default=os.environ.get("VERILATOR", "verilator"))
    try:
        run_harness(parser.parse_args())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"noos_harness 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
