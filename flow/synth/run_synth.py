#!/usr/bin/env python3
"""按调用方声明的文件准备或执行本机综合，不补器件默认参数。

输入准备本身不是综合或静态时序分析证据。外部工具身份及工艺适用性由调用方
保证；本封装记录命令、输入和结果，不据此认证时序收敛。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    """计算调用方指定文件的 SHA256，以实际内容标识输入。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_filelist(path: Path) -> dict:
    """读取显式源文件、包含目录、头文件和宏列表，不扫描源码树。

    路径相对于清单文件所在目录解析；列表去重并检查对应文件或目录存在。

    Args:
        path: JSON 清单路径；键必须恰为 sources、include_dirs、headers、defines。

    Returns:
        校验后的映射；路径项为 Path，宏定义项仍为字符串。

    Raises:
        ValueError: 字段、条目、重复项、扩展名或路径不符合清单约束。
    """

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate filelist key: {key}")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError(f"nonfinite filelist value: {value}")

    path = path.expanduser().resolve()
    raw = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=reject_constant
    )
    if not isinstance(raw, dict) or set(raw) != {"sources", "include_dirs", "headers", "defines"}:
        raise ValueError("filelist requires exactly sources/include_dirs/headers/defines lists")
    result = {}
    for key, values in raw.items():
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v.strip() or any(c in v for c in "\r\n\0") for v in values
        ):
            raise ValueError(f"{key}: explicit nonempty single-line strings required")
        if key == "defines":
            if any(not re.fullmatch(r"[A-Za-z_]\w*(?:=[A-Za-z0-9_+'.-]+)?", v) for v in values):
                raise ValueError("unsupported define encoding")
            result[key] = values
            continue
        files = [(Path(v) if Path(v).is_absolute() else path.parent / v).resolve() for v in values]
        if len(files) != len(set(files)):
            raise ValueError(f"duplicate {key} input")
        if any(not (p.is_dir() if key == "include_dirs" else p.is_file()) for p in files):
            raise ValueError(f"missing declared {key} input")
        if key == "sources" and (
            not files or any(p.suffix.lower() not in {".sv", ".v"} for p in files)
        ):
            raise ValueError("nonempty explicit .sv/.v source list required")
        if key == "headers" and any(p.suffix.lower() not in {".vh", ".svh"} for p in files):
            raise ValueError("headers must name .vh/.svh files")
        result[key] = files
    return result


def new_output(path: Path) -> Path:
    """创建新输出目录，拒绝已有路径以及任何 Git 工作树内部的位置。"""
    raw = path.expanduser()
    if raw.is_symlink():
        raise ValueError("output must be a new directory, not an existing symlink")
    out = raw.resolve()
    if (
        out.exists()
        or out == ROOT
        or out.is_relative_to(ROOT)
        or any((p / ".git").exists() for p in (out, *out.parents))
    ):
        raise ValueError("output must be a new directory outside Git working trees")
    out.mkdir(parents=True, exist_ok=False)
    return out


def tcl_word(value: str) -> str:
    """将字符串编码为一个 Tcl 字面值，转义变量、命令及换行替换。"""
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    value = value.replace("[", "\\[").replace("]", "\\]").replace("\n", "\\n").replace("\r", "\\r")
    return '"' + value + '"'


def render_contract(config: dict) -> str:
    """将已声明输入写成 Tcl 数据字，保留调用方给出的约束。"""
    rows = [
        "# Explicit synthesis inputs, not an ADC profile.",
        "set ::synth_inputs [dict create \\",
    ]
    for key, value in config.items():
        encoded = (
            "[list " + " ".join(tcl_word(str(v)) for v in value) + "]"
            if isinstance(value, list)
            else tcl_word(str(value))
        )
        rows.append(f"    {key} {encoded} \\")
    return "\n".join([*rows, "]", ""])


