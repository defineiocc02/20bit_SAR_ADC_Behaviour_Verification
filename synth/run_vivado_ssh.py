#!/usr/bin/env python3
"""Upload only the RTL snapshot and run the Vivado OOC screen on the EDA host.

Requires an explicit installed FPGA part. Outputs are isolated for every run.
Exit 0: synthesis and setup screen pass; 3: synthesis completed, negative slack;
other nonzero: flow failed. Neither status is ASIC PPA or implementation signoff.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import shlex
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
POWERSHELL_PREAMBLE = """$ProgressPreference = 'SilentlyContinue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
"""


def powershell_command(script: str) -> str:
    """Encode the entire script so the Windows SSH default shell cannot reparse it."""
    encoded = base64.b64encode((POWERSHELL_PREAMBLE + script).encode("utf-16le")).decode("ascii")
    return (
        "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}"
    )


def ps_literal(value: str) -> str:
    """Quote one literal value inside the encoded PowerShell script."""
    return "'" + value.replace("'", "''") + "'"


def windows_launch_script(remote: str, batch: str, timeout: int) -> str:
    """Run one isolated cmd tree; timeout kills only that process and descendants."""
    batch_b64 = base64.b64encode(batch.encode("utf-8")).decode("ascii")
    return f"""$ErrorActionPreference = 'Stop'
