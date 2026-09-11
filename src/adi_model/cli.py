"""Console entry points: ``adi-run-all`` and ``adi-make-report``.

Why this module exists
----------------------
``pyproject.toml`` declares two console scripts. Until this module existed the
declaration was aspirational: ``pip install`` succeeded but the commands failed
with ``ModuleNotFoundError: adi_model.cli``. A declared-but-missing entry point
is worse than no entry point, because it turns a missing feature into a broken
install. Two tests (``tests/unit/test_cli.py``) now keep the declaration and the
implementation in step.

Division of labour
------------------
The full acceptance sweep lives in ``tools/run_all.py`` and the report builder
in ``tools/make_report.py``. They are *scripts* (module-level execution, results
written next to themselves) rather than library functions, and they are
deliberately left that way: a reviewer can read one file top to bottom and see
the whole experiment. This module is a thin, argument-parsing launcher that runs
them in-process with an explicit results directory, so that:

* ``adi-run-all`` works from any working directory;
* the output location is controllable (``--results-dir``, or the
  ``ADI_MODEL_RESULTS_DIR`` environment variable) instead of always being
  ``tools/results``;
* a missing script produces a clear message rather than a traceback.

Contract
--------
* Both commands are found relative to this package (source checkout /
  editable install) or via a sibling ``tools`` directory of the current working
  directory.
* Neither command mutates the caller's environment: ``ADI_MODEL_RESULTS_DIR`` is
  set for the duration of the run and restored afterwards.
* Return values are process exit codes (0 = success, 2 = usage/locale error).

Example:
-------
>>> from adi_model.cli import main_run_all, main_make_report
>>> callable(main_run_all) and callable(main_make_report)
True
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path

__all__ = ["main_run_all", "main_make_report", "find_tools_dir"]

#: Scripts that must be present for the launcher to be usable.
REQUIRED_SCRIPTS = ("run_all.py", "make_report.py")

_ENV_OUT = "ADI_MODEL_RESULTS_DIR"


def find_tools_dir(start: Path | None = None) -> Path | None:
    """Locate the directory holding ``run_all.py`` / ``make_report.py``.

    Args:
        start: Directory to search from. Defaults to this module's directory.

    Returns:
        The tools directory, or ``None`` if it cannot be found. Searching
        relative to the package covers source checkouts and editable installs;
        searching the working directory covers the case where the package was
        installed into site-packages but the user is standing in the repository.
    """
    explicit = start is not None
    here = Path(start).resolve() if start is not None else Path(__file__).resolve().parent
    candidates = [
        here / "_tools",  # vendored next to the package
        here.parent / "tools",  # src/adi_model -> src/tools
        here.parent.parent / "tools",  # src/adi_model -> <repo>/tools
    ]
    if not explicit:
        # Only as a last resort, and only for the implicit search: an installed
        # package running inside a checkout should still find the scripts.
        candidates.append(Path.cwd() / "tools")
    for cand in candidates:
        try:
            if all((cand / name).is_file() for name in REQUIRED_SCRIPTS):
                return cand.resolve()
        except OSError:  # pragma: no cover - unreadable path
            continue
    return None


def _run_script(script_name: str, results_dir: str | None) -> int:
    """Execute one of the tools scripts in-process with an explicit output dir.

    Args:
        script_name: File name inside the tools directory.
        results_dir: Output directory. ``None`` keeps the script's own default
            (``<tools>/results``).

    Returns:
        Process exit code.

    Side effects:
        Temporarily sets ``ADI_MODEL_RESULTS_DIR``, restores it afterwards, and
        runs the script (which writes ``results.json`` and figures).
    """
    tools = find_tools_dir()
    if tools is None:
        sys.stderr.write(
            "error: could not find the experiment scripts "
            f"({', '.join(REQUIRED_SCRIPTS)}).\n"
            "       The sweep scripts ship with the source tree, not with the\n"
            "       wheel. Run from a checkout (or `pip install -e .`), or pass\n"
            "       the path to a repository copy.\n"
        )
        return 2
    script = tools / script_name

    if results_dir is not None:
        out = Path(results_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

    if (
        script_name == "make_report.py"
        and results_dir is not None
        and not (Path(results_dir).expanduser().resolve() / "results.json").is_file()
    ):
        sys.stderr.write(
            f"error: no results.json in {results_dir!r}. " "Run `adi-run-all` first.\n"
        )
        return 2

    previous = os.environ.get(_ENV_OUT)
    if results_dir is not None:
        os.environ[_ENV_OUT] = str(Path(results_dir).expanduser().resolve())
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        if results_dir is not None:
            if previous is None:
                os.environ.pop(_ENV_OUT, None)
            else:  # pragma: no cover - only when the caller pre-set the var
                os.environ[_ENV_OUT] = previous
    return 0


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    """构造两个子命令共用的 ArgumentParser，仅含 --results-dir 一个选项。

    Args:
        prog: 程序名（str），显示在 --help 的 usage 行。
        description: 命令描述（str），显示在 --help 的正文顶部。

    Returns:
        已注册 ``--results-dir``（默认 None，指向 tools/results）的
        ArgumentParser；调用方再补充分支特定的子命令解析。
    """
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument(
        "--results-dir",
        default=None,
        metavar="DIR",
        help="where to write results.json and figs (default: tools/results)",
    )
    return p


def main_run_all(argv: Sequence[str] | None = None) -> int:
    """Run the full acceptance sweep (all experiment stages + figures + JSON).

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 2 if the scripts cannot be located.
    """
    args = _parser(
        "adi-run-all",
        "Run every acceptance stage, save results.json and the figures.",
    ).parse_args(argv)
    return _run_script("run_all.py", args.results_dir)


def main_make_report(argv: Sequence[str] | None = None) -> int:
    """Render the self-contained HTML report from an existing results.json.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 2 if the scripts or results.json are
        missing.
    """
    args = _parser(
        "adi-make-report",
        "Build the self-contained HTML report from results.json.",
    ).parse_args(argv)
    return _run_script("make_report.py", args.results_dir)
