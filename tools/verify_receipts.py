# =============================================================================
# 文件名     : verify_receipts.py
# 模块名     : tools.verify_receipts
# 版本       : v1.0
# 功能       : 逐条复核收据里记录的文件摘要，区分「字节变了」与「语义变了」
#
# 设计说明   :
#   * 收据里的 source_sha256 记的是「当时的字节」。注释改造、格式化、换行风格
#     都会让它失配，但功能可能一字未动。只报「失配」等于把所有改动混为一谈。
#   * 本工具因此报三种状态：PASS（字节一致）、STALE_BYTES（字节不一致，
#     再用 semantic_digest 判断语义是否与 HEAD 一致）、MISSING（文件不存在）。
#   * 判定语义时需要参照物。收据本身没存语义摘要，所以参照物取 git HEAD：
#     只有当收据是在 HEAD 上生成时，「语义与 HEAD 一致」才等价于「语义没变」。
#     这一点写在输出的 note 字段里，不当作无条件结论。
#   * 不修改任何收据。工具只读，失配要由人决定是重签还是回退。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""复核收据中的文件摘要，区分字节改动与语义改动。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adi_model.hashing import semantic_digest

HEX = set("0123456789abcdef")
TEXT_SUFFIX = {".py", ".sv", ".v", ".svh", ".vh"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}


def is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.lower()) <= HEX


def looks_like_path(key: str) -> bool:
    return bool(Path(key).suffix)


def reference_bytes(root: Path, receipt: Path, payload: dict, trail: tuple, rel: str):
    """按收据声明的容器读取引用，返回字节和来源；不解压、不扫描同名文件。

    members、member_sha256、files 只能从最近一层明确声明的 archive.path
    读取精确成员。handoff_manifest 的源码路径相对其项目目录，其他本地
    引用只尝试仓库根与收据所在目录；两者均存在且不同则拒绝猜测。
    没有显式容器或精确文件时返回 None，不能靠匹配摘要反向寻找一个绿结果。

    Args:
        root: 仓库绝对根目录，所有本地引用限于此目录内。
        receipt: 当前 JSON 收据的绝对路径。
        payload: 完整收据，用于寻找当前字段最近的 archive 声明。
        trail: 从根到摘要字段的键序列，列表项使用字符串下标。
        rel: 被引用的相对文件名或 ZIP 精确成员名。
    Returns:
        文件字节、可引用的位置和本地文件路径；ZIP 成员路径为 None。
        引用缺失返回 None，调用方必须保留缺失状态。
    Raises:
        ValueError: 仓库根和收据目录存在不同的同名文件，无法确定引用。

    """
    levels = [payload]
    for part in trail:
        node = levels[-1]
        levels.append(node[int(part)] if isinstance(node, list) else node[part])
    if trail and trail[-1] in {"members", "member_sha256", "files"}:
        for node in reversed(levels[:-1]):
            archive = node.get("archive") if isinstance(node, dict) else None
            if not isinstance(archive, dict) or not archive.get("path"):
                continue
            archive_path = Path(archive["path"])
            location = (
                root / archive_path
                if (root / archive_path).is_file()
                else receipt.parent / archive_path
            )
            if not location.is_file() or not location.resolve().is_relative_to(root):
                return None
            with zipfile.ZipFile(location) as container:
                if rel not in container.namelist():
                    return None
                return container.read(rel), f"{location.relative_to(root)}!{rel}", None
    if receipt.name == "handoff_manifest.json" and trail and trail[-1] == "source_sha256":
        candidates = [receipt.parent / rel]
    else:
        candidates = list(dict.fromkeys([root / rel, receipt.parent / rel]))
    found = [p.resolve() for p in candidates if p.is_file() and p.resolve().is_relative_to(root)]
    if not found:
        return None
    if len(found) > 1 and found[0].read_bytes() != found[1].read_bytes():
        raise ValueError(f"ambiguous receipt reference: {receipt.relative_to(root)} -> {rel}")
    return found[0].read_bytes(), str(found[0].relative_to(root)), found[0]


