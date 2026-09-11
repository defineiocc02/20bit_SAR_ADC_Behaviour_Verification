"""The declared console entry points must exist and resolve.

``pyproject.toml`` advertises ``adi-run-all`` and ``adi-make-report``. Before
this module existed, ``pip install`` succeeded and the commands failed with
``ModuleNotFoundError: adi_model.cli`` — a broken install is worse than a
missing feature, so the wiring is pinned by tests.
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
    """Every ``--flag`` the named launcher's parser will accept."""
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
        """The launcher exports ADI_MODEL_RESULTS_DIR for the duration of the
        run and restores the caller's value afterwards — running a sweep must
        not leak state into the process that called it.
        """
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
    """The workflow must call the launchers with flags that actually exist.

    The determinism job used to invoke ``adi-run-all --out DIR``. ``--out`` is
    not a flag of the parser, so the job died on ``unrecognized arguments``
    before running a single experiment — a green-looking pipeline that verified
    nothing. These tests read the workflow and reject any invocation whose
    flags the parser would refuse.
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
        """`pytest -m slow` collects nothing: no test carries that marker.

        The real sweep is `run_all.py`. If CI stops calling it, the acceptance
        criteria silently stop being exercised. Only ``run:`` commands count —
        a comment mentioning the old command is documentation, not a defect.
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
