#!/usr/bin/env python3
"""检查指定冻结 RTL 源集的词法单元、指令及工具注释是否保持一致。

有限词法器只覆盖本仓库使用的 SV 子集，不是形式化 HDL 检查器。工具
可按显式选项调用 Verilator 预处理，分别比较包含文件和宏展开后的词法单元。
遇到未知字符或不支持的续行指令即报错，不生成等价结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

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
    # 换行终止指令或宏；对这里支持的有限源集，
    # 逐项保留指令词法单元，并拒绝续行形式。
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


def _relative(name: str, *, include_root: bool = False) -> Path:
    path = Path(name)
    if include_root and name == ".":
        return path
    if path.is_absolute() or not path.parts or any(p in {".", ".."} for p in path.parts):
        raise ValueError(f"files/includes must be explicit relative paths: {name}")
    return path


def _read(root: Path, name: str) -> bytes:
    path = (root / _relative(name)).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size >= 300 * 1024**2:
        raise ValueError(f"declared input missing, outside its root, or too large: {name}")
    return path.read_bytes()


def _new_output(path: Path, input_roots: tuple[Path, ...]) -> Path:
    if path.expanduser().exists() or path.expanduser().is_symlink():
        raise ValueError("output path already exists")
    out = path.expanduser().resolve()
    if (
        out.exists()
        or any(out.is_relative_to(root) for root in input_roots)
        or any((p / ".git").exists() for p in (out, *out.parents))
    ):
        raise ValueError("output must be a new directory outside Git working trees")
    out.mkdir(parents=True, exist_ok=False)
    return out


def _git_commit(root: Path, reference: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", reference + "^{commit}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", result):
        raise ValueError("baseline did not resolve to one immutable commit")
    return result


def git_bytes(root: Path, commit: str, path: str) -> bytes:
    """Read a declared path from an already resolved immutable Git commit."""
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=root, check=True, capture_output=True
    ).stdout


def preprocess(
    root: Path, executable: str, files: list[str], includes: list[str], defines: list[str]
) -> bytes:
    """Run explicitly requested Verilator preprocessing on declared snapshots only."""
    sources = [name for name in files if Path(name).suffix in {".sv", ".v"}]
    if not sources:
        raise ValueError("preprocessing requires at least one declared .sv/.v source")
    result = subprocess.run(
        [
            executable,
            "-E",
            "-P",
            "--pp-comments",
            *[f"-I{x}" for x in includes],
            *[f"-D{x}" for x in defines],
            *sources,
        ],
        cwd=root,
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(
            f"preprocessor rc={result.returncode}; diagnostic={result.stderr.decode(errors='backslashreplace')}"
        )
    return result.stdout


def main(argv=None) -> int:
    """Compare an explicit nonempty source set; never substitute a device profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--before-root", type=Path)
    origin.add_argument("--baseline", help="Git reference resolved to an immutable commit")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument(
        "--files", nargs="+", required=True, help="Exact relative source/header paths"
    )
    parser.add_argument("--include-dirs", nargs="*")
    parser.add_argument("--defines", nargs="*", default=[])
    parser.add_argument("--verilator", help="Optional existing executable; no PATH/tool default")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = None
    result = {
        "schema_version": 1,
        "status": "VALIDATING",
        "scope": "FINITE_SV_TOKEN_IDENTITY_NOT_SYNTAX_FORMAL_OR_TIMING_PROOF",
        "preprocessing": "NOT_REQUESTED",
        "files": [],
    }
    try:
        candidate = args.candidate_root.expanduser().resolve()
        before = args.before_root.expanduser().resolve() if args.before_root else None
        if (
            not candidate.is_dir()
            or before is not None
            and (not before.is_dir() or before == candidate)
        ):
            raise ValueError("candidate and optional baseline must be distinct existing roots")
        names = [str(_relative(name)) for name in args.files]
        if len(names) != len(set(names)) or any(
            Path(name).suffix not in {".sv", ".v", ".vh", ".svh"} for name in names
        ):
            raise ValueError("declare unique SV/Verilog source or header paths")
        includes = [str(_relative(name, include_root=True)) for name in (args.include_dirs or [])]
        if args.verilator and args.include_dirs is None:
            raise ValueError("requested preprocessing requires an explicit --include-dirs list")
        if any(
            not re.fullmatch(r"[A-Za-z_]\w*(?:=[A-Za-z0-9_+'.-]+)?", value)
            for value in args.defines
        ):
            raise ValueError("unsupported explicit define encoding")
        commit = _git_commit(candidate, args.baseline) if args.baseline else None
        result.update(
            {
                "baseline_commit": commit,
                "before_root": str(before) if before else None,
                "candidate_root": str(candidate),
                "include_dirs": includes,
                "defines": args.defines,
            }
        )
        out = _new_output(args.out, (candidate, before) if before is not None else (candidate,))
        payloads = {}
        token_streams = []
        for name in names:
            old = _read(before, name) if before is not None else git_bytes(candidate, commit, name)
            new = _read(candidate, name)
            if len(old) >= 300 * 1024**2:
                raise ValueError("baseline input exceeds 300 MiB")
            left, left_directives = tokens(old.decode("utf-8"))
            right, right_directives = tokens(new.decode("utf-8"))
            row = {
                "path": name,
                "baseline_sha256": digest(old),
                "candidate_sha256": digest(new),
                "tokens": len(left),
                "tokens_equal": left == right,
                "directives_equal": left_directives == right_directives,
                "bytes_changed": old != new,
            }
            result["files"].append(row)
            if not left or not right or left != right or left_directives != right_directives:
                raise ValueError(f"empty or changed declared token/directive stream: {name}")
            payloads[name] = (old, new)
            token_streams.append(right)
        for name, (old, new) in payloads.items():
            for label, data in (("baseline", old), ("candidate", new)):
                target = out / "input_snapshot" / label / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        if args.verilator:
            import shutil

            result["preprocessing"] = "REQUESTED_NOT_STARTED"
            executable = shutil.which(args.verilator)
            if executable is None:
                raise ValueError("explicit Verilator executable not found; no fallback performed")
            result["preprocessing"] = "REQUESTED_NOT_COMPLETED"
            with tempfile.TemporaryDirectory(prefix="rtl_identity_") as temporary:
                snapshot = Path(temporary)
                for name, (old, new) in payloads.items():
                    for label, data in (("before", old), ("candidate", new)):
                        target = snapshot / label / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                for label in ("before", "candidate"):
                    for directory in includes:
                        (snapshot / label / directory).mkdir(parents=True, exist_ok=True)
                old_pp = preprocess(snapshot / "before", executable, names, includes, args.defines)
                new_pp = preprocess(
                    snapshot / "candidate", executable, names, includes, args.defines
                )
            (out / "baseline.preprocessed.sv").write_bytes(old_pp)
            (out / "candidate.preprocessed.sv").write_bytes(new_pp)
            if not tokens(old_pp.decode("utf-8"))[0] or tokens(old_pp.decode("utf-8")) != tokens(
                new_pp.decode("utf-8")
            ):
                raise ValueError("expanded token/directive streams differ or are empty")
            result.update(
                {
                    "preprocessing": "DECLARED_SNAPSHOTS_TOKEN_IDENTITY_CONFIRMED",
                    "preprocessor": executable,
                    "baseline_preprocessed_sha256": digest(old_pp),
                    "candidate_preprocessed_sha256": digest(new_pp),
                }
            )
        if any(
            digest(_read(candidate, name)) != row["candidate_sha256"]
            for name, row in zip(names, result["files"], strict=True)
        ):
            raise RuntimeError("candidate input changed while identity was checked")
        result.update(
            {
                "status": "DECLARED_TOKEN_IDENTITY_CONFIRMED",
                "file_count": len(result["files"]),
                "effective_tokens": sum(x["tokens"] for x in result["files"]),
                "token_sha256": digest(json.dumps(token_streams).encode()),
            }
        )
        (out / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"RTL_TOKEN_IDENTITY_COMPLETE files={len(names)} tokens={result['effective_tokens']} preprocessing={result['preprocessing']} formal=NOT_ASSESSED"
        )
        return 0
    except (
        OSError,
        UnicodeError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        if out is not None:
            result.update({"status": "FAILED", "error": str(error)})
            (out / "result.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        print(f"rtl_readability: {error}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
