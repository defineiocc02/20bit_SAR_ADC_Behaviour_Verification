#!/usr/bin/env python3
"""同步指定 SystemVerilog 源到 EDA 虚拟机，远程执行 Design Compiler 并取回报告。

本机入口适用于 Windows Git Bash 或 Linux，封装 ssh／scp、仓库相对路径及
--files 通配符处理。目录递归展开 .sv/.v；.vh/.svh 由 --incdirs 提供。
默认 smoke 连接五个子模块与 top_smoke；--params 通过 elaborate -parameters
传递 HDL 参数；--dry-run 仅同步，不执行综合。结果保存到 synth/artifacts/<name>/。

远端退出状态原样传回：0 为其成功及所检时序通过，1 流程失败，2 参数错误，
3 时序未收敛，4 状态不可解析；本机路径、传输或目录错误返回 90。
该状态只覆盖所选工具、库及约束，不是 ASIC 或物理签核结论。

调用环境须已有 ssh／scp。原 Windows 开发环境使用 Git Bash 工具路径及
C:/Users/Administrator/miniconda3/python.exe；这些是运行环境记录，不是此脚本
安装或配置工具的承诺。
"""

from __future__ import annotations

import argparse
import glob
import os
import shlex
import subprocess
import sys
from pathlib import Path

# --- 默认配置（可用环境变量覆盖，不要在这里塞机器专属的临时路径） ----------
DEFAULT_HOST = os.environ.get("ADC_SYNTH_HOST", "yian@192.168.38.129")
DEFAULT_ROOT = os.environ.get("ADC_SYNTH_REMOTE_ROOT", "adc_rtl_synth")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]

REPO_ROOT = Path(__file__).resolve().parent.parent

# smoke 默认用到的文件（rtl/core 5 个模块 + 顶层 wrapper）
SMOKE_FILES = [
    "synth/smoke/top_smoke.sv",
    "rtl/core/dem_state_gen.sv",
    "rtl/core/dem_addr_gen.sv",
    "rtl/core/swap_decode.sv",
    "rtl/core/dither_gen.sv",
    "rtl/core/unit_therm.sv",
    "rtl/params/rtl_params.vh",
]
SMOKE_INCDIRS = ["rtl/params"]

# 目录模式展开时接受的源文件后缀（**.vh/.svh 是 `include 目标，走 --incdirs**）
SOURCE_EXTS = {".sv", ".v"}

# 取回本地的报告文件
ARTIFACTS = [
    "area.rpt",
    "timing.rpt",
    "power.rpt",
    "qor.rpt",
    "check_design.rpt",
    "status.txt",
    "phase.log",
    "dc.log",
    "elaborate.log",
    "run_dc.snapshot.tcl",  # 产出这份报告的 run_dc.tcl 快照（可追溯）
]

LOCAL_ERR = 90


def log(msg: str) -> None:
    """打印带 [run_synth] 前缀的进度或错误，立即刷新输出。"""
    print(f"[run_synth] {msg}", flush=True)


def run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """执行本地命令并按 capture 选择捕获或继承终端输出。

    check=True 且返回码非零时，以 LOCAL_ERR 退出；capture=False 用于远端长任务，
    使输出直接显示而不等待捕获完成。
    """
    log("$ " + " ".join(shlex.quote(c) for c in cmd))
    try:
        cp = subprocess.run(cmd, check=False, capture_output=capture, text=True)
    except FileNotFoundError as exc:
        log(f"ERROR: command not found: {cmd[0]} ({exc})")
        sys.exit(LOCAL_ERR)
    if capture and cp.stdout:
        sys.stdout.write(cp.stdout)
    if capture and cp.stderr:
        sys.stderr.write(cp.stderr)
    if check and cp.returncode != 0:
        log(f"ERROR: command failed with rc={cp.returncode}: {cmd[0]}")
        sys.exit(LOCAL_ERR)
    return cp