def walk(node: object, trail: tuple[str, ...] = ()):
    """产出 (键路径, 路径字符串, 记录的摘要)。只认「路径 -> 摘要」的字典。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if is_digest(value) and isinstance(key, str) and looks_like_path(key):
                yield trail, key, value.lower()
            else:
                yield from walk(value, (*trail, str(key)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk(item, (*trail, str(index)))


def head_text(root: Path, rel: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{rel}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def head_semantic(root: Path, rel: str) -> str | None:
    text = head_text(root, rel)
    if text is None:
        return None
    suffix = Path(rel).suffix
    if suffix not in TEXT_SUFFIX:
        return hashlib.sha256(text.encode()).hexdigest()
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as handle:
        handle.write(text)
        temp = Path(handle.name)
    try:
        return semantic_digest(temp)
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    """--root 指向需审查的工作树，--json 可保存逐条结果，--quiet 只压缩显示。

    --root 指向需审查的工作树，--json 可保存逐条结果，--quiet 只压缩显示。
    MISSING 是当前树缺文件，不自动否定历史运行；STALE_BYTES 也不自动等于行为改变。
    语义比较的参照是当前 HEAD，不能冒充收据生成时的未知源码。
    退出码只对 MISSING 判非零，调用方不能把零退出码解释为所有收据当前有效。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", type=Path, default=None, help="把逐条结果写成 JSON")
    parser.add_argument("--quiet", action="store_true", help="只打印汇总")
    args = parser.parse_args()
    root = args.root.resolve()

    records = []
    for path in sorted(root.rglob("*.json")):
        if SKIP_DIRS & set(path.parts):
            continue
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for trail, rel, digest in walk(payload):
            records.append((path.relative_to(root), trail, rel, digest, payload))

    rows = []
    for receipt, trail, rel, digest, payload in records:
        resolved = reference_bytes(root, root / receipt, payload, trail, rel)
        trail = ".".join(trail)
        if resolved is None:
            rows.append(
                {
                    "receipt": str(receipt),
                    "field": trail,
                    "path": rel,
                    "status": "MISSING",
                    "recorded": digest,
                }
            )
            continue
        content, location, target = resolved
        if hashlib.sha256(content).hexdigest() == digest:
            rows.append(
                {
                    "receipt": str(receipt),
                    "field": trail,
                    "path": rel,
                    "status": "PASS",
                    "resolved": location,
                    "recorded": digest,
                }
            )
            continue
        current = (
            semantic_digest(target) if target is not None and target.suffix in TEXT_SUFFIX else None
        )
        reference = (
            head_semantic(root, str(target.relative_to(root))) if current is not None else None
        )
        if current is None or reference is None:
            verdict = "unknown"
        else:
            verdict = "same" if current == reference else "different"
        rows.append(
            {
                "receipt": str(receipt),
                "field": trail,
                "path": rel,
                "status": "STALE_BYTES",
                "recorded": digest,
                "semantics_vs_head": verdict,
            }
        )

    counts: dict[str, int] = {}
    for row in rows:
        key = row["status"]
        if row["status"] == "STALE_BYTES":
            key = f"STALE_BYTES/{row['semantics_vs_head']}"
        counts[key] = counts.get(key, 0) + 1

    print(f"收据条目 {len(rows)} 条，涉及 {len({r['receipt'] for r in rows})} 份收据")
    for key in sorted(counts):
        print(f"  {key:28s} {counts[key]}")
    print("  注：STALE_BYTES 的语义判定以 git HEAD 为参照物，仅当收据生成于 HEAD 时成立。")

    if not args.quiet:
        for row in rows:
            if row["status"] != "PASS":
                extra = row.get("semantics_vs_head", "")
                print(f"  {row['status']:12s} {extra:9s} {row['path']}  <- {row['receipt']}")
    if args.json is not None:
        args.json.write_text(
            json.dumps({"counts": counts, "rows": rows}, ensure_ascii=False, indent=2) + "\n"
        )
    return 0 if not counts.get("MISSING") else 1


if __name__ == "__main__":
    raise SystemExit(main())
