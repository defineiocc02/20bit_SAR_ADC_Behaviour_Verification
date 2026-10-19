"""Exercise runtime-path handling; launcher fixtures do not simulate RTL."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools/audit_rtl_arithmetic.py"
SPEC = importlib.util.spec_from_file_location("audit_rtl_arithmetic", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)

# Model the executable search boundary in the upstream 5.020 Perl launcher:
# an explicit ROOT bypasses the launcher's own executable directory. The fixture
# binary only prints a marker; this verifies process startup, not Verilator or RTL.
LAUNCHER = """import os
from pathlib import Path
import sys
root = os.environ.get("VERILATOR_ROOT")
if root is None:
    binary = Path(__file__).parent / "verilator_bin"
else:
    binary = Path(root) / "bin/verilator_bin"
    if not os.access(binary, os.X_OK):
        binary = Path(root) / "verilator_bin"
try:
    os.execv(str(binary), [str(binary), *sys.argv[1:]])
except FileNotFoundError:
    print(f"launcher binary missing: {binary}", file=sys.stderr)
    sys.exit(127)
"""


def executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nprint('LAUNCHER_FIXTURE_STARTED')\n")
    path.chmod(0o755)


def test_split_install_starts_without_forcing_header_root(tmp_path):
    prefix = tmp_path / "installed"
    runtime = prefix / "share/verilator"
    runtime.mkdir(parents=True)
    executable(prefix / "bin/verilator_bin")
    launcher = prefix / "bin/verilator"
    launcher.write_text(LAUNCHER)
    work = tmp_path / "work"
    work.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "VERILATOR_ROOT"}
    version = f"VERILATOR_ROOT = {runtime}\n"
    AUDIT.alias_bundled_runtime(work, env, version)
    assert "VERILATOR_ROOT" not in env
    result = subprocess.run(
        [sys.executable, str(launcher)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "LAUNCHER_FIXTURE_STARTED\n"
    # Negative control: the old runner injected this header-only root and broke
    # the same otherwise-valid installation, before any RTL compilation began.
    result = subprocess.run(
        [sys.executable, str(launcher)],
        env={**env, "VERILATOR_ROOT": str(runtime)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 127
    assert "launcher binary missing" in result.stderr


def test_explicit_ascii_root_and_binary_are_preserved(tmp_path):
    env = {"VERILATOR_ROOT": str(tmp_path / "caller-root"), "VERILATOR_BIN": "caller-binary"}
    original = dict(env)
    AUDIT.alias_bundled_runtime(tmp_path, env, "VERILATOR_ROOT = /another/runtime\n")
    assert env == original
    assert not (tmp_path / "verilator").exists()


@pytest.mark.parametrize("name", ["bundled runtime", "bundle_中文"])
@pytest.mark.parametrize("explicit", [False, True])
def test_unsafe_self_contained_bundle_gets_equivalent_ascii_alias(tmp_path, name, explicit):
    runtime = tmp_path / name
    executable(runtime / "bin/verilator_bin")
    work = tmp_path / "work"
    work.mkdir()
    env = {"VERILATOR_ROOT": str(runtime)} if explicit else {}
    AUDIT.alias_bundled_runtime(work, env, f"VERILATOR_ROOT = {runtime}\n")
    alias = Path(env["VERILATOR_ROOT"])
    assert alias == work / "verilator"
    assert alias.resolve() == runtime
    assert os.access(alias / "bin/verilator_bin", os.X_OK)


def test_header_only_non_ascii_runtime_is_not_relocated(tmp_path):
    runtime = tmp_path / "headers_中文"
    runtime.mkdir()
    env = {}
    AUDIT.alias_bundled_runtime(tmp_path, env, f"VERILATOR_ROOT = {runtime}\n")
    assert env == {}
    assert not (tmp_path / "verilator").exists()


def test_version_without_runtime_does_not_inject_environment(tmp_path):
    env = {"VERILATOR_BIN": "caller-binary"}
    AUDIT.alias_bundled_runtime(tmp_path, env, "Verilator version fixture\n")
    assert env == {"VERILATOR_BIN": "caller-binary"}
