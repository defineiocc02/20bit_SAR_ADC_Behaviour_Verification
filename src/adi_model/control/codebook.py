# =============================================================================
# 文件名     : codebook.py
# 模块名     : task1.model.codebook
# 版本       : v1.0
# 功能       : 把命令码与 DEM 相位翻成物理选通位图，并求 Fig.11 名义 DAC 阈值。
#
# 设计说明   :
#   * 依赖方向为 task1.model → adi_model。位图映射用 Fig.11 的电容拓扑，属于模型层，
#     因此不能放进 adi_model（库层不得反向依赖任务层）。
#   * 码本用独立电荷参考求解，不复用被测映射器的查表结果，否则求出的阈值与被测量同源，
#     名义重构闭合检查会退化成自证。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# [归位] map_record / codebook 从 task1/experiments/run_effects.py 上移。二者是模型层
#        能力（被 10 个模块引用），原位置迫使 task2 的模型反向依赖实验脚本。
# =============================================================================

"""命令码与 DEM 相位到物理选通位图的映射，以及名义 DAC 阈值码本。"""

from __future__ import annotations

import numpy as np

from adi_model.capacitors import (
    GROUP_SLICES,
    Capacitors,
    ExperimentConfig,
    feedback_charge,
    voltage_equivalent,
)
from .fig11_dem import map_fig11


def map_record(
    codes: np.ndarray,
    phase: tuple[np.ndarray, np.ndarray],
    *,
    dem_enable: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """逐笔生成 (main, sub) 选通位图，位序为 slot 内 unit 编号。

    codes 为 9 位命令码；phase 是采集时冻结的 (horizontal, vertical) 相位。
    DEM 关闭时相位仍参与映射，保证同一笔在开/关两组的电容底板一致，
    差异只来自 DEM 的选列策略，否则两组对照不成立。
    映射器拒绝该码时抛 RuntimeError 而不是跳过——静默丢样会让统计分母悄悄变小。
    """
    main = np.empty((len(codes), GROUP_SLICES), dtype=np.uint32)
    sub = np.empty((len(codes), GROUP_SLICES), dtype=np.uint8)
    for sample_id, (code, h, v) in enumerate(zip(codes, *phase, strict=True)):
        mask = map_fig11(int(code), dem_enable, int(h), int(v))
        if not mask.valid:
            raise RuntimeError(f"valid stimulus rejected at sample {sample_id}")
        main[sample_id] = mask.main
        sub[sample_id] = mask.sub
    return main, sub


def codebook(nominal: Capacitors, cfg: ExperimentConfig) -> np.ndarray:
    """求 512 个命令码对应的名义 DAC 阈值电压，单位 V，单调递增。

    做法：把输入钉在负满量程，用名义电容算反馈电荷，再折算成输入等效电压，
    得到该码的阈值 vin - Δv。末码阈值必须仍低于正满量程，否则最高码没有合法输入区间。
    """
    codes = np.arange(512, dtype=np.uint16)
    zero_phase = np.zeros(len(codes), dtype=np.uint8)
    plan = np.tile(np.arange(GROUP_SLICES, dtype=np.int8), (len(codes), 1))
    vin = np.full(len(codes), -cfg.full_scale_peak_v)
    mask = map_record(codes, (zero_phase, zero_phase), dem_enable=False)
    thresholds = vin - voltage_equivalent(feedback_charge(nominal, vin, plan, mask, cfg), cfg)
    if not np.all(np.diff(thresholds) > 0) or thresholds[-1] >= cfg.full_scale_peak_v:
        raise RuntimeError("invalid Fig.11 nominal thresholds")
    return thresholds
