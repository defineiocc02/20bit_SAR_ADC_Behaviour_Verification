"""CAAZ 扩展测试的 fixture 桥接。

测试搬到 `extensions/` 后不再自动加载 `tests/conftest.py`；这里显式复用
主测试的显式合成输入夹具，避免复制两份参数。
"""

from tests.conftest import explicit_format, explicit_pipeline_factory  # noqa: F401
