"""带格式版本的 UTF-8 结果文档，显式登记未定义数值。"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np


def result_document(results: dict) -> dict:
    """转换 NumPy 数值，并将每个 NaN/无穷值显式登记为 null。

    这里只做序列化，不作验收：失败门禁仍保持失败，未定义字段不会变成虚构的有限数值。
    路径采用 JSON Pointer 转义；不修改原内存结果。
    """
    undefined = (
        list(results.get("undefined_numeric_values", []))
        if results.get("result_schema_version") == 2
        else []
    )

    def convert(value, path):
        if isinstance(value, np.ndarray):
            return convert(value.tolist(), path)
        if isinstance(value, np.generic):
            return convert(value.item(), path)
        if isinstance(value, float) and not math.isfinite(value):
            kind = "nan" if math.isnan(value) else ("+infinity" if value > 0 else "-infinity")
            undefined.append({"path": path, "kind": kind, "status": "undefined_numeric_value"})
            return None
        if isinstance(value, dict):
            converted = {}
            for key, item in value.items():
                if isinstance(key, np.generic):
                    key = key.item()
                if isinstance(key, str):
                    name = key
                elif type(key) in (int, float) and math.isfinite(key):
                    name = str(key)
                else:
                    raise TypeError(f"unsupported JSON object key at {path}: {key!r}")
                if name in converted:
                    raise ValueError(f"JSON key normalization collision at {path}/{name}")
                converted[name] = convert(
                    item, path + "/" + name.replace("~", "~0").replace("/", "~1")
                )
            return converted
        if isinstance(value, list | tuple):
            return [convert(v, path + "/" + str(i)) for i, v in enumerate(value)]
        return value

    out = convert(results, "")
    out["result_schema_version"] = 2
    out["undefined_numeric_values"] = undefined
    return out


def write_results(path, results: dict) -> dict:
    """原子写入标准 UTF-8 JSON，并返回序列化后的文档。"""
    destination = Path(path)
    document = result_document(results)
    payload = json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(payload)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    return document