$run = {ps_literal(remote)}
try {{
    Set-Location -LiteralPath $run
    & tar.exe -xzf source.tar.gz
    if ($LASTEXITCODE -ne 0) {{ throw 'tar.exe extraction failed' }}
    $batchBytes = [Convert]::FromBase64String('{batch_b64}')
    [IO.File]::WriteAllBytes((Join-Path $run 'run_vivado.cmd'), $batchBytes)
    $process = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /v:off /c run_vivado.cmd' -WorkingDirectory $run -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $run 'console.log') -RedirectStandardError (Join-Path $run 'console.stderr.log')
    $null = $process.Handle
    if (-not $process.WaitForExit({timeout * 1000})) {{
        if (-not $process.HasExited) {{
            & taskkill.exe /PID $process.Id /T /F
            if ($LASTEXITCODE -ne 0) {{ throw 'Failed to terminate timed-out Vivado process tree' }}
        }}
        if (-not $process.WaitForExit(30000)) {{ throw 'Vivado process did not terminate' }}
        [IO.File]::WriteAllText((Join-Path $run 'vivado_exit_code.txt'), '124')
        exit 124
    }}
    $process.WaitForExit()
    $code = $process.ExitCode
    if ($null -eq $code) {{ throw 'Vivado process exit code unavailable' }}
    [IO.File]::WriteAllText((Join-Path $run 'vivado_exit_code.txt'), [string]$code)
    exit $code
}} catch {{
    [IO.File]::WriteAllText((Join-Path $run 'runner_failure.txt'), $_.ToString())
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}}
"""


def main() -> int:
    """Prepare an isolated source snapshot, then run and collect one screen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="yian@192.168.38.129")
    parser.add_argument("--remote-os", choices=("linux", "windows"), default="linux")
    parser.add_argument(
        "--part", required=True, help="Exact installed FPGA part; no guessed default"
    )
    parser.add_argument("--stages", type=int, choices=range(5, 64), default=7)
    parser.add_argument("--period", type=float, default=1.5625)
    parser.add_argument("--vivado", default="vivado", help="Executable path on SSH host")
    parser.add_argument("--settings", help="Optional remote settings64.sh path (Linux only)")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.host.startswith("-") or any(c.isspace() for c in args.host):
        parser.error("host must be an SSH hostname or user@host, not an option")
    if not 0 < args.period < float("inf") or args.timeout < 1:
        parser.error("period must be positive and finite; timeout must be positive")
    if args.remote_os == "windows":
        if args.settings:
            parser.error("Windows uses the installed vivado.bat; --settings is Linux only")
        if any(c in args.vivado for c in '%"\r\n\x00') or not args.vivado:
            parser.error("Windows Vivado path cannot contain percent, quotes or control characters")
        if not re.fullmatch(r"[A-Za-z0-9_.+\-]+", args.part):
            parser.error("Windows FPGA part must be a literal part identifier")
        if args.timeout > 2147483:
            parser.error("Windows timeout exceeds WaitForExit millisecond limit")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out = ROOT / "synth/artifacts/vivado" / f"{stamp}_p{args.stages}"
    out.mkdir(parents=True, exist_ok=False)
    sources = [
        line.strip()
        for line in (ROOT / "rtl/rtl_sources.f").read_text().splitlines()
        if line.strip()
    ]
    sources += ["rtl/rtl_sources.f", "synth/run_vivado_ooc.tcl"]
    sources += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "rtl/params").glob("*.vh"))]
    hashes = {}
    archive = out / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in sources:
            path = (ROOT / name).resolve()
            path.relative_to(ROOT)
            if not path.is_file():
                raise FileNotFoundError(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            tar.add(path, arcname=name, recursive=False)
    manifest = {
        "status": "PREPARED",
        "utc": stamp,
        "arguments": vars(args),
        "sources_sha256": hashes,
    }
    manifest_path = out / "manifest.json"

    def save() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    save()
    print(f"Artifacts: {out}", flush=True)
    if args.prepare_only:
        return 0
    ssh = ["ssh", *SSH_OPTIONS, args.host]
    # SFTP avoids remote cmd.exe interpretation of Windows drive paths/spaces.
    scp = ["scp", *(["-s"] if args.remote_os == "windows" else []), *SSH_OPTIONS]
    try:
        # One unique directory, no deletion/reuse of previous runs or user files.
        mkdir = "mkdir -p adc_rtl_vivado && mktemp -d adc_rtl_vivado/run.XXXXXXXX"
        if args.remote_os == "windows":
            mkdir = powershell_command("""$ErrorActionPreference = 'Stop'
$base = Join-Path $env:USERPROFILE 'adc_rtl_vivado'
$null = New-Item -ItemType Directory -Force -Path $base
$run = Join-Path $base ('run.' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $run
[Console]::Out.WriteLine($run.Replace('\\', '/'))
""")
        cp = subprocess.run(
            [*ssh, mkdir],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            timeout=20,
        )
        (out / "mkdir.stdout.log").write_text(cp.stdout, encoding="utf-8")
        (out / "mkdir.stderr.log").write_text(cp.stderr, encoding="utf-8")
        remote = cp.stdout.strip()
        if args.remote_os == "windows":
            valid_remote = bool(
                re.fullmatch(
                    r'[A-Za-z]:/[^\\\r\n:*?"<>|]+/adc_rtl_vivado/run\.[0-9a-f]{32}', remote
                )
            )
        else:
            valid_remote = remote.startswith("adc_rtl_vivado/run.") and all(
                c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./"
                for c in remote
            )
        if not valid_remote:
            raise RuntimeError(f"Unexpected remote work directory: {remote!r}")
        manifest["remote_directory"] = remote
        save()
        subprocess.run(
            [*scp, str(archive), f"{args.host}:{remote}/source.tar.gz"],
            check=True,
            timeout=60,
        )
        command = [
            "timeout",
            "--signal=TERM",
            "--kill-after=30s",
            f"{args.timeout}s",
            args.vivado,
            "-mode",
            "batch",
            "-nojournal",
            "-log",
            "vivado.log",
            "-source",
            "synth/run_vivado_ooc.tcl",
            "-tclargs",
            args.part,
            "out",
            str(args.period),
            str(args.stages),
        ]
        setup = f"source {shlex.quote(args.settings)} && " if args.settings else ""
        script = (
            f"cd {shlex.quote(remote)} && tar -xzf source.tar.gz && {setup}{shlex.join(command)}"
        )
        launch = "bash -lc " + shlex.quote(script)
        if args.remote_os == "windows":
            # CALL returns from the vendor batch file; preserve its actual code.
            batch = (
                "@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\n"
                f'call "{args.vivado}" -mode batch -nojournal -log vivado.log '
                f'-source synth/run_vivado_ooc.tcl -tclargs "{args.part}" '
                f"out {args.period} {args.stages}\r\n"
                "exit /b %ERRORLEVEL%\r\n"
            )
            (out / "run_vivado.cmd").write_bytes(batch.encode("utf-8"))
            script = windows_launch_script(remote, batch, args.timeout)
            (out / "remote_run.ps1").write_text(POWERSHELL_PREAMBLE + script, encoding="utf-8")
            launch = powershell_command(script)
        with (out / "ssh.log").open("w") as log:
            cp = subprocess.run(
                [*ssh, launch],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.timeout + 60,
            )
        manifest["exit_code"] = cp.returncode
        # Retain raw reports even when timing fails. No old PASS can be reused.
        items = ("out", "vivado.log")
        if args.remote_os == "windows":
            items = ("vivado_exit_code.txt", "console.log", "console.stderr.log", *items)
        for item in items:
            subprocess.run(
                [*scp, "-r", f"{args.host}:{remote}/{item}", str(out)],
                check=True,
                timeout=120,
            )
        if args.remote_os == "windows":
            remote_rc = (out / "vivado_exit_code.txt").read_text().strip()
            if remote_rc != str(cp.returncode):
                raise RuntimeError("Windows saved process exit code differs from SSH exit code")
        status = out / "out/status.txt"
        fields = {}
        if status.is_file():
            for line in status.read_text().splitlines():
                key, sep, value = line.partition("=")
                if not sep or key in fields:
                    raise RuntimeError("Malformed or duplicate Vivado status fields")
                fields[key] = value
        try:
            wns = float(fields["WNS_NS"])
            complete = (
                fields.get("STATUS") == "SYNTH_COMPLETE"
                and math.isfinite(wns)
                and fields.get("TIMING_MET") == str(int(wns >= 0))
                and fields.get("PART") == args.part
                and float(fields["PERIOD_NS"]) == args.period
                and int(fields["P_RECON_STAGES"]) == args.stages
                and bool(fields.get("VIVADO_VERSION"))
                and fields.get("SCOPE") == "FPGA_POST_SYNTH_OOC_VECTORLESS_NOT_ASIC"
                and cp.returncode == (0 if wns >= 0 else 3)
            )
        except (KeyError, ValueError):
            complete = False
        if not complete:
            raise RuntimeError(f"Vivado flow failed or inconsistent: rc={cp.returncode}")
        manifest["vivado_status"] = fields
        manifest["status"] = (
            "SYNTH_COMPLETE_TIMING_MET" if cp.returncode == 0 else "SYNTH_COMPLETE_TIMING_NOT_MET"
        )
        save()
        return cp.returncode
    except (OSError, RuntimeError, UnicodeError, subprocess.SubprocessError) as exc:
        manifest["status"] = "FAILED"
        manifest["error"] = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            manifest["stderr"] = exc.stderr
        save()
        print(f"Vivado flow failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
