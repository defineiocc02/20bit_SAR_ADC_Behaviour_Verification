"""检查已声明的控制台入口存在并能解析。

pyproject.toml 提供 adi-run-all 与 adi-make-report。历史安装虽成功，命令曾因
缺少 adi_model.cli 而抛 ModuleNotFoundError；本组分别检查入口、参数和 CI
调用的一致性，不以安装成功代替命令可运行。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 — tomllib landed in 3.11; tomli is the backport.
    import tomli as tomllib

from adi_model import cli

REPO_ROOT = Path(__file__).resolve().parents[2]


def _accepted_flags(prog: str) -> set[str]:
    """取得指定入口解析器实际接受的 --flag 集合。"""
    parser = cli._parser(prog, "help text is irrelevant here")
    return {opt for action in parser._actions for opt in action.option_strings}


class TestEntryPoints:
    def test_pyproject_declares_the_two_scripts(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
            data = tomllib.load(fh)
        scripts = data["project"]["scripts"]
        assert scripts["adi-run-all"] == "adi_model.cli:main_run_all"
        assert scripts["adi-make-report"] == "adi_model.cli:main_make_report"

    @pytest.mark.parametrize(
        "dotted", ["adi_model.cli:main_run_all", "adi_model.cli:main_make_report"]
    )
    def test_each_declared_target_is_importable_and_callable(self, dotted):
        module_name, attr = dotted.split(":")
        mod = __import__(module_name, fromlist=[attr])
        assert callable(getattr(mod, attr))

    def test_package_is_runnable_as_a_module(self):
        from adi_model import __main__ as mod_main

        assert callable(mod_main.main)


class TestScriptDiscovery:
    def test_finds_the_tools_directory_in_this_checkout(self):
        found = cli.find_tools_dir()
        assert found is not None, "the sweep scripts must be discoverable from a source checkout"
        for name in cli.REQUIRED_SCRIPTS:
            assert (found / name).is_file()

    def test_returns_none_for_an_empty_tree(self, tmp_path):
        assert cli.find_tools_dir(start=tmp_path) is None

    def test_missing_scripts_produce_a_message_not_a_traceback(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(cli, "find_tools_dir", lambda *a, **k: None)
        rc = cli.main_run_all(["--results-dir", str(tmp_path / "out")])
        assert rc == 2
        assert "could not find the experiment scripts" in capsys.readouterr().err

    def test_make_report_without_results_json_fails_cleanly(self, tmp_path, capsys):
        rc = cli.main_make_report(["--results-dir", str(tmp_path)])
        assert rc == 2
        assert "results.json" in capsys.readouterr().err

    def test_results_dir_is_honoured_and_environment_restored(self, tmp_path, monkeypatch):
        """检查调用期间设置 ADI_MODEL_RESULTS_DIR，并在结束后恢复调用方环境。"""
        import os

        captured: dict[str, str | None] = {}

        def fake_run_path(path, run_name):
            captured["env"] = os.environ.get("ADI_MODEL_RESULTS_DIR")
            captured["path"] = path

        monkeypatch.setattr(cli.runpy, "run_path", fake_run_path)
        monkeypatch.delenv("ADI_MODEL_RESULTS_DIR", raising=False)

        rc = cli.main_run_all(["--results-dir", str(tmp_path / "res")])
        assert rc == 0
        assert captured["env"] == str((tmp_path / "res").resolve())
        assert captured["path"].endswith("run_all.py")
        assert "ADI_MODEL_RESULTS_DIR" not in os.environ

    def test_preexisting_environment_value_is_restored(self, tmp_path, monkeypatch):
        import os

        monkeypatch.setattr(cli.runpy, "run_path", lambda path, run_name: None)
        monkeypatch.setenv("ADI_MODEL_RESULTS_DIR", "/tmp/original")
        cli.main_make_report(["--results-dir", str(tmp_path)])
        assert os.environ["ADI_MODEL_RESULTS_DIR"] == "/tmp/original"


class TestCIMatchesTheCLI:
    """检查 CI 使用入口解析器实际支持的参数。

    历史确定性任务使用不受支持的 adi-run-all --out DIR，在实验开始前即被
    unrecognized arguments 拒绝；这里检查实际 run 命令与解析器的一致性。
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

    #: ``adi-run-all --flag value`` / ``adi-make-report --flag value``
    _INVOCATION = re.compile(
        r"\b(adi-run-all|adi-make-report)\b((?:\s+--[A-Za-z0-9-]+(?:\s+\S+)?)*)"
    )

    def _invocations(self):
        text = self.WORKFLOW.read_text()
        for prog, tail in self._INVOCATION.findall(text):
            flags = re.findall(r"(--[A-Za-z0-9-]+)", tail)
            if flags:
                yield prog, flags

    def test_workflow_exists(self):
        assert self.WORKFLOW.is_file(), "CI workflow is missing"

    def test_every_ci_invocation_uses_known_flags(self):
        seen = list(self._invocations())
        assert seen, "no launcher invocations found — did the workflow change shape?"
        for prog, flags in seen:
            accepted = _accepted_flags(prog)
            unknown = [f for f in flags if f not in accepted]
            assert not unknown, (
                f"ci.yml calls `{prog}` with {unknown}, which its parser rejects "
                f"(accepted: {sorted(accepted)}). The job would fail before "
                "doing any work."
            )

    def test_sweep_is_driven_by_the_acceptance_script(self):
        """检查 CI 实际执行 run_all.py 验收入口。

        按 run 命令而非注释中提到的命令判定，避免文档文字触发错误；也不以无对应
        测试收集的 pytest -m slow 代替验收脚本。
        """
        commands = [
            line.split("run:", 1)[1].strip()
            for line in self.WORKFLOW.read_text().splitlines()
            if line.strip().startswith("run:")
        ]
        assert commands, "no `run:` steps found in ci.yml"

        assert any("adi-run-all" in c for c in commands), (
            "ci.yml no longer runs the acceptance sweep; the acceptance stages "
            "would never be executed in CI"
        )
        assert not any('pytest -m "slow"' in c for c in commands), (
            'ci.yml still runs `pytest -m "slow"`, which deselected every test '
            "in the suite and therefore verified nothing"
        )
