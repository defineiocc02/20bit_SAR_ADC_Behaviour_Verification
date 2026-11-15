#!/usr/bin/env python3
"""对显式选定源码进行有限的 ANSI 声明与命名端口集合检查。

调用方必须明确指定源码闭包、顶层和新建的库外输出目录。通配连接
记为 BLOCKED；本工具不检查位宽，不 elaboration HDL，也不证明协议。
真实模拟器或 HDL 检查器仍须独立验证。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def balanced(text: str, start: int) -> int:
    """返回 text[start:] 中匹配的右括号位置。

    调用方须令 start 指向左圆括号；本函数只按字符计数，不词法分析。

    Raises:
        ValueError: 遍历结束仍未匹配到关闭位置。
    """
    depth = 0
    for pos in range(start, len(text)):
        depth += (text[pos] == "(") - (text[pos] == ")")
        if depth == 0:
            return pos
    raise ValueError("unbalanced port/parameter parentheses")


def commas(text: str) -> list[str]:
    """仅按顶层逗号分割端口列表，保留嵌套括号内的逗号。

    输入应已完成注释清理；这是字符级分割，不是 HDL 词法分析。
    """
    items, start, depth = [], 0, 0
    for pos, char in enumerate(text):
        depth += (char in "([{") - (char in ")]}")
        if char == "," and depth == 0:
            items.append(text[start:pos])
            start = pos + 1
    return [*items, text[start:]]


def clean(text: str) -> str:
    """用正则删除注释，供有限的声明与名称检查使用，不代替 HDL 词法器。"""
    return re.sub(r"//[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", text, flags=re.S))


def declarations(text: str) -> dict[str, set[str]]:
    """读取 ANSI 模块声明，返回模块名到命名端口集合的映射。

    本函数不展开宏、不计算位宽；调用方须传入已清理注释的文本。

    Raises:
        ValueError: 声明截断、非 ANSI 端口形式、重复模块或端口、
            缺少方向，或无法提取端口名称。
    """
    found = {}
    for match in re.finditer(r"(?m)^\s*module\s+(\w+)\b", text):
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            raise ValueError(f"truncated module declaration: {match[1]}")
        if text[pos] == "#":
            pos = text.index("(", pos)
            pos = balanced(text, pos) + 1
            while pos < len(text) and text[pos].isspace():
                pos += 1
        if pos >= len(text):
            raise ValueError(f"truncated module ports: {match[1]}")
        if text[pos] == ";":
            if match[1] in found:
                raise ValueError(f"duplicate module {match[1]}")
            found[match[1]] = set()
            continue
        if text[pos] != "(":
            raise ValueError(f"unsupported non-ANSI module declaration: {match[1]}")
        end = balanced(text, pos)
        ports, direction = set(), None
        for item in commas(text[pos + 1 : end]):
            item = item.strip()
            if not item:
                continue
            dmatch = re.match(r"(input|output|inout)\b", item)
            if dmatch:
                direction = dmatch[1]
            if not direction:
                raise ValueError(f"missing ANSI port direction in {match[1]}")
            item = re.sub(r"\[[^\]]*\]", " ", item).split("=", 1)[0]
            names = re.findall(r"\b[A-Za-z_]\w*\b", item)
            if not names:
                raise ValueError(f"empty port declaration in {match[1]}")
            if names[-1] in ports:
                raise ValueError(f"duplicate port {names[-1]} in {match[1]}")
            ports.add(names[-1])
        if match[1] in found:
            raise ValueError(f"duplicate module {match[1]}")
        found[match[1]] = ports
    return found


def main() -> int:
    """检查显式源码的命名端口集合，将结果写入新建的库外目录。

    输出状态仅为有限静态名称检查结果；通配连接记为 BLOCKED，
    不将其计为已检查连接。输出目录必须事先不存在。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo_root.resolve(), args.out.resolve()
    if out.exists() or out.is_relative_to(repo):
        parser.error("--out must be a new directory outside the source repository")
    errors, blocked, texts, modules, hashes = [], [], {}, {}, {}
    try:
        for name in args.sources:
            path = Path(name) if Path(name).is_absolute() else repo / name
            if path.stat().st_size > 300 * 1024**2:
                raise ValueError(f"file exceeds 300 MiB limit: {name}")
            data = path.read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            text = clean(data.decode("utf-8"))
            texts[name] = text
            for module, ports in declarations(text).items():
                if module in modules:
                    raise ValueError(f"module declared in multiple selected files: {module}")
                modules[module] = ports
    except (OSError, UnicodeError, ValueError) as error:
        errors.append(str(error))
    if args.top not in modules:
        errors.append(f"selected top is not declared: {args.top}")
    if len(modules) < 2:
        errors.append("fewer than two selected module declarations; no DUT/bench closure")
    instances = comparisons = 0
    for name, text in texts.items():
        for module, ports in modules.items():
            for match in re.finditer(rf"\b{re.escape(module)}\b", text):
                if re.search(r"\bmodule\s*$", text[max(0, match.start() - 16) : match.start()]):
                    continue
                pos = match.end()
                while pos < len(text) and text[pos].isspace():
                    pos += 1
                if pos < len(text) and text[pos] == "#":
                    pos = text.index("(", pos)
                    pos = balanced(text, pos) + 1
                instance = re.match(r"\s*(\w+)\s*\(", text[pos:])
                if not instance:
                    continue
                pos += instance.end() - 1
                body = text[pos + 1 : balanced(text, pos)]
                instances += 1
                if re.search(r"\.\s*\*", body):
                    blocked.append(
                        f"{name}:{module}.{instance[1]} wildcard needs actual elaboration"
                    )
                    continue
                connections = re.findall(r"\.(\w+)\s*\(", body)
                if len(connections) != len(set(connections)):
                    errors.append(f"duplicate named connections: {module}.{instance[1]}")
                comparisons += len(connections)
                unknown, missing = set(connections) - ports, ports - set(connections)
                if unknown or missing:
                    errors.append(
                        f"{name}:{module}.{instance[1]} unknown={sorted(unknown)} missing={sorted(missing)}"
                    )
    if not instances or (not comparisons and not blocked):
        errors.append("no nonempty explicit instance/port comparisons")
    status = "FAIL" if errors else ("BLOCKED" if blocked else "STATIC_NAMES_VALID")
    payload = {
        "scope": "Selected ANSI module declarations and explicit named-port sets only",
        "status": status,
        "module_count": len(modules),
        "instance_count": instances,
        "port_comparisons": comparisons,
        "errors": errors,
        "blocked": blocked,
        "source_sha256": hashes,
        "widths_elaboration_protocol": "NOT_ASSESSED",
        "model_RTL_simulation_executed": False,
    }
    out.mkdir(parents=True, exist_ok=False)
    (out / "static_check.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload))
    return 1 if errors else (2 if blocked else 0)


if __name__ == "__main__":
    raise SystemExit(main())
