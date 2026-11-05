"""检查运行库路径处理；启动器夹具不执行 RTL 仿真。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools/run_open_rtl.py"
SPEC = importlib.util.spec_from_file_location("runtime_support_test", SCRIPT)
assert SPEC and SPEC.loader
SUPPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUPPORT
SPEC.loader.exec_module(SUPPORT)

# 模拟上游 5.020 Perl 启动器的可执行文件搜索边界：
# 显式 ROOT 绕过启动器所在目录；夹具程序
# 只打印标记，因此这里只检查进程启动，不验证 Verilator 或 RTL。
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
    SUPPORT.alias_runtime(work, env, version)
    assert "VERILATOR_ROOT" not in env
    result = subprocess.run(
        [sys.executable, str(launcher)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "LAUNCHER_FIXTURE_STARTED\n"
    # 负对照：旧运行器注入仅含头文件的根路径，
    # 在 RTL 编译前即破坏原本可启动的同一安装。
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
    SUPPORT.alias_runtime(tmp_path, env, "VERILATOR_ROOT = /another/runtime\n")
    assert env == original
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("name", ["bundled runtime", "bundle_中文"])
@pytest.mark.parametrize("explicit", [False, True])
def test_unsafe_self_contained_bundle_gets_equivalent_ascii_alias(tmp_path, name, explicit):
    runtime = tmp_path / name
    executable(runtime / "bin/verilator_bin")
    work = tmp_path / "work"
    work.mkdir()
    env = {"VERILATOR_ROOT": str(runtime)} if explicit else {}
    SUPPORT.alias_runtime(work, env, f"VERILATOR_ROOT = {runtime}\n")
    alias = Path(env["VERILATOR_ROOT"])
    assert alias == work / "runtime"
    assert alias.resolve() == runtime
    assert os.access(alias / "bin/verilator_bin", os.X_OK)


def test_header_only_non_ascii_runtime_is_not_relocated(tmp_path):
    runtime = tmp_path / "headers_中文"
    runtime.mkdir()
    env = {}
    SUPPORT.alias_runtime(tmp_path, env, f"VERILATOR_ROOT = {runtime}\n")
    assert env == {}
    assert not (tmp_path / "runtime").exists()


def test_version_without_runtime_does_not_inject_environment(tmp_path):
    env = {"VERILATOR_BIN": "caller-binary"}
    SUPPORT.alias_runtime(tmp_path, env, "Verilator version fixture\n")
    assert env == {"VERILATOR_BIN": "caller-binary"}


def test_runtime_version_uses_the_last_nonempty_root_without_overwriting_installation(tmp_path):
    runtime = tmp_path / "explicit bundle_中文"
    executable(runtime / "bin/verilator_bin")
    work = tmp_path / "work"
    work.mkdir()
    env = {}
    version = f"VERILATOR_ROOT = /headers\nVERILATOR_ROOT = {runtime}\nVERILATOR_ROOT = \n"
    SUPPORT.alias_runtime(work, env, version)
    assert Path(env["VERILATOR_ROOT"]).resolve() == runtime


def test_an_existing_alias_to_a_different_runtime_is_not_replaced(tmp_path):
    runtime = tmp_path / "new bundle"
    executable(runtime / "bin/verilator_bin")
    work = tmp_path / "work"
    work.mkdir()
    old = tmp_path / "old tool"
    old.mkdir()
    alias = work / "runtime"
    alias.symlink_to(old, target_is_directory=True)
    with pytest.raises(ValueError, match="another tool"):
        SUPPORT.alias_runtime(work, {}, f"VERILATOR_ROOT = {runtime}\n")
    assert alias.resolve() == old
