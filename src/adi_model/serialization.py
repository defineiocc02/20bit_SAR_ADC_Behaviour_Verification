"""Versioned UTF-8 result documents with explicit undefined-number metadata."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np


def result_document(results: dict) -> dict:
    """Convert NumPy values and annotate every NaN/infinity as an explicit null.

    This is serialization, not acceptance: failed gates remain failed, and
    undefined numeric fields cannot become fabricated finite values. Paths use
    JSON Pointer escaping. The source in-memory result is not modified.
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
    """Atomically write standard UTF-8 JSON and return the serialized document."""
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