def expand_files(patterns: list[str]) -> list[Path]:
    """将 glob 或目录展开为去重后的稳定文件序列。

    目录递归只收 .sv/.v；.vh/.svh 是包含目标，经 --incdirs 提供，不交给 analyze。
    没有匹配或最终无源文件时以 LOCAL_ERR 退出，不静默用不完整文件集继续。
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for pat in patterns:
        p = REPO_ROOT / pat
        matches = sorted(glob.glob(str(p), recursive=True))
        if not matches:
            log(f"ERROR: pattern matched no file: {pat}")
            sys.exit(LOCAL_ERR)
        for m in matches:
            mp = Path(m).resolve()
            if mp.is_dir():
                sub = sorted(q for q in mp.rglob("*") if q.suffix in SOURCE_EXTS and q.is_file())
                if not sub:
                    log(
                        f"NOTE: directory pattern {pat!r} has no .sv/.v source "
                        f"(include-only dir? use --incdirs for that)"
                    )
                for q in sub:
                    q = q.resolve()
                    if q in seen:
                        continue
                    seen.add(q)
                    out.append(q)
                continue
            if mp in seen:
                continue
            seen.add(mp)
            out.append(mp)
    if not out:
        log("ERROR: no source files after glob expansion")
        sys.exit(LOCAL_ERR)
    return out


def remote_rel(path: Path) -> str:
    """将本机绝对路径转换为仓库相对 POSIX 路径，供远端复刻结构。"""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        log(f"ERROR: file is outside the repo, refusing to guess a remote path: {path}")
        sys.exit(LOCAL_ERR)
    return rel.as_posix()


def main() -> int:
    """解析参数、同步源文件、执行远端 dc_shell 并取回报告，返回远端状态。"""
    ap = argparse.ArgumentParser(description="Sync RTL to the EDA VM and run Design Compiler.")
    ap.add_argument(
        "--name", required=True, help="本次运行的短名，远端目录名 + 本地 artifacts 子目录名"
    )
    ap.add_argument("--top", help="顶层模块名（--preflight 时可省）")
    ap.add_argument("--clk-period", type=float, help="时钟周期 (ns)（--preflight 时可省）")
    ap.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="源文件列表/glob（相对仓库根）。不给则用 smoke 默认集合",
    )
    ap.add_argument(
        "--incdirs",
        nargs="*",
        default=None,
        help="`include 搜索目录（相对仓库根）。不给则用 smoke 默认",
    )
    ap.add_argument("--defines", nargs="*", default=None, help="+define+ 宏")
    ap.add_argument(
        "--params",
        default=None,
        help='顶层模块 HDL 参数覆盖，如 "P_STAGES=4"（转发给 elaborate -parameters）',
    )
    ap.add_argument(
        "--compile-mode",
        default=None,
        choices=["ultra", "compile"],
        help="ultra=compile_ultra（默认）/ compile=非 ultra（快但结果差，需连 COMPILE_MODE 一起引用）",
    )
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="只做参数一致性检查 + check_design 后退出（不起 compile），用于快速红绿",
    )
    ap.add_argument(
        "--no-design-rule",
        action="store_true",
        help="关掉 compile 的设计规则修复（-no_design_rule）；只配 --compile-mode compile",
    )
    ap.add_argument("--clk-name", default="clk")
    ap.add_argument("--corner", default="tt0p9v25c")
    ap.add_argument("--drive-cell", default=None, help="输入驱动单元（默认库侧决定）")
    ap.add_argument("--load-pf", default=None, help="输出负载 pF")
    ap.add_argument("--max-trans", default=None, help="覆盖库 default_max_transition")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--remote-root", default=DEFAULT_ROOT)
    ap.add_argument(
        "--dc-timeout",
        default=None,
        help="远端 dc_shell 超时秒数（转发为 DC_TIMEOUT 环境变量；默认走 run_synth.sh 的 3600）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只同步、不跑 dc_shell")
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="只在远端做库/license 体检（约 49 s），不综合；不需要 --top/--clk-period",
    )
    args = ap.parse_args()

    if not args.name.replace("-", "").replace("_", "").isalnum():
        log(f"ERROR: --name must be alnum/-/_ , got {args.name!r}")
        return LOCAL_ERR

    if not args.preflight:
        missing = [n for n in ("top", "clk_period") if getattr(args, n) is None]
        if missing:
            log(
                "ERROR: missing required argument(s): "
                + ", ".join("--" + m.replace("_", "-") for m in missing)
            )
            return LOCAL_ERR

    # preflight 只关心库，不需要任何源文件
    if args.preflight:
        patterns, incdirs = [], []
    else:
        patterns = args.files if args.files else SMOKE_FILES
        incdirs = args.incdirs if args.incdirs else (SMOKE_INCDIRS if not args.files else [])

    src_files = expand_files(patterns) if patterns else []

    # include 目录也同步过去（rtl_params.vh 就在里面）
    inc_dirs_local = [(REPO_ROOT / d).resolve() for d in incdirs]
    for d in inc_dirs_local:
        if not d.is_dir():
            log(f"ERROR: include dir does not exist: {d}")
            return LOCAL_ERR

    # 远端路径一律相对 $HOME，并且**每条 ssh 命令都先 cd "$HOME"**。
    # bug 记录：早先 mkdir 的目录集合里混进了 `rtl/core` 这种"裸相对路径"，
    # 而 ssh 默认 cwd 是 $HOME，于是 `mkdir -p rtl/core` 在 $HOME 下建了空目录
    # —— 越出了"只在 ~/adc_rtl_synth/ 下活动"的硬约束。下面把它钉死：
    # 所有远端路径都必须以 remote_base 开头，且 mkdir/scp/run 全部在 $HOME 下执行。
    remote_base = f"{args.remote_root}/{args.name}"
    if not remote_base.startswith(f"{args.remote_root}/") or ".." in remote_base:
        log(f"ERROR: refusing to use unsafe remote path: {remote_base!r}")
        return LOCAL_ERR

    remote_ssh = f"{args.host}"
    scp = ["scp", *SSH_OPTS]
    ssh = ["ssh", *SSH_OPTS, remote_ssh]

    def ssh_sh(body: str) -> list[str]:
        """在远端 HOME 下执行 body；remote_base 相对于该 HOME。"""
        return [*ssh, "bash", "-c", f'set -euo pipefail; cd "$HOME"; {body}']

    # 0) 远端准备目录
    run(
        ssh_sh(
            f"rm -rf '{remote_base}/src' '{remote_base}/inc'; "
            f"mkdir -p '{remote_base}/src' '{remote_base}/inc' '{remote_base}/out'"
        )
    )

    # 1) 同步源文件，复刻仓库内相对路径
    #    先建目录（**只建 remote_base 底下的**），再逐个 scp。
    src_rel_dirs = sorted({os.path.dirname(remote_rel(f)) for f in src_files})
    inc_rel_dirs = sorted({remote_rel(d) for d in inc_dirs_local})
    remote_dirs = (
        [f"{remote_base}/src"]
        + [f"{remote_base}/src/{d}" for d in src_rel_dirs]
        + [f"{remote_base}/inc"]
        + [f"{remote_base}/inc/{d}" for d in inc_rel_dirs]
    )
    assert all(
        d.startswith(remote_base + "/") or d == remote_base for d in remote_dirs
    ), remote_dirs
    mkdir_cmd = 'set -euo pipefail; cd "$HOME"; mkdir -p ' + " ".join(
        f"'{d}'" for d in sorted(set(remote_dirs))
    )
    run([*ssh, "bash", "-c", mkdir_cmd])

    for f in src_files:
        rel = remote_rel(f)
        dst = f"{remote_base}/src/{os.path.dirname(rel)}/"
        run([*scp, str(f), f"{remote_ssh}:{dst}"])

    for d in inc_dirs_local:
        rel = remote_rel(d)
        run([*scp, "-r", str(d) + "/.", f"{remote_ssh}:{remote_base}/inc/{rel}/"])

    # 2) 同步脚本本体（永远用仓库里当前这一份，避免远端残留旧脚本）
    for script in ("run_dc.tcl", "run_synth.sh"):
        run([*scp, str(Path(__file__).parent / script), f"{remote_ssh}:{remote_base}/"])

    # 3) 拼远端命令行
    # ⚠️ 这里必须用 **相对 remote_base 的** 路径（src/... 、inc/...），
    #    不能用 remote_base/... 那种"相对 $HOME"的路径。
    #    原因：run_synth.sh 会 `cd "$HOME/{remote_base}"` 再调 dc_shell，
    #    而 run_dc.tcl 里的 `file exists $f` 是相对 dc_shell 的 cwd 判的。
    #    踩过的坑：早先传了 "adc_rtl_synth/<name>/src/..."，dc_shell 找不到文件，
    #    直接以 exit 1 结束（症状是"source file not found"，而不是路径拼错）。
    #    同一个坑在 --out-dir 上也踩过一次（写成 <name>/out 导致 $HOME 下多套一层目录）。
    remote_src_files = " ".join(f"src/{remote_rel(f)}" for f in src_files)
    remote_inc = " ".join(f"inc/{remote_rel(d)}" for d in inc_dirs_local)

    rs_args: list[str] = []
    if args.preflight:
        rs_args += ["--preflight"]
    else:
        rs_args += [
            "--top",
            args.top,
            "--clk-period",
            str(args.clk_period),
            "--files",
            remote_src_files,
        ]
        if remote_inc:
            rs_args += ["--incdirs", remote_inc]
    rs_args += ["--out-dir", "out", "--clk-name", args.clk_name, "--corner", args.corner]
    if args.params:
        rs_args += ["--params", args.params]
    if args.compile_mode:
        rs_args += ["--compile-mode", args.compile_mode]
    if args.check_only:
        rs_args += ["--check-only"]
    if args.no_design_rule:
        rs_args += ["--no-design-rule"]
    if args.drive_cell:
        rs_args += ["--drive-cell", args.drive_cell]
    if args.load_pf:
        rs_args += ["--load", args.load_pf]
    if args.max_trans:
        rs_args += ["--max-trans", args.max_trans]

    remote_cmd = " ".join(shlex.quote(a) for a in ["./run_synth.sh", *rs_args])
    # DC_TIMEOUT 以"命令前缀赋值"的方式给到远端 run_synth.sh（ssh 不会转发本机环境变量）。
    if args.dc_timeout:
        remote_cmd = f"DC_TIMEOUT={shlex.quote(str(args.dc_timeout))} {remote_cmd}"
    parts = [
        "bash",
        "-c",
        f'set -euo pipefail; cd "$HOME/{remote_base}"; chmod +x run_synth.sh; {remote_cmd}',
    ]

    if args.dry_run:
        log("dry-run: skipping dc_shell")
        remote_rc = 0
    else:
        rc = subprocess.run(ssh + parts, check=False)
        remote_rc = rc.returncode
        log(f"remote run_synth.sh rc = {remote_rc}")

    # 4) 取回报告（即使失败也取，失败时的 dc.log 才是最有用的东西）
    local_out = Path(__file__).parent / "artifacts" / args.name
    local_out.mkdir(parents=True, exist_ok=True)
    got, missed = [], []
    for art in ARTIFACTS:
        cp = subprocess.run(
            [*scp, f"{remote_ssh}:{remote_base}/out/{art}", str(local_out)],
            check=False,
            capture_output=True,
            text=True,
        )
        (got if cp.returncode == 0 else missed).append(art)
    log(
        f"retrieved {len(got)}/{len(ARTIFACTS)} artifacts into {local_out}"
        + (f"; missing: {', '.join(missed)}" if missed else "")
    )

    # 5) 打印摘要
    status_file = local_out / "status.txt"
    if status_file.is_file():
        log(f"status.txt @ {status_file}:")
        print(status_file.read_text(encoding="utf-8", errors="replace"), flush=True)
        area_rpt = local_out / "area.rpt"
        if area_rpt.is_file():
            for line in area_rpt.read_text(encoding="utf-8", errors="replace").splitlines():
                if "Total cell area" in line or "Total area" in line:
                    print("  " + line.strip(), flush=True)
    else:
        log(f"WARNING: no status.txt retrieved; see {local_out / 'dc.log'}")

    return remote_rc


if __name__ == "__main__":
    sys.exit(main())
