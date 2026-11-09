# =============================================================================
# 文件名     : test_receipt_resolution.py
# 模块名     : test_receipt_resolution
# 版本       : v1.0
# 功能       : 验证收据相对路径及明确 ZIP 容器，防止误报文件缺失。
# 设计说明   :
#   [独立性] 使用临时人工夹具，不读取真实工程收据作为期望答案。
#   [拒绝] 禁止模糊同名搜索；不同文件发生歧义、声明成员缺失时必须拒绝。
# 变更记录   : 增加文件、容器、歧义和缺失四类定向测试。
# =============================================================================

"""内容引用解析的独立边界测试。"""

import zipfile

import pytest
from tools.verify_receipts import reference_bytes


def test_input_relative_to_receipt(tmp_path):
    """数据相对收据所在目录，不应错误地只在仓库根寻找。"""
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "data.npz").write_bytes(b"input")
    value = reference_bytes(tmp_path, folder / "figures.json", {}, (), "data.npz")
    assert value[0] == b"input"


def test_explicit_zip_member(tmp_path):
    """只读明确 archive.path 的精确成员；不存在的成员不能搜索替代。"""
    with zipfile.ZipFile(tmp_path / "run.zip", "w") as z:
        z.writestr("logs/run.log", b"passed")
    payload = {"archive": {"path": "run.zip"}, "members": {}}
    value = reference_bytes(tmp_path, tmp_path / "run.json", payload, ("members",), "logs/run.log")
    assert value[0] == b"passed"
    assert value[2] is None  # ZIP 字节一致不表示当前源码行为与历史等价。
    assert (
        reference_bytes(tmp_path, tmp_path / "run.json", payload, ("members",), "missing.log")
        is None
    )


def test_handoff_uses_project_root(tmp_path):
    """子工程 handoff 的 SPEC.md 不能误配仓库根同名文件。"""
    folder = tmp_path / "task"
    folder.mkdir()
    (tmp_path / "SPEC.md").write_bytes(b"other")
    (folder / "SPEC.md").write_bytes(b"project")
    value = reference_bytes(
        tmp_path,
        folder / "handoff_manifest.json",
        {"source_sha256": {}},
        ("source_sha256",),
        "SPEC.md",
    )
    assert value[0] == b"project"


def test_ambiguous_local_reference_is_rejected(tmp_path):
    """两处同名文件不同，不能按哪个摘要匹配就选择哪个。"""
    folder = tmp_path / "evidence"
    folder.mkdir()
    (tmp_path / "input.npz").write_bytes(b"root")
    (folder / "input.npz").write_bytes(b"local")
    with pytest.raises(ValueError, match="ambiguous"):
        reference_bytes(tmp_path, folder / "figures.json", {}, (), "input.npz")


def test_missing_reference_stays_missing(tmp_path):
    """没有明确文件或容器时维持缺失，不能通过目录扫描猜测。"""
    assert reference_bytes(tmp_path, tmp_path / "receipt.json", {}, (), "unknown.npz") is None
