# =============================================================================
# 文件名     : capacitors.py
# 模块名     : adi_model.capacitors
# 版本       : v1.0
# 功能       : 电容阵列常量、实验配置与电荷域计算的唯一真源。
#
# 设计说明   :
#   * 本模块只做纯计算，不 import 任何 task* 模块，因此可被各任务反向依赖而不会成环。
#   * 电容权重与片数在这里定义一次；scheduler、run_effects、fixed 等只引用，不再各自
#     复制字面量。改工艺参数只需要改这一处。
#   * 失配抽样依赖 PCG64 的抽取顺序（main → sub → bridge），顺序一变数值就变；
#     任何改动都必须先跑对照，不能只看形状对不对。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# [归位] 从 task1/experiments/run_effects.py 上移。原位置是实验脚本，却被 18 个模块
#        引用，形成「模型依赖实验」的反向依赖；此处为库层，方向为正。
# =============================================================================

"""电容阵列常量、实验配置与电荷域计算的唯一真源。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from adi_model.charge_ref import ref_ra_charge_nodal
from adi_model.noise import NoiseConfig

# -----------------------------------------------------------------------------
# 架构常量
# -----------------------------------------------------------------------------
# 18 片物理资源池，每笔取其中 8 片：上一笔的 8 片在转换，本笔 8 片在采样，余 2 片空闲。
# 这两个数字出现在调度、掩码、重构、物理约束与验证计划里，必须只有一处定义。
POOL_SLICES = 18
GROUP_SLICES = 8

# 主阵列 15 个 2C + 2 个 1C（共 32C）；子阵列 8C/4C/2C/1C/1C，末个 1C 恒接低轨。
MAIN_WEIGHTS = (2,) * 15 + (1, 1)
SUB_WEIGHTS = (8, 4, 2, 1, 1)
# 可切换特征去掉恒接低轨的末位，故是 4 项而非 5 项——这是设计事实，不是笔误。
SUB_FEATURES = SUB_WEIGHTS[:-1]

# 桥接电容 1C。与零寄生假设共同给出子阵列对主阵列的耦合系数 β=1/17。
# 放在这里而非 fig11_dem，是因为 cap_pool 建桥接电容时就要用它；
# 若留在 task1 则本模块必须反向 import，违反分层约定。
BRIDGE_CAP_UNITS = 1

# DEM 的默认种子。放在库层是因为 ExperimentConfig 与 module1 都要用它，
# 若留在 module1 则本模块必须反向 import task1，违反上面的分层约定。
DEFAULT_DEM_SEED = 0x9E3779B9


@dataclass(frozen=True)
class ExperimentConfig:
    """一次实验的激励、失配样本与调度种子。

    单位约定：电压为 V、电容为 F、时间为 s、频率为 Hz。所有派生的误差与噪声
    都折算到 ADC 输入端，不保留内部节点量纲。
    """

    fs_hz: int = 10_000_000
    samples: int = 4096
    tone_bin: int = 409
    tone_peak_v: float = 10.0 / 3.0
    full_scale_peak_v: float = 5.0
    skew_s: float = 5e-12
    unit_cap_f: float = 1e-15
    mismatch_sigma_unit: float = 0.001
    manufacturing_seed: int = 100
    schedule_seed: int = 0x243F6A88
    dem_seed: int = DEFAULT_DEM_SEED
    static_repeats: int = 128
    # 噪声源。默认全关，所以引入这个字段不改变任何既有实验的数值。
    noise: NoiseConfig = field(default_factory=NoiseConfig)

    def __post_init__(self) -> None:
        """把从 JSON 读回的 noise 字典还原成 NoiseConfig。

        归档里存的是 ``asdict(experiment)``，noise 那一项是嵌套字典；
        调用方习惯用 ``ExperimentConfig(**meta["experiment"])`` 还原，
        若不在这里转回来，``cfg.noise.enabled`` 就会在字典上取属性而炸。
        这条转换只在「传入的是映射」时生效，正常构造不受影响。
        """
        if isinstance(self.noise, dict):
            object.__setattr__(self, "noise", NoiseConfig(**self.noise))

    @property
    def fin_hz(self) -> float:
        """相干激励频率，单位 Hz。

        tone_bin 是整数频点，因此 fin 落在 FFT 栅格上，不需要额外加窗修正泄漏；
        改成非整数频点会同时改变 SNDR 与 SFDR 的口径。
        """
        return self.tone_bin * self.fs_hz / self.samples


@dataclass(frozen=True)
class Capacitors:
    """按物理片编号排列的主、子及桥电容，单位 F。

    main 形状 (POOL_SLICES, 17)，sub 形状 (POOL_SLICES, 5)，bridge 形状 (POOL_SLICES,)。
    数组按物理片编号索引，不按 slot；slot 到物理片的映射由调度计划给出。
    """

    main: np.ndarray
    sub: np.ndarray
    bridge: np.ndarray


def cap_pool(cfg: ExperimentConfig) -> tuple[Capacitors, Capacitors]:
    """构造名义网络与同一制造样本，返回 (nominal, actual)。

    失配模型：每个单位电容独立高斯，标准差按面积平方根缩放
    （sigma = mismatch_sigma_unit * sqrt(C)），即绝对失配随电容值增大。
    同一 seed 下 nominal 与 actual 成对生成，两组实验必须复用同一 actual 才可比。
    """
    rng = np.random.Generator(np.random.PCG64(cfg.manufacturing_seed))
    main = np.tile(MAIN_WEIGHTS, (POOL_SLICES, 1)) * cfg.unit_cap_f
    sub = np.tile(SUB_WEIGHTS, (POOL_SLICES, 1)) * cfg.unit_cap_f
    bridge = np.full(POOL_SLICES, BRIDGE_CAP_UNITS * cfg.unit_cap_f)

    def mismatch(cap: np.ndarray) -> np.ndarray:
        # 单元面积与名义电容成正比，独立并联单位电容的绝对失配按面积平方根增长。
        sigma_f = cfg.mismatch_sigma_unit * np.sqrt(cap * cfg.unit_cap_f)
        return cap + rng.normal(0.0, sigma_f)

    # 抽取顺序固定为 main → sub → bridge；调换顺序会改变同一 seed 下的失配样本。
    nominal = Capacitors(main, sub, bridge)
    actual = Capacitors(mismatch(main), mismatch(sub), mismatch(bridge))
    return nominal, actual


def unpack(mask: np.ndarray, unit_count: int) -> np.ndarray:
    """把打包的高参考选通位图展开成逐电容布尔数组，末轴为 unit 编号。

    位序约定：bit k 对应该阵列第 k 个单位电容，低位在前。掩码按 slot 打包，
    调用方负责先把物理片编号映射到 slot。
    """
    bit = np.arange(unit_count, dtype=mask.dtype)
    return ((mask[..., None] >> bit) & 1).astype(bool)


def feedback_charge(
    pool: Capacitors,
    vin: np.ndarray,
    plan: np.ndarray,
    masks: tuple[np.ndarray, np.ndarray],
    cfg: ExperimentConfig,
) -> np.ndarray:
    """按实际物理电容与底板电压求每笔反馈电荷，单位 C。

    逐片解一次两节点方程：主阵列底板采 VIN，子阵列底板固定低参考，
    转换阶段按掩码把各底板切到高或低参考。桥电容 Cb=C、寄生为零。
    返回形状与 vin 相同，逐笔累加该笔 8 片的贡献。
    """
    low_v = -cfg.full_scale_peak_v
    high_v = cfg.full_scale_peak_v
    main_mask, sub_mask = masks
    charge = np.zeros(len(vin))
    for physical_id in range(POOL_SLICES):
        sample, slot = np.nonzero(plan == physical_id)
        if not sample.size:
            continue
        main_cap = pool.main[physical_id]
        sub_cap = pool.sub[physical_id]
        main_select = unpack(main_mask[sample, slot], len(main_cap))
        sub_select = unpack(sub_mask[sample, slot], len(sub_cap))
        # 主底板采 VIN，子底板固定低参考。逐片的独立两节点方程接收真实位图。
        charge[sample] += ref_ra_charge_nodal(
            main_cap_f=main_cap,
            sub_cap_f=sub_cap,
            bridge_cap_f=pool.bridge[physical_id],
            sub_parasitic_f=0.0,
            feedback_cap_f=cfg.unit_cap_f,
            sample_main_v=np.broadcast_to(vin[sample, None], main_select.shape),
            sample_sub_v=np.full(len(sub_cap), low_v),
            conversion_main_v=np.where(main_select, high_v, low_v),
            conversion_sub_v=np.where(sub_select, high_v, low_v),
        )
    return charge


def voltage_equivalent(charge: np.ndarray, cfg: ExperimentConfig) -> np.ndarray:
    """把反馈电荷折算成输入等效电压，单位 V。

    分母取 8 片 × 主阵列名义总电容（32C），即采样时刻的等效采样电容。
    注意这是名义值：用实际失配电容算出的电荷除以名义电容，残差即失配误差本身。
    """
    return charge / (GROUP_SLICES * sum(MAIN_WEIGHTS) * cfg.unit_cap_f)
