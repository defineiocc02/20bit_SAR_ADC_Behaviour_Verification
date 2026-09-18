#!/usr/bin/env python3
r"""把仓库里的 RTL/TB/向量文件同步到远程 EDA 虚拟机，跑 VCS 仿真，再把结果取回来。

本机（Windows + Git Bash / PowerShell）→ 远程 RHEL 8.10 VM（VCS W-2024.09-SP1）。

它做的事，按顺序：

1. 解析「要同步哪些文件」（``--src`` / ``--tb`` / ``--filelist``，支持目录与 glob）；
2. 在远端建出 ``<remote-root>/<name>/`` 这**一个**工作目录（绝不碰同一台机器
   上别人的工程），把源文件按仓库相对路径镜像进去，并写入远端文件列表
   ``sim_files.f``；
3. 把本仓库的 ``sim/run_vcs.sh`` 一起传上去，远程执行它；
4. 把 ``vcs.log``（以及 ``--fetch`` 指定的产物，例如 dump 出来的 ``*.hex``）取回
   到 ``--local-out`` 目录，并原样透传远端退出码；
5. 若 ``--fetch`` 取回的文件超过 512 KB，给出告警 —— 仓库 pre-commit 有
   ``check-added-large-files --maxkb=512``，别把中间产物提交进去。

退出码
    0   仿真通过（无 $error / $fatal）
    1   本机侧错误（参数、找不到文件、找不到 ssh/scp ……）
    2   编译失败
    3   运行期致命错误（$fatal / UVM_FATAL / simv 非零退出）
    4   运行期非致命错误（$error / UVM_ERROR / 断言失败）
    5   仿真超时
    255 SSH 传输层失败（连不上、认证失败等）

    2..5 由远端 ``sim/run_vcs.sh`` 判定，本脚本原样透传。

典型用法::

    # 只同步指定文件，把日志与 dump 出来的 hex 取回 sim/artifacts/smoke/
    python tools/run_rtl_sim.py \\
        --top tb_smoke \\
        --src rtl/params/rtl_params.vh rtl/sar_ctrl.sv \\
        --tb  tb/tb_smoke.sv \\
        --incdir rtl/params \\
        --fetch '*.hex' \\
        --local-out sim/artifacts/smoke

    # 从干净状态重建远端工作目录（--fresh），只看编译不跑仿真（--compile-only）
    python tools/run_rtl_sim.py --top tb_smoke --src ... --fresh --compile-only

    # 不重新同步/编译，只把远端目录里的日志与产物取回来
    python tools/run_rtl_sim.py --top tb_smoke --fetch-only --fetch '*.hex'

传输实现：默认 scp。``--transfer auto`` 会探测本机有没有 rsync，有就用 rsync。
本机实测 **没有 rsync**（PortableGit 的 ``usr/bin`` 里只有 ssh/scp），所以
``auto`` 在本机等价于 scp；rsync 分支按 ``rsync -aR --files-from`` 的写法实现，
但**未在 Windows 本机验证过**（Linux/macOS 主机可用 ``--transfer rsync``）。
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_HOST = "yian@192.168.38.129"
DEFAULT_REMOTE_ROOT = "~/adc_rtl_sim"
SSH_BASE_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
# 与 .pre-commit-config.yaml 的 check-added-large-files --maxkb=512 对齐
LARGE_FILE_KB = 512

EXIT_LOCAL = 1
VERDICT_TEXT = {
    0: "PASS    仿真通过（无 $error / $fatal）",
    1: "FAIL    远端用法/环境错误",
    2: "FAIL    编译失败",
    3: "FAIL    运行期致命错误（$fatal / UVM_FATAL / simv 非零退出）",
    4: "FAIL    运行期非致命错误（$error / UVM_ERROR / 断言失败）",
    5: "FAIL    仿真超时",
    255: "ERROR   SSH 传输层失败",
}


class LocalError(Exception):
    """本机侧错误（参数、IO、外部命令缺失）。"""


# 远端 sshd 的版本比本机 OpenSSH 老，每次连接都会往 stderr 打一段 PQ 告警，
# 和仿真结果混在一起很难看。它不是错误，过滤掉，免得掩盖真正的报错。
SSH_NOISE = re.compile(
    r"post-quantum|store now, decrypt later|openssh\.com/pq|may need to be upgraded"
)


def clean_ssh_output(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not SSH_NOISE.search(ln))


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def to_msys_path(p: Path | str) -> str:
    """把 Windows 绝对路径转成 MSYS/Git-Bash 形式。

    必须转：``scp`` 用第一个冒号切 ``host:path``，``D:/x/y.sv`` 会被当成
    主机 ``D`` 上的 ``/x/y.sv``。``/d/x/y.sv`` 才安全。
    """
    s = str(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if m:
        return "/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return s.replace("\\", "/")


def rq(path: str) -> str:
    """远端路径的 shell 引用；``~/x`` 展开成 ``"$HOME"/x``（引号会挡住 ~）。"""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def which_or_die(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise LocalError(
            f"找不到 `{name}`。本脚本需要 Windows 上的 OpenSSH 客户端"
            "（Git for Windows 的 usr/bin 里有 ssh.exe / scp.exe）。"
        )
    return exe


class Runner:
    """统一封装 ssh / scp / rsync 调用，便于 --dry-run 与 --verbose。"""

    def __init__(
        self,
        host: str,
        *,
        transfer: str = "scp",
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        """记下目标主机与传输方式，并把 ssh 客户端解析成绝对路径。"""
        self.host = host
        self.transfer = transfer
        self.dry_run = dry_run
        self.verbose = verbose
        self.ssh_exe = which_or_die("ssh")

    # --- 底层 ---
    def _exec(self, argv: list[str], *, capture: bool) -> subprocess.CompletedProcess:
        shown = " ".join(shlex.quote(a) for a in argv)
        if self.dry_run:
            print(f"[dry-run] {shown}")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if self.verbose:
            print(f"[cmd] {shown}")
        return subprocess.run(
            argv, text=True, encoding="utf-8", errors="replace", capture_output=capture
        )

    def _ssh_argv(self, remote_cmd: str) -> list[str]:
        return [self.ssh_exe, *SSH_BASE_OPTS, self.host, remote_cmd]

    # --- ssh ---
    def ssh_cmd(self, remote_cmd: str) -> subprocess.CompletedProcess:
        """执行一条远端命令，返回 CompletedProcess（不抛异常，交由调用方判退出码）。"""
        return self._exec(self._ssh_argv(remote_cmd), capture=True)

    def ssh_text(self, remote_cmd: str) -> str:
        """执行远端命令并返回 stdout；非零退出则抛 LocalError。"""
        cp = self.ssh_cmd(remote_cmd)
        if cp.returncode != 0:
            raise LocalError(
                f"远端命令失败（exit={cp.returncode}）：{remote_cmd}\n"
                f"{(cp.stderr or '').strip()}"
            )
        return cp.stdout or ""

    # --- scp ---
    def scp_up(self, local_paths: list[Path | str], remote_dest: str) -> None:
        """把若干本地路径上传到同一个远端目录（Windows 路径先转成 MSYS 形式）。"""
        exe = which_or_die("scp")
        argv = [exe, *SSH_BASE_OPTS]
        argv += [p if isinstance(p, str) else to_msys_path(p) for p in local_paths]
        argv.append(remote_dest)
        cp = self._exec(argv, capture=True)
        if cp.returncode != 0:
            raise LocalError(f"scp 上传失败（exit={cp.returncode}）：\n{(cp.stderr or '').strip()}")

    def scp_down(self, remote_src: str, local_dest: Path) -> None:
        """把远端单个文件取回本地，自动建出本地父目录。"""
        local_dest.parent.mkdir(parents=True, exist_ok=True)
        exe = which_or_die("scp")
        argv = [exe, *SSH_BASE_OPTS, remote_src, to_msys_path(local_dest)]
        cp = self._exec(argv, capture=True)
        if cp.returncode != 0:
            raise LocalError(f"scp 下载失败（exit={cp.returncode}）：\n{(cp.stderr or '').strip()}")

    # --- rsync ---
    def rsync_up(self, local_root: Path, rel_paths: list[str], remote_dest: str) -> None:
        """Rsync -aR --files-from（相对路径得以保留）。未在 Windows 本机验证。"""
        exe = which_or_die("rsync")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".rsync-list", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("\n".join(rel_paths) + "\n")
            list_file = Path(fh.name)
        try:
            rsh = "ssh " + " ".join(SSH_BASE_OPTS)
            argv = [
                exe,
                "-azR",
                "-e",
                rsh,
                f"--files-from={to_msys_path(list_file)}",
                to_msys_path(local_root) + "/",
                f"{self.host}:{remote_dest}/",
            ]
            cp = self._exec(argv, capture=True)
            if cp.returncode != 0:
                raise LocalError(
                    f"rsync 上传失败（exit={cp.returncode}）：\n{(cp.stderr or '').strip()}"
                )
        finally:
            list_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 文件清单
# ---------------------------------------------------------------------------
def collect_local_files(args: argparse.Namespace) -> list[Path]:
    """把 --src / --tb / --filelist 汇总成去重后的绝对路径列表。"""
    raw: list[str] = []
    raw += list(args.src or [])
    raw += list(args.tb or [])

    for fl in args.filelist or []:
        p = Path(fl)
        if not p.is_file():
            raise LocalError(f"--filelist 指向的文件不存在：{fl}")
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.append(line)

    if not raw:
        raise LocalError("没有指定任何文件：请用 --src / --tb / --filelist")

    out: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        r = p.resolve()
        if r.is_file() and r not in seen:
            seen.add(r)
            out.append(r)

    for item in raw:
        cand = Path(item)
        cands = [cand] if cand.is_absolute() else [Path.cwd() / cand, REPO_ROOT / cand]
        hit = False
        for c in cands:
            if c.is_file():
                add(c)
                hit = True
                break
            if c.is_dir():
                for sub in sorted(x for x in c.rglob("*") if x.is_file()):
                    add(sub)
                hit = True
                break
        if not hit:
            raise LocalError(f"找不到文件或目录：{item}")
    if not out:
        raise LocalError("文件清单解析结果为空")
    return out


def remote_rel_path(local: Path, layout: str) -> Path:
    """决定文件在远端工作目录里的相对路径。"""
    if layout == "flat":
        return Path(local.name)
    try:
        return local.relative_to(REPO_ROOT)
    except ValueError:
        # 仓库之外的文件：平铺，避免把 ../.. 带到远端
        return Path(local.name)


def plan_mapping(files: list[Path], layout: str) -> dict[Path, str]:
    mapping: dict[Path, str] = {}
    used: dict[str, Path] = {}
    for f in files:
        rel = remote_rel_path(f, layout).as_posix()
        if rel in used and used[rel] != f:
            raise LocalError(
                f"远端相对路径冲突：{rel}\n  {used[rel]}\n  {f}\n"
                "（--layout flat 会丢掉目录层级，重名时请改用 --layout preserve）"
            )
        used[rel] = f
        mapping[f] = rel
    return mapping


# ---------------------------------------------------------------------------
# 远端工作目录
# ---------------------------------------------------------------------------
def resolve_remote_root(r: Runner, root: str) -> str:
    """把 ``~/adc_rtl_sim`` 之类解析成远端绝对路径（引号会挡住 ~ 展开）。"""
    if r.dry_run:
        return root
    cmd = f"mkdir -p {rq(root)} && cd {rq(root)} && pwd"
    out = [ln.strip() for ln in r.ssh_text(cmd).splitlines() if ln.strip()]
    if not out:
        raise LocalError(f"无法解析远端根目录：{root}")
    return out[-1]


def prepare_remote_dir(r: Runner, workdir: str, *, fresh: bool) -> None:
    if fresh:
        # 只删自己这一层工作目录，绝不 rm -rf 上级
        r.ssh_text(f"rm -rf -- {rq(workdir)}")
    r.ssh_text(f"mkdir -p -- {rq(workdir)}")


def upload_sources(r: Runner, files: list[Path], workdir: str, layout: str) -> dict[Path, str]:
    """上传源文件，返回 {本地路径: 远端相对路径}。"""
    mapping = plan_mapping(files, layout)

    # 一次性把所有需要的远端目录建出来
    dirs = sorted({f"{workdir}/{Path(rel).parent.as_posix()}" for rel in mapping.values()})
    dirs = [d for d in dirs if not d.endswith("/.")]
    r.ssh_text("mkdir -p -- " + " ".join(rq(d) for d in dirs) + f" {rq(workdir)}")

    rels = list(mapping.values())
    under_repo = all(f.is_relative_to(REPO_ROOT) for f in mapping)  # py3.9+
    if r.transfer == "rsync" and layout == "preserve" and under_repo:
        r.rsync_up(REPO_ROOT, rels, workdir)
    else:
        if r.transfer == "rsync":
            print("[warn] rsync 需要 --layout preserve 且文件都在仓库内，退回 scp")
        by_dir: dict[str, list[Path]] = {}
        for f, rel in mapping.items():
            by_dir.setdefault(Path(rel).parent.as_posix(), []).append(f)
        for rel_dir, group in sorted(by_dir.items()):
            dest = workdir if rel_dir == "." else f"{workdir}/{rel_dir}"
            r.scp_up(list(group), f"{r.host}:{dest}/")

    # 远端文件列表（run_vcs.sh 用 -f 读它）；写成远端绝对路径最省心
    # 只有 HDL 源进 filelist。`--src` 里可能夹带**数据**（黄金向量、期望码流、
    # 寄存器镜像），它们必须同步到远端但**绝不能**交给 VCS 当源解析 ——
    # VCS 对未知扩展名不做区分，会把 hex 文本当 Verilog 报语法错（实测踩过）。
    hdl_suffixes = {".sv", ".v", ".svh", ".vh"}
    with tempfile.NamedTemporaryFile("w", suffix=".f", delete=False, encoding="utf-8") as fh:
        for rel in sorted(mapping.values()):
            if Path(rel).suffix.lower() in hdl_suffixes:
                fh.write(f"{workdir}/{rel}\n")
        local_list = Path(fh.name)
    try:
        r.scp_up([local_list], f"{r.host}:{workdir}/sim_files.f")
    finally:
        local_list.unlink(missing_ok=True)

    return mapping


def upload_driver(r: Runner, workdir: str) -> None:
    script = REPO_ROOT / "sim" / "run_vcs.sh"
    if not script.is_file():
        raise LocalError(f"缺少 {script}")
    r.scp_up([script], f"{r.host}:{workdir}/run_vcs.sh")


# ---------------------------------------------------------------------------
# 远端执行
# ---------------------------------------------------------------------------
def build_remote_command(workdir: str, args: argparse.Namespace, remote_incdirs: list[str]) -> str:
    cmd: list[str] = [
        "bash",
        "./run_vcs.sh",
        "-top",
        args.top,
        "-f",
        "sim_files.f",
        "-w",
        workdir,
        "-l",
        f"{workdir}/vcs.log",
        "-o",
        "simv",
    ]
    if args.timescale:
        cmd += ["-timescale", args.timescale]
    for d in remote_incdirs:
        cmd += ["-incdir", d]
    for d in args.define or []:
        cmd += ["-define", d]
    for a in args.vcs_arg or []:
        cmd += ["-vcs-arg", a]
    for a in args.sim_arg or []:
        cmd += ["-sim-arg", a]
    for a in args.plusarg or []:
        cmd.append(a if a.startswith("+") else "+" + a)
    if args.cm:
        cmd += ["-cm", args.cm]
        if args.cm_dir:
            cmd += ["-cm-dir", args.cm_dir]
    if args.timeout:
        cmd += ["-timeout", str(args.timeout)]
    if args.compile_only:
        cmd.append("-no-run")

    inner = " ".join(shlex.quote(c) for c in cmd)
    return f"cd {rq(workdir)} && {inner}"


# ---------------------------------------------------------------------------
# 取回产物
# ---------------------------------------------------------------------------
def remote_glob(r: Runner, workdir: str, patterns: list[str]) -> list[str]:
    """在远端展开 glob，只回传**普通文件**，路径相对 workdir。"""
    if not patterns:
        return []
    pats = " ".join(rq(p) for p in patterns)
    # for f in $p 里的 $p 故意不加引号，才能让远端 shell 展开通配符
    inner = f'for p in {pats}; do for f in $p; do [ -f "$f" ] && echo "$f"; done; done'
    out = r.ssh_text(f"cd {rq(workdir)} && {inner} || true")
    found: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        found.append(line[2:] if line.startswith("./") else line)
    return sorted(set(found))


def fetch_outputs(
    r: Runner, workdir: str, patterns: list[str], local_out: Path
) -> list[tuple[str, Path, int]]:
    fetched: list[tuple[str, Path, int]] = []
    for rel in remote_glob(r, workdir, patterns):
        dest = local_out / rel
        r.scp_down(f"{r.host}:{workdir}/{rel}", dest)
        size = dest.stat().st_size if dest.exists() else 0
        fetched.append((rel, dest, size))
    return fetched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_rtl_sim.py",
        description="同步指定 RTL/TB/向量文件到远程 EDA VM，跑 VCS 仿真并取回日志与产物。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 通过 / 1 本机错误 / 2 编译失败 / 3 $fatal / "
            "4 $error / 5 超时 / 255 SSH 失败\n"
            "远端只会在 <remote-root>/<name>/ 下活动，不会碰同机上别人的工程。"
        ),
    )
    p.add_argument("--top", required=True, help="顶层模块名（传给 VCS -top）")
    p.add_argument("--src", nargs="+", action="extend", help="要同步的源文件/目录（可多个）")
    p.add_argument("--tb", nargs="+", action="extend", help="要同步的 testbench 文件（可多个）")
    p.add_argument(
        "--filelist", nargs="+", action="extend", help="额外的文件清单（每行一个，# 注释）"
    )
    p.add_argument("--incdir", nargs="+", action="extend", help="include 目录（仓库相对路径）")
    p.add_argument("--define", nargs="+", action="extend", help="宏定义 X 或 X=V")
    p.add_argument("--plusarg", nargs="+", action="extend", help="+ 前缀运行参数")
    p.add_argument("--vcs-arg", nargs="+", action="extend", help="额外 vcs 参数")
    p.add_argument("--sim-arg", nargs="+", action="extend", help="额外 simv 参数")

    p.add_argument("--host", default=DEFAULT_HOST, help=f"SSH 目标，默认 {DEFAULT_HOST}")
    p.add_argument(
        "--remote-root", default=DEFAULT_REMOTE_ROOT, help=f"远端根目录，默认 {DEFAULT_REMOTE_ROOT}"
    )
    p.add_argument("--name", help="远端工作目录名，默认取 --top")
    p.add_argument(
        "--layout",
        choices=["preserve", "flat"],
        default="preserve",
        help="远端目录结构：preserve=按仓库相对路径（默认），flat=全部平铺",
    )
    p.add_argument(
        "--transfer",
        choices=["auto", "scp", "rsync"],
        default="auto",
        help="传输方式，默认 auto（本机无 rsync 时落到 scp）",
    )

    p.add_argument("--local-out", help="日志与产物的本地落盘目录，默认 sim/artifacts/<name>")
    p.add_argument(
        "--fetch",
        nargs="+",
        action="extend",
        help="仿真结束后额外取回的远端文件（glob，相对工作目录），如 '*.hex'",
    )
    p.add_argument("--fresh", action="store_true", help="先删掉远端工作目录再重建")
    p.add_argument("--compile-only", action="store_true", help="只编译，不运行仿真")
    p.add_argument("--fetch-only", action="store_true", help="跳过同步与仿真，只取回产物")
    p.add_argument("--cm", help="覆盖率类型，如 line+cond+fsm+tgl+branch")
    p.add_argument("--cm-dir", help="覆盖率库目录（远端绝对路径）")
    p.add_argument("--timescale", default="1ns/1ps", help="时间精度，默认 1ns/1ps")
    p.add_argument("--timeout", type=int, default=0, help="仿真超时秒数，0=不限")
    p.add_argument(
        "--max-log-lines", type=int, default=40, help="失败时打印取回日志的尾部行数，默认 40"
    )
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    p.add_argument("-v", "--verbose", action="store_true", help="打印每条 ssh/scp 命令")
    return p


def resolve_transfer(requested: str) -> str:
    if requested == "scp":
        return "scp"
    have_rsync = shutil.which("rsync") is not None
    if requested == "rsync":
        if not have_rsync:
            raise LocalError("--transfer rsync 但本机没有 rsync（Windows 上通常没有）")
        return "rsync"
    return "rsync" if have_rsync else "scp"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    name = args.name or args.top
    local_out = Path(args.local_out) if args.local_out else REPO_ROOT / "sim" / "artifacts" / name

    try:
        transfer = resolve_transfer(args.transfer)
        r = Runner(args.host, transfer=transfer, dry_run=args.dry_run, verbose=args.verbose)
        remote_root = resolve_remote_root(r, args.remote_root)
        workdir = f"{remote_root}/{name}"

        print(f"远端工作目录 : {args.host}:{workdir}")
        print(f"本地取回目录 : {local_out}")
        print(
            f"传输方式     : {transfer}"
            + (
                "   （本机无 rsync，auto 落到 scp）"
                if args.transfer == "auto" and transfer == "scp"
                else ""
            )
        )

        exit_code = 0
        remote_log = ""

        if not args.fetch_only:
            if transfer == "rsync" and args.layout != "preserve":
                print("[warn] rsync 只支持 --layout preserve，退回 scp")
                r.transfer = "scp"
            files = collect_local_files(args)
            print(
                f"上传文件     : {len(files)} 个（layout={args.layout}，" f"transfer={r.transfer}）"
            )
            prepare_remote_dir(r, workdir, fresh=args.fresh)
            upload_driver(r, workdir)
            mapping = upload_sources(r, files, workdir, args.layout)
            if args.verbose:
                for local, rel in sorted(mapping.items(), key=lambda kv: kv[1]):
                    print(f"    {rel}  <-  {local}")

            remote_incdirs = [
                d if d.startswith("/") else f"{workdir}/{d.strip('/')}" for d in (args.incdir or [])
            ]
            remote_cmd = build_remote_command(workdir, args, remote_incdirs)
            print(f"远程执行     : bash run_vcs.sh（顶层 {args.top}）")
            cp = r.ssh_cmd(remote_cmd)
            exit_code = cp.returncode
            remote_log = clean_ssh_output((cp.stdout or "") + (cp.stderr or ""))

        # ---- 取回日志与产物 ----
        local_out.mkdir(parents=True, exist_ok=True)
        patterns = ["vcs.log", *list(args.fetch or [])]
        try:
            fetched = fetch_outputs(r, workdir, patterns, local_out)
        except LocalError as exc:
            fetched = []
            print(f"[warn] 取回产物失败：{exc}", file=sys.stderr)

        if fetched:
            print("取回文件     :")
            for rel, dest, size in sorted(fetched):
                flag = "   <<< 超过 512KB，别提交进仓库！" if size > LARGE_FILE_KB * 1024 else ""
                print(f"    {size:>9d} B  {rel}  ->  {dest}{flag}")
        elif not args.dry_run:
            print("[warn] 没有取回任何文件", file=sys.stderr)

        # ---- 结论 ----
        print("-" * 68)
        if remote_log.strip():
            print(remote_log.rstrip())
            print("-" * 68)
        print(f"退出码 {exit_code} —— {VERDICT_TEXT.get(exit_code, '未知状态')}")

        log_path = local_out / "vcs.log"
        if log_path.is_file() and exit_code != 0:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = lines[-max(1, args.max_log_lines) :]
            print(f"--- {log_path} 尾部 {len(tail)} 行 ---")
            print("\n".join(tail))
        return exit_code

    except LocalError as exc:
        print(f"run_rtl_sim.py: ERROR: {exc}", file=sys.stderr)
        return EXIT_LOCAL
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
