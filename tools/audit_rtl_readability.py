#!/usr/bin/env python3
"""Prove this documentation-only RTL edit preserves source/tool tokens.

This finite lexer targets the repository's SV subset; it is not a formal HDL
checker. Verilator preprocessing independently checks include/macro expansion.
Failure is explicit; an unknown character or unsupported continued directive
must not silently produce an equivalence result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"|\\[^\s]+|'
    r"(?:[0-9][0-9_]*\s*)?'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ?_]+|"
    r"'[01xXzZ]|[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][+-]?[0-9_]+)?|"
    r"[a-zA-Z_$][a-zA-Z0-9_$]*|"
    r"<<<|>>>|===|!==|<<=|>>=|\*\*|::|<=|>=|==|!=|&&|\|\||"
    r"<<|>>|\+\+|--|\+:|-:|~\^|\^~|~&|~\||->|=>|"
    r"[{}\[\]();,:.?@#`'~!%^&*+\-/|<>=]"
)
TOOL_COMMENT = re.compile(r"^\s*(verilator|synopsys|synthesis|pragma|cadence|altera)\b", re.I)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tokens(source: str) -> tuple[list[str], list[str]]:
    # Newlines terminate directives/macros. For the finite current source set,
    # preserve the exact directive's tokens and reject line continuations.
    directives = []
    for line in source.splitlines():
        if line.lstrip().startswith("`"):
            if line.rstrip().endswith("\\"):
                raise ValueError("Continued directive requires a separate preprocessing review")
            text = line.split("//", 1)[0]
            directives.append(" ".join(m.group() for m in TOKEN.finditer(text)))
    result: list[str] = []
    pragmas: list[str] = []
    pos = 0
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
        elif source.startswith("//", pos) or source.startswith("/*", pos):
            line = source.startswith("//", pos)
            end = source.find("\n" if line else "*/", pos + 2)
            if end < 0:
                if not line:
                    raise ValueError("Unterminated block comment")
                end = len(source)
            comment = source[pos + 2 : end]
            if TOOL_COMMENT.match(comment):
                pragma = " ".join(comment.split())
                pragmas.append(pragma)
                result.append("COMMENT_PRAGMA:" + pragma)
            pos = end if line else end + 2
        else:
            match = TOKEN.match(source, pos)
            if match is None:
                raise ValueError(f"Unsupported lexical input at {pos}: {source[pos : pos + 30]!r}")
            result.append(match.group())
            pos = match.end()
    return result, directives


def git_bytes(commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=REPO, check=True, capture_output=True
    ).stdout


def preprocess(root: Path, command: list[str], files: list[str]) -> bytes:
    proc = subprocess.run(
        [*command, "-E", "-P", "--pp-comments", "-Irtl/params", *files],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=120,
    )
    if proc.stderr:
        raise RuntimeError(f"Preprocessing diagnostic must be reviewed: {proc.stderr.decode()}")
    return proc.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="71e7d5a017246340b9fa1f71cbbe99e20b6503f6")
    parser.add_argument("--out", required=True, type=Path, help="New audit output directory")
    args = parser.parse_args()
    command = shlex.split(os.environ.get("VERILATOR", "verilator"))
    if not command:
        raise ValueError("VERILATOR must name an executable")
    files = (REPO / "rtl/rtl_sources.f").read_text().splitlines()
    files = [p for p in files if p.strip()]
    files += ["rtl/params/rtl_params.vh", "rtl/params/rtl_error_codes.vh"]
    if len(files) != 26 or len(set(files)) != len(files):
        raise ValueError("Expected the frozen 24 SV + 2 header file set")
    if git_bytes(args.baseline, "rtl/rtl_sources.f") != (REPO / "rtl/rtl_sources.f").read_bytes():
        raise ValueError("Source file manifest changed")
    rows = []
    before_all, after_all = [], []
    with tempfile.TemporaryDirectory(prefix="sar_rtl_baseline_") as temp:
        baseline_root = Path(temp)
        for name in files:
            before = git_bytes(args.baseline, name)
            after = (REPO / name).read_bytes()
            old_tokens, old_directives = tokens(before.decode("utf-8"))
            new_tokens, new_directives = tokens(after.decode("utf-8"))
            if old_tokens != new_tokens or old_directives != new_directives:
                raise ValueError(f"Effective source tokens/directives changed: {name}")
            if name.endswith("rtl_params.vh") and before != after:
                raise ValueError("Generated parameter header was edited")
            target = baseline_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
            before_all.append(old_tokens)
            after_all.append(new_tokens)
            rows.append(
                {
                    "path": name,
                    "baseline_sha256": digest(before),
                    "current_sha256": digest(after),
                    "tokens": len(old_tokens),
                    "bytes_changed": before != after,
                }
            )
        old_pp = preprocess(baseline_root, command, files[:24])
        new_pp = preprocess(REPO, command, files[:24])
        if tokens(old_pp.decode("utf-8")) != tokens(new_pp.decode("utf-8")):
            raise ValueError("Verilator expanded tokens/tool comments changed")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "baseline.preprocessed.sv").write_bytes(old_pp)
    (args.out / "current.preprocessed.sv").write_bytes(new_pp)
    version = subprocess.run([*command, "--version"], check=True, capture_output=True, text=True)
    result = {
        "status": "PASS",
        "baseline_commit": args.baseline,
        "files": rows,
        "file_count": len(rows),
        "changed_files": sum(r["bytes_changed"] for r in rows),
        "effective_tokens": sum(r["tokens"] for r in rows),
        "token_sha256": digest(json.dumps(after_all, ensure_ascii=False).encode()),
        "baseline_preprocessed_sha256": digest(old_pp),
        "current_preprocessed_sha256": digest(new_pp),
        "verilator": version.stdout.strip(),
        "scope": "Source/token/macro/tool-pragma identity, not formal/STA/analog signoff",
    }
    (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        f"RTL_READABILITY_PASS files={len(rows)} changed={result['changed_files']} "
        f"tokens={result['effective_tokens']}"
    )


if __name__ == "__main__":
    main()
