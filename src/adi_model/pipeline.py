"""相位流水线的公开入口，物理实现位于 pipeline_engine。

采集电荷绑定样本及其物理 slice 组。首笔显式预充，转换不消费其他组的采样电荷。
"""

from __future__ import annotations

from .pipeline_engine import execute_split
from .sampler import input_derivative


def run_pipeline(
    cfg,
    input_fn,
    n_samples,
    chip=None,
    state=None,
    rng=None,
    scheduler=None,
    *,
    pool=None,
    ra_output_error_v=None,
):
    """运行具有显式电容归属的物理 split 流水线。

    Args:
        cfg: 配置；入口按 split 拓扑解析。
        input_fn: 输入电压函数，时间单位 s，输出单位 V。
        n_samples: 首笔预充之后的输出样本数。
        chip: 可选的 aggregate split 制造实现。
        state: 可选的冻结数字系数状态。
        rng: 可选的采样/噪声随机流。
        scheduler: 可选的兼容采集调度器。
        pool: 可选的已有 PhysicalSlicePool；仅重置电气状态。
        ra_output_error_v: 可选的 raw 长度有限误差流 [RA 输出 V]，在原有钳位及 ADC2 量化前注入。
            仅支持调用方定义来源的样本域误差；None 保留既有行为。

    Returns:
        SimResult，含电荷、样本 ID、实际转换 slice 及误差。

    Side effects:
        重置 pool 电气状态，并消费启用的采样/噪声随机流。
        显式误差不改变噪声预算、钳位规则或随机数消费序列。
    """
    return execute_split(
        cfg,
        input_fn,
        n_samples,
        chip=chip,
        state=state,
        rng=rng,
        scheduler=scheduler,
        pool=pool,
        derivative_fn=input_derivative,
        ra_output_error_v=ra_output_error_v,
    )
