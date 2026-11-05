"""仅为六组综合支持测试提供显式输入及本机文本工具替身。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "synth_support_test_driver", ROOT / "synth/run_synth.py"
)
assert SPEC is not None and SPEC.loader is not None
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)

MARKER = (
    "GENERIC_SYNTH_COMPLETE sources=2 registers=2 clocks=1 "
    "reported_paths=2 closure=NOT_CERTIFIED"
)


def tool(name: str) -> str:
    """缺少本机文本解释器时报错，不跳过约束和失败传播检查。"""
    result = shutil.which(name)
    assert result is not None, f"Required local text-test executable is missing: {name}"
    return result


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(directory: Path) -> dict[str, Path]:
    """写明全部源、库和SDC字段；文本只是假工具输入，不能用于器件时序结论。"""
    directory.mkdir(parents=True, exist_ok=True)
    include = directory / "include"
    include.mkdir()
    source = directory / "sample_counter.sv"
    source.write_text("module sample_counter; endmodule\n", encoding="utf-8")
    leaf = directory / "leaf.sv"
    leaf.write_text("module leaf; endmodule\n", encoding="utf-8")
    header = include / "test_constants.svh"
    header.write_text("`define TEST_CONSTANT 1\n", encoding="utf-8")
    filelist = directory / "filelist.json"
    filelist.write_text(
        json.dumps(
            {
                "sources": [source.name, leaf.name],
                "include_dirs": [include.name],
                "headers": ["include/" + header.name],
                "defines": ["TEXT_FIXTURE=1"],
            }
        ),
        encoding="utf-8",
    )
    library = directory / "synthetic.lib"
    library.write_text("SYNTHETIC TEXT LIBRARY; NOT A TIMING MODEL\n", encoding="utf-8")
    sdc = directory / "user.sdc"
    sdc.write_text("# Explicit synthetic SDC for mocked command control only\n", encoding="utf-8")
    return {
        "source": source,
        "leaf": leaf,
        "header": header,
        "include": include,
        "filelist": filelist,
        "library": library,
        "sdc": sdc,
    }


def arguments(data: dict[str, Path], out: Path, executable: str, *extra: str) -> list[str]:
    return [
        "--tool",
        executable,
        "--top",
        "sample_counter",
        "--filelist",
        str(data["filelist"]),
        "--library",
        str(data["library"]),
        "--sdc",
        str(data["sdc"]),
        "--clock",
        "sample_clock",
        "--compile-command",
        "compile",
        "--out",
        str(out),
        *extra,
    ]


def invoke(data: dict[str, Path], out: Path, executable: str, *extra: str):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "synth/run_synth.py"),
            *arguments(data, out, executable, *extra),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def fake_tool(
    directory: Path, *, output: str = MARKER, returncode: int = 0, before: str = ""
) -> Path:
    """生成明确的Python替身；不调用综合器、许可证或工艺文件。"""
    path = directory / "fake synthesis command"
    path.write_text(
        f"#!{sys.executable}\n"
        "# Synthetic command-control fixture; not a vendor tool.\n"
        "import sys\n" + before + f"\nprint({output!r})\nraise SystemExit({returncode!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def manifest(out: Path) -> dict:
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def tcl_probe(directory: Path, text: str, *, env: dict | None = None, args: tuple = ()):
    harness = directory / "probe.tcl"
    harness.write_text(text, encoding="utf-8")
    return subprocess.run(
        [tool("tclsh"), str(harness), *map(str, args)],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
