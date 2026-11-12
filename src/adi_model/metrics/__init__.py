"""指标分析子包：只读已产生的电压/电荷记录，不修改仿真数据。

兼容说明
--------
迁移前 `adi_model.metrics` 是**模块**，调用方写 `from adi_model import metrics`
再取 `metrics.inl_from_mean_error(...)`。迁移后同名路径变成了**包**，直接
`from adi_model import metrics` 会拿到空包。

这里保留一层薄适配：把 `adi_model.metrics.metrics` 的公开名字重新导出到包
命名空间，因此旧的公开路径继续可用；新代码应优先写
`from adi_model.metrics.metrics import ...`。

这只是路径兼容，不是第二套实现：公式仍只有 `metrics.py` 一处。
"""

from adi_model.metrics.metrics import *  # noqa: F401,F403