def write_json(path: Path, value: dict) -> None:
    """写入本次调用的完整审阅或执行结果记录。"""
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    """分别处理输入准备与外部工具执行，并保留实际返回码和日志。

    --prepare-only 只准备输入，不调用工具。执行路径检查唯一完成标记、正计数、
    源文件数量、输入摘要和错误诊断；成功记录仍不等于时序收敛认证。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tool", "top", "clock"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--filelist", type=Path, required=True)
    parser.add_argument("--library", type=Path, nargs="+", required=True)
    parser.add_argument("--sdc", type=Path, required=True)
    parser.add_argument("--compile-command", choices=("compile", "compile_ultra"), required=True)
    parser.add_argument("--parameters")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    out = None
    record = {
        "schema_version": 1,
        "status": "VALIDATING",
        "tool_execution_performed": False,
        "synthesis_evidence": "NOT_RUN",
        "sta_evidence": "NOT_RUN",
        "scope": "GENERIC_DECLARED_INPUTS_NOT_SIGNOFF",
        "tool_authenticity": "CALLER_SUPPLIED_EXECUTABLE_NOT_AUTHENTICATED_BY_WRAPPER",
    }
    try:
        if (
            args.timeout <= 0
            or not re.fullmatch(r"[A-Za-z_]\w*", args.top)
            or not re.fullmatch(r"[A-Za-z_]\w*", args.clock)
        ):
            raise ValueError("positive timeout and simple explicit top/clock identifiers required")
        if args.parameters is not None and not re.fullmatch(
            r"[A-Za-z_]\w*=-?[0-9]+(?:,[A-Za-z_]\w*=-?[0-9]+)*", args.parameters
        ):
            raise ValueError("parameters require explicit comma-separated NAME=integer assignments")
        inputs = load_filelist(args.filelist)
        libraries, sdc = (
            [p.expanduser().resolve() for p in args.library],
            args.sdc.expanduser().resolve(),
        )
        if len(libraries) != len(set(libraries)) or any(
            not p.is_file() or p.stat().st_size == 0 for p in [*libraries, sdc]
        ):
            raise ValueError("distinct nonempty library and SDC files required")
        declared = [
            args.filelist.resolve(),
            *inputs["sources"],
            *inputs["headers"],
            *libraries,
            sdc,
        ]
        hashes = {str(p): sha(p) for p in declared}
        out = new_output(args.out)
        snapshot = out / "input_snapshot"
        snapshot.mkdir()
        for index, path in enumerate(declared):
            (snapshot / f"{index:04d}_{path.name}").write_bytes(path.read_bytes())
        config = {
            "top": args.top,
            "sources": [str(p) for p in inputs["sources"]],
            "include_dirs": [str(p) for p in inputs["include_dirs"]],
            "defines": inputs["defines"],
            "libraries": [str(p) for p in libraries],
            "sdc": str(sdc),
            "clock": args.clock,
            "compile_command": args.compile_command,
            "parameters": args.parameters or "",
            "out": str(out / "reports"),
        }
        (out / "inputs.tcl").write_text(render_contract(config), encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("run_dc.tcl"), out / "run_dc.tcl")
        record.update(
            {
                "status": "INPUTS_PREPARED",
                "declared_input_sha256": hashes,
                "config": config,
                "requested_tool": args.tool,
                "script_sha256": sha(out / "run_dc.tcl"),
                "contract_sha256": sha(out / "inputs.tcl"),
            }
        )
        write_json(out / "manifest.json", record)
        if args.prepare_only:
            print("SYNTH_INPUTS_PREPARED execution=NOT_RUN sta=NOT_RUN")
            return 0
        tool = shutil.which(args.tool)
        if tool is None:
            raise ValueError("explicit tool not found; no installation or fallback performed")
        command = [tool, "-no_gui", "-f", str(out / "run_dc.tcl")]
        record.update({"tool_command": command, "tool_execution_performed": True})
        with (out / "tool.log").open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=out,
                env=dict(os.environ, SYNTH_INPUT_TCL=str(out / "inputs.tcl")),
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            )
        record["raw_returncode"] = result.returncode
        log = (out / "tool.log").read_text(errors="backslashreplace")
        markers = re.findall(
            r"(?m)^GENERIC_SYNTH_COMPLETE sources=([0-9]+) registers=([0-9]+) clocks=1 reported_paths=2 closure=NOT_CERTIFIED$",
            log,
        )
        if (
            result.returncode not in (0, 3)
            or len(markers) != 1
            or any(int(v) <= 0 for v in markers[0])
        ):
            raise RuntimeError("external command supplied no complete generic synthesis record")
        if int(markers[0][0]) != len(inputs["sources"]) or any(
            sha(Path(p)) != h for p, h in hashes.items()
        ):
            raise RuntimeError(
                "source count disagrees or a declared input changed during execution"
            )
        if re.search(r"(?mi)^\s*(?:Error:|Error-\[|Fatal:|DC_FATAL)", log):
            raise RuntimeError("external tool emitted an error")
        record.update(
            {
                "status": "TIMING_VIOLATION_OBSERVED"
                if result.returncode == 3
                else "EXTERNAL_COMMAND_COMPLETE_NOT_SIGNOFF",
                "synthesis_evidence": "EXTERNAL_COMMAND_RECORD_REQUIRES_TOOL_AUTHENTICATION",
                "sta_evidence": "REPORTED_PATHS_ONLY_NOT_TIMING_CLOSURE",
            }
        )
        write_json(out / "manifest.json", record)
        print(f"SYNTH_COMMAND_RECORDED rc={result.returncode} closure=NOT_CERTIFIED")
        return result.returncode
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        if out is not None:
            record.update({"status": "FAILED", "error": str(error)})
            if record["tool_execution_performed"]:
                record.setdefault("raw_returncode", None)
                record["synthesis_evidence"] = "NO_COMPLETE_EXTERNAL_COMMAND_RESULT"
                record["sta_evidence"] = "NOT_ESTABLISHED"
            write_json(out / "manifest.json", record)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
