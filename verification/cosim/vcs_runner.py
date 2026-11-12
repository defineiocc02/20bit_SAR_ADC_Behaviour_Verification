"""Run VCS on the remote Linux host and bring the raw results back.

Why remote: this Mac has no simulator. The toolchain lives on `vm-meow`
(VCS W-2024.09-SP1). The runner keeps the remote side dumb — it copies a
directory, runs one command, copies results out — so a failure on the host is
never hidden behind local cleverness.

Everything the runner needs is derived from files in the repo; nothing is
generated from memory. If `vcs` or its licence is missing the run returns
status ``NOT_RUN`` with the host's own error text, never a fake pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "verification" / "build"
HOST = os.environ.get("VERIF_HOST", "vm-meow")
VCS_ENV = (
    "export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1; "
    "export PATH=$VCS_HOME/bin:$PATH; "
    "export SNPSLMD_LICENSE_FILE=27080@localhost; "
)


@dataclass
class SimResult:
    """Outcome of one simulator invocation, including the host's own log."""

    status: str            # "PASS_RUN" | "COMPILE_ERROR" | "RUN_ERROR" | "NOT_RUN"
    module: str
    log: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    elapsed_s: float = 0.0


def _ssh(command: str, *, timeout: int = 600, stdin_bytes: bytes | None = None,
         attempts: int = 3) -> subprocess.CompletedProcess:
    """Run a command on the verification host, retrying transient link failures.

    The jump host occasionally drops the banner exchange; a single failure is
    not evidence about the design. Retries are bounded so a dead host still
    surfaces as an error instead of hanging.
    """
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST, command]
    last: subprocess.CompletedProcess | None = None
    for attempt in range(attempts):
        try:
            last = subprocess.run(argv, input=stdin_bytes, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if attempt == attempts - 1:
                raise
            continue
        text = (last.stderr or b"").decode("utf-8", "replace").lower()
        transient = "timed out during banner" in text or "connection timed out" in text
        if last.returncode == 0 and not transient:
            return last
        if attempt == attempts - 1:
            return last
    assert last is not None
    return last


def check_host() -> dict:
    """Probe the host for vcs and the licence server. Never raises."""
    probe = VCS_ENV + "command -v vcs && vcs -ID 2>&1 | head -3"
    try:
        result = _ssh(probe, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as error:
        return {"available": False, "detail": f"ssh failed: {error}"}
    text = (result.stdout + result.stderr).decode("utf-8", "replace")
    available = result.returncode == 0 and "vcs" in text.lower()
    return {"available": available, "detail": text.strip()[:600], "host": HOST}


def stage(tmp_name: str, *, module: str, tb_files: list[str], extra_files: list[str]) -> Path:
    """Assemble the exact file set that will be compiled, and hash it."""
    # Unique per invocation: never delete a previous stage, so a host-side
    # delete guard can never take the campaign down mid-run.
    stamp = f"{os.getpid()}_{int(time.time() * 1000) % 10**9}"
    stage_dir = BUILD / f"{tmp_name}_{stamp}"
    stage_dir.mkdir(parents=True)

    vectors = ROOT / "verification" / "vectors" / "generated" / module
    if not vectors.is_dir():
        raise FileNotFoundError(
            f"no vectors for {module}; run `python -m verification.vectors.generate` first"
        )
    shutil.copytree(vectors, stage_dir / "vectors")
    shutil.copy2(ROOT / "verification" / "contracts" / "rtl_params.vh", stage_dir / "rtl_params.vh")

    for name in tb_files:
        shutil.copy2(ROOT / name, stage_dir / Path(name).name)
    for name in extra_files:
        shutil.copy2(ROOT / name, stage_dir / Path(name).name)

    listing = sorted(p.name for p in stage_dir.iterdir() if p.is_file())
    digest = hashlib.sha256()
    for name in listing:
        digest.update(name.encode())
        digest.update((stage_dir / name).read_bytes())
    (stage_dir / "STAGE_MANIFEST.json").write_text(
        json.dumps({"files": listing, "sha256": digest.hexdigest()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return stage_dir


def run_on_host(stage_dir: Path, *, top: str, plusargs: str = "", timeout: int = 900,
                filelist: list[str] | None = None) -> SimResult:
    """Copy the staged tree to the host, compile, run, and fetch the dumps."""
    remote = f"/tmp/verif_{stage_dir.name}"
    tar = subprocess.run(["tar", "czf", "-", "-C", str(stage_dir.parent), stage_dir.name],
                         capture_output=True, check=True).stdout
    try:
        upload = _ssh(f"rm -rf {remote} && mkdir -p {remote} && tar xzf - -C {remote} --strip-components=1",
                      timeout=300, stdin_bytes=tar)
    except (subprocess.TimeoutExpired, OSError) as error:
        return SimResult("NOT_RUN", top, f"upload failed: {error}")
    if upload.returncode != 0:
        return SimResult("NOT_RUN", top, upload.stderr.decode("utf-8", "replace"))

    sources = " ".join(shlex.quote(name) for name in (
        filelist if filelist is not None else sorted(p.name for p in stage_dir.glob("*.sv"))
    ))
    compile_cmd = (
        f"{VCS_ENV} cd {remote} && "
        f"vcs -full64 -sverilog -timescale=1ns/1ps +incdir+. -top {top} {sources} -o simv 2>&1"
    )
    try:
        compiled = _ssh(compile_cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return SimResult("RUN_ERROR", top, "compile timed out")

    log = (compiled.stdout + compiled.stderr).decode("utf-8", "replace")
    if "Error-" in log or compiled.returncode != 0:
        return SimResult("COMPILE_ERROR", top, log[-8000:])

    run_cmd = f"{VCS_ENV} cd {remote} && ./simv {plusargs} 2>&1"
    try:
        executed = _ssh(run_cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return SimResult("RUN_ERROR", top, log + "\nrun timed out")
    log += "\n--- run ---\n" + (executed.stdout + executed.stderr).decode("utf-8", "replace")

    # Fetch every .out dump the testbench produced.
    outputs: dict[str, str] = {}
    listing = _ssh(f"ls {remote}/*.out 2>/dev/null || true", timeout=60)
    for name in listing.stdout.decode().split():
        fetched = _ssh(f"cat {name}", timeout=300)
        outputs[Path(name).name] = fetched.stdout.decode("utf-8", "replace")
    if not outputs or all(not text.strip() for text in outputs.values()):
        return SimResult("RUN_ERROR", top, log[-8000:] + "\n(no dump content produced)")
    return SimResult("PASS_RUN", top, log, outputs)


def cleanup() -> None:
    """Remove local staging trees. Remote trees are removed by the next upload."""
    if BUILD.exists():
        shutil.rmtree(BUILD)


if __name__ == "__main__":
    print(json.dumps(check_host(), indent=2))
    sys.exit(0)
