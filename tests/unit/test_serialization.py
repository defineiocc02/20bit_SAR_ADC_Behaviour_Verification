"""检查标准 JSON 保留布尔类型并显式表示未定义数值。"""

import json

import numpy as np
import pytest

from adi_model.serialization import result_document, write_results


def test_recursive_nonfinite_annotations_and_boolean_verdicts(tmp_path):
    raw = {
        "pipeline": {"PASS": np.bool_(False)},
        "a/b~c": np.array([1, np.nan, np.inf, -np.inf]),
        "scalar": np.array(np.nan),
        "missing": None,
    }
    path = tmp_path / "中文结果.json"
    doc = write_results(path, raw)
    loaded = json.loads(
        path.read_text(encoding="utf-8"), parse_constant=lambda x: pytest.fail(f"nonstandard {x}")
    )
    assert loaded == doc
    assert result_document(doc) == doc
    assert loaded["a/b~c"] == [1, None, None, None]
    assert [r["path"] for r in doc["undefined_numeric_values"]] == [
        "/a~1b~0c/1",
        "/a~1b~0c/2",
        "/a~1b~0c/3",
        "/scalar",
    ]
    assert loaded["pipeline"]["PASS"] is False
    assert type(loaded["pipeline"]["PASS"]) is bool
    assert np.isnan(raw["a/b~c"][1])


def test_malformed_document_does_not_replace_previous_result(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("original", encoding="utf-8")
    with pytest.raises(TypeError):
        write_results(path, {"unsupported": object()})
    assert path.read_text() == "original"
    assert result_document({3: "code index"})["3"] == "code index"
    with pytest.raises(TypeError):
        result_document({(1, 2): "invalid key"})
    with pytest.raises(ValueError, match="collision"):
        result_document({3: "numeric", "3": "string"})
