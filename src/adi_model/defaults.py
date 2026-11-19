# =============================================================================
# 文件名     : defaults.py
# 模块名     : adc_digital_ip_v21 的 Python 公共入口
# 功能       : 对受理样本复现实际片号、两端扰动、逐位写门、DEM、权重和与出码。
# 设计说明   : 默认使用理想外部宏、名义加载表及 B8 外部相位激励。一次调用从
#              复位与完整配置已提交开始。逐拍反压与物理片权另由生产 RTL 测试。
# =============================================================================
"""生产数字 IP 的事务参考。默认不需要 EDA；专利 Fig.11 行为研究有独立的显式导入。"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from hashlib import sha256
from math import floor, isfinite
from numbers import Integral, Real

import numpy as np
from numpy.typing import ArrayLike

from adi_model._arrays import readonly
from adi_model.control.dither_word import DEFAULT_DITHER_SEED, DitherWordGenerator
from adi_model.dem_dither import SUB, pre
from adi_model.digital_conversion import (
    ADC2_MIN,
    ADC2_SPAN,
    LSB20,
    Q30,
    VFS,
    conversion_mask,
    nominal_weights_q30,
    reconstruct,
    sar_conversion,
    weight_sums,
)
from adi_model.sched18_rtl import SEED_DEFAULT, Scheduler18Rtl, prng_step
from adi_model.simulation.config import ConfigError

DEFAULT_SEED = 20261105


# 布尔值在 Python 中属于 int 的子类。用 True 当作标签或片号会让错误输入看似
# 已经通过握手，因此在缩成端口位宽之前先判类型。
def _integer(value, name: str, lo: int, hi: int | None = None) -> int:
    """在截断为引脚位宽前校验；布尔和小数不能当作控制字或样本数。"""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ConfigError(f"{name} must be an integer")
    result = int(value)
    if result < lo or (hi is not None and result > hi):
        raise ConfigError(f"{name} out of range")
    return result


# 采样率要求大于零；标准差可以恰为零。两种用途都不允许 NaN，
# 因为单纯的上下界比较无法发现它，量化器随后也无法给出有效箱号。
def _finite(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not isfinite(value):
        raise ConfigError(f"{name} must be finite")
    if value < 0 or (positive and value == 0):
        raise ConfigError(f"{name} out of range")
    return float(value)


# 1. 固定数字合同与可显式改变的宏输入。所有制造/噪声项默认为零。
@dataclass(frozen=True)
class DefaultParameters:
    """fs 以 Hz 计，噪声/限幅以 V 计，失配为标准差。物理表为 Ceff/Cf，加载表为整数 Q30。"""

    adc2_n_bits: int = 13
    adc2_v_min: float = -0.1875
    adc2_v_max: float = 0.5625
    endpoint_policy: str = "half_open"
    fs: float = 40e6
    v_fs: float = 3.0
    n_main: int = 16
    n_sub: int = 4
    n_slices: int = 18
    n_active: int = 8
    b1: int = 9
    scheduler_seed: int = SEED_DEFAULT
    b_q24: int = 0
    unit_mismatch_std: float = 0.0
    bridge_mismatch_std: float = 0.0
    feedback_mismatch_std: float = 0.0
    sampling_noise_rms_v: float = 0.0
    ra_noise_rms_v: float = 0.0
    ra_clip_v: float | None = None
    enable_sampling_noise: bool = True
    ra_enable_noise: bool = True
    dem_enable: bool = True
    dither_enable: bool = True
    device_weights: ArrayLike | None = None
    weights_q30: ArrayLike | None = None

    def __post_init__(self):
        # 这些值对应生产引脚和配置地址宽度；不同几何需要自己的物理研究。
        # 在生成第一笔之前拒绝不兼容的几何，以免旧系数仅因行数相同而被接受。
        dims = (
            (self.adc2_n_bits, 13),
            (self.n_main, 16),
            (self.n_sub, 4),
            (self.n_slices, 18),
            (self.n_active, 8),
            (self.b1, 9),
        )
        for value, fixed in dims:
            _integer(value, "production geometry or width", fixed, fixed)
        if (self.v_fs, self.adc2_v_min, self.adc2_v_max, self.endpoint_policy) != (
            VFS,
            ADC2_MIN,
            ADC2_MIN + ADC2_SPAN,
            "half_open",
        ):
            raise ConfigError("voltage window differs from adc_digital_ip_v21")
        object.__setattr__(self, "fs", _finite(self.fs, "fs", positive=True))
        _integer(self.scheduler_seed, "scheduler_seed", 0, 0xFFFF_FFFF)
        _integer(self.b_q24, "b_q24", -(1 << 26), (1 << 26) - 1)
        for key in (
            "unit_mismatch_std",
            "bridge_mismatch_std",
            "feedback_mismatch_std",
            "sampling_noise_rms_v",
            "ra_noise_rms_v",
        ):
            object.__setattr__(self, key, _finite(getattr(self, key), key))
        if self.ra_clip_v is not None:
            object.__setattr__(self, "ra_clip_v", _finite(self.ra_clip_v, "ra_clip_v", positive=True))
        for key in ("enable_sampling_noise", "ra_enable_noise", "dem_enable", "dither_enable"):
            if type(getattr(self, key)) is not bool:
                raise ConfigError(f"{key} must be a boolean")


# 实际电容和加载字是两组输入。向数字侧提供真实器件的系数只允许显式选择，
# 否则“未校准”和“已知系数校准”会在建立模型时意外变成同一工况。
# 每行由物理片号寻址，不能按本次八个逻辑槽的位置替换制造值。
def _weights(p: DefaultParameters, seed: int) -> tuple[np.ndarray, np.ndarray]:
    nominal = nominal_weights_q30()
    # 每字先按传入的整数类型检查上下界，再转换存储。
    # 浮点数即使看起来是整数也可能已经丢失最低位，不能默认为寄存器字。
    configured = np.asarray(nominal if p.weights_q30 is None else p.weights_q30)
    if (
        configured.shape != (18, 20)
        or configured.dtype.kind not in "iu"
        or np.any(configured < 0)
        or np.any(configured >= 1 << 40)
        or np.any(configured[:, 16:] > 128 * Q30)
    ):
        raise ConfigError(
            "weights_q30 requires 18x20 unsigned 40-bit integers; check sub-weight range"
        )
    physical = nominal / Q30
    mismatch = (p.unit_mismatch_std, p.bridge_mismatch_std, p.feedback_mismatch_std)
    # 显式物理表已经包含实际器件，不能再对它进行第二次随机制造。
    # 加载表仍可独立指定，用来验证已知校正或故意失配的对照。
    if p.device_weights is not None:
        if any(mismatch):
            raise ConfigError("specify either device_weights or fabrication deviations")
        a = np.asarray(p.device_weights)
        if a.shape != (18, 20) or a.dtype.kind not in "fiu":
            raise ConfigError("device_weights requires 18x20 physical Ceff/Cf values")
        physical = np.asarray(a, dtype=float)
    elif any(mismatch):
        # 先制造实际电容，再由同一片的桥和子阵列计算 beta。数字加载表独立保存。
        rand = np.random.default_rng(np.random.SeedSequence([seed, 0]))
        cf = 16 * (1 + rand.normal(0, p.feedback_mismatch_std))
        main = 4 * (1 + rand.normal(0, p.unit_mismatch_std, (18, 16)))
        sub = np.asarray(SUB) * (1 + rand.normal(0, p.unit_mismatch_std, (18, 4)))
        cb = (8 / 7) * (1 + rand.normal(0, p.bridge_mismatch_std, (18, 1)))
        if cf <= 0 or np.any(main <= 0) or np.any(sub <= 0) or np.any(cb <= 0):
            raise ConfigError("sampled nonpositive physical capacitance")
        physical = np.column_stack((main, sub * cb / (cb + sub.sum(axis=1, keepdims=True)))) / cf
    if not np.all(np.isfinite(physical)) or np.any(physical <= 0):
        raise ConfigError("physical weights must be finite and positive")
    # 复制后冻结表的底层字节，调用方后续改写原 ndarray 不会影响在途样本。
    return readonly(physical), readonly(configured, np.int64)


# 2. 每一行对应一个输入样本。无有效出码的异常行仍保存标签、原因和已发出的命令。
# 输入保留两列：波形源给定的干净电压与指定宏噪声后的采样电压。
# 这样改变噪声假设时仍可以重建相同的外部激励。
@dataclass(frozen=True)
class InputSamples:
    input_clean_v: np.ndarray
    input_v: np.ndarray


# 数组的第一维始终按接受顺序排列。sample_id 从零计数，顶层 tag 从一开始。
# 八个槽的 pre 为四位开关字，post 为二十位开关字；槽 s 的物理片号取同一行 ids[s]。
# event_count 范围内的 −1 为关闭写门的比较，范围外的 −1 用于定长填充。
# 非法转换命令不会产生最终 post 或后级返回，须连同 out_valid 阅读这些列。
@dataclass(frozen=True)
class DigitalTrace:
    sample: InputSamples
    sample_id: np.ndarray
    tag: np.ndarray
    qid: np.ndarray
    word: np.ndarray
    h: np.ndarray
    v: np.ndarray
    half: np.ndarray
    coin: np.ndarray
    sadc_mask: np.ndarray
    lower_eighths: np.ndarray
    upper: np.ndarray
    coarse: np.ndarray
    k: np.ndarray
    adc2_code: np.ndarray
    acquisition_ids: np.ndarray
    pre_slots: np.ndarray
    post_slots: np.ndarray
    event_k: np.ndarray
    event_count: np.ndarray
    g_q30: np.ndarray
    wm_q30: np.ndarray
    wp_q30: np.ndarray
    out_valid: np.ndarray
    out_err: np.ndarray
    sadc_over: np.ndarray
    ra_sat: np.ndarray
    adc2_over: np.ndarray
    rdac_over: np.ndarray

    @property
    def analog_overflow(self) -> np.ndarray:
        """全部输入样本的节点与码域异常并集；逐节点明细随记录返回。"""
        return self.sadc_over | self.ra_sat | self.adc2_over | self.rdac_over


# 输出码按正向整数网格解释，电压量只用于诊断。
# 在模拟越窗时仍可能得到合法数字码，所以保留两个独立的标志集合。
@dataclass(frozen=True)
class DigitalCodes:
    code: np.ndarray
    clipped_low: np.ndarray
    clipped_high: np.ndarray
    analog_overflow: np.ndarray

    @property
    def voltage(self) -> np.ndarray:
        """工程有符号舍入的 20 位网格，0x80000 对应差分 0 V。"""
        return self.code.astype(float) * LSB20 - VFS


# CLI 的 NPZ 和库结果从相同数组导出。两者可通过样本号对齐，
# 不会把标志为异常的一笔从一个视图单独删除。
@dataclass(frozen=True)
class DefaultRun:
    raw: DigitalTrace
    codes: DigitalCodes
    metadata: dict

    def arrays(self) -> dict[str, np.ndarray]:
        """返回适合 NPZ 发布的记录列，每列的第一维均为原始样本数。"""
        # 与原有 codes.npz 的码和标志列兼容，另外公开同笔的开关、权重和 ADC2 码。
        arrays = {f.name: getattr(self.raw, f.name) for f in fields(self.raw) if f.name != "sample"}
        arrays.update(input_clean_v=self.raw.sample.input_clean_v, input_v=self.raw.sample.input_v)
        arrays.update({f.name: getattr(self.codes, f.name) for f in fields(self.codes)})
        return arrays


# 强制字可按笔给出，也可在同一记录内用 None 切回片内字。
# 四个 DEM 输入来自上游，其取值范围不同；先检查整份激励，再启动本次数字序列。
def _controls(n: int, words, dem):
    # 整批没有外部字时始终使用片内下一状态；一批混合请求可以只有个别样本强制。
    # 两种选择都只推进一个状态，因此不会使后续样本错位。
    if words is None:
        forced = None
    else:
        values = list(words)
        if len(values) != n:
            raise ConfigError("dither_words must have one word or None per accepted sample")
        forced = [None if w is None else _integer(w, "dither word", 0, 63) for w in values]
    if dem is None:
        return forced, None
    phases = np.asarray(dem)
    if (
        phases.shape != (n, 4)
        or phases.dtype.kind not in "iu"
        or np.any(phases < 0)
        or np.any(phases > [7, 7, 1, 1])
    ):
        raise ConfigError("dem_controls must be n x (h:0..7, v:0..7, half:0..1, coin:0..1)")
    return forced, phases


class DefaultModel:
    """每次 simulate 复位数字字源和片池。相同 seed/run_id 重放同一制造及宏激励。"""

    # 创建器件时只消耗制造用随机流。加载表被复制成独立不可变数组，
    # 防止调用者在一次运行期间改写系数、破坏样本的身份。
    def __init__(self, *, seed: int, parameters: DefaultParameters, config_overrides: dict | None):
        self.seed = _integer(seed, "seed", 0)
        if not isinstance(parameters, DefaultParameters):
            raise ConfigError("expected production DefaultParameters")
        self.parameters = parameters
        self._overrides = dict(config_overrides or {})
        self.cfg = replace(parameters, **self._overrides)
        self.device, self.loaded = _weights(self.cfg, self.seed)

    @property
    def nominal_gain(self) -> float:
        """名义 8 片主阵列共 512Cu，共享 Cf=16Cu，电压增益为 32。"""
        return 32.0

    # 3. 从同笔输入走到 tagged 输出
    # 事务层每行代表一次接受；分支只依据本笔的物理片号、开关、已决比较及显式宏输入。
    # 调度器在数字时钟上的停收或排空另外从 RTL 的实际 valid/ready 检查。
    # 两个采样通道轮流接收低段；八片 RDAC 自身承受完整采样预置。
    def simulate(
        self, input_fn, n_samples: int, *, run_id: int = 0, dither_words=None, dem_controls=None
    ) -> DefaultRun:
        """以秒采样给定差分电压函数。显式六位字对应强制引脚，None 消费同一片内序列。"""
        n = _integer(n_samples, "n_samples", 1, 0xFFFF_FFFF)
        run_id = _integer(run_id, "run_id", 0)
        cfg = self.cfg
        forced, phases = _controls(n, dither_words, dem_controls)
        if not cfg.dither_enable and forced is not None and any(w not in (None, 0) for w in forced):
            raise ConfigError("nonzero forced words conflict with the zero-word control")
        if not cfg.dem_enable and phases is not None and phases.any():
            raise ConfigError("nonzero phases conflict with the fixed-phase control")
        # 这里的时间是公共采样时刻。使用 fs 改变测试波形的采样位置，
        # 不据此推断真实时钟能否塞入所有 SAR 事件或模拟建立。
        t = np.arange(n, dtype=float) / cfg.fs
        wave = np.asarray(input_fn(t))
        if (
            wave.dtype.kind not in "fiu"
            or wave.shape not in ((), (n,))
            or not np.isfinite(wave).all()
        ):
            raise ConfigError("input must produce finite scalar or per-sample voltages")
        clean = np.broadcast_to(wave, (n,)).astype(float)
        # 制造、采样和 RA 各用独立随机流。改变噪声不会改变调度或扰动序列。
        sample_seed, ra_seed = np.random.SeedSequence([self.seed, run_id, 1]).spawn(2)
        # 保留干净输入供误差评估。向采样节点加入噪声时不能改写调用方的波形数组。
        sampled = clean.copy()
        if cfg.enable_sampling_noise and cfg.sampling_noise_rms_v:
            sampled += np.random.default_rng(sample_seed).normal(0, cfg.sampling_noise_rms_v, n)
        noise = np.zeros(n)
        if cfg.ra_enable_noise and cfg.ra_noise_rms_v:
            noise = np.random.default_rng(ra_seed).normal(0, cfg.ra_noise_rms_v, n)
        arr = {
            k: np.zeros(n, dtype=np.int64)
            for k in (
                "word h v half coin sadc_mask lower_eighths upper coarse k adc2_code event_count "
                "g_q30 wm_q30 wp_q30"
            ).split()
        }
        for k in ("out_valid", "out_err", "sadc_over", "ra_sat", "adc2_over", "rdac_over"):
            arr[k] = np.zeros(n, dtype=bool)
        for k in ("acquisition_ids", "pre_slots", "post_slots"):
            arr[k] = np.zeros((n, 8), dtype=np.int64)
        arr["event_k"] = np.full((n, 8), -1, dtype=np.int64)
        code = np.zeros(n, dtype=np.uint32)
        low, high = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
        # 每次批处理对应一次独立复位和装表。
        # 开始时的片池、扰动和两个上游相位状态分别初始化。
        scheduler, rng = Scheduler18Rtl(cfg.scheduler_seed), DitherWordGenerator()
        v_rng, bridge_rng = 0x2468ACE1, 0x9E3779B9
        pack = 1 << np.arange(20, dtype=np.int64)
        # 四个数是开关线的打包位序；求电荷使用器件表中相应的真实权重。
        pack_pre = np.array([8, 4, 2, 1], dtype=np.int64)
        for i in range(n):
            # 受理时冻结旧 ACQ 的八个片号。step 把该组交给转换，新 ACQ 属于下一笔。
            ids = scheduler.step()["conv"]
            word = rng.step(
                sample_accept=True,
                sample_id=i + 1,
                dither_enable=cfg.dither_enable,
                force_word=None if forced is None else forced[i],
            ).word
            v_rng, bridge_rng = prng_step(v_rng), prng_step(bridge_rng)
            h, v, half, coin = i & 7, v_rng & 7, (i >> 3) & 1, bridge_rng & 1
            # 显式四元组直接代表受理时的外部引脚；生成期不读未来的 Vin 或码。
            # 关闭 DEM 表示上游将相位与交换针脚置零，18 选 8 片池继续正常工作。
            if phases is not None:
                h, v, half, coin = map(int, phases[i])
            if not cfg.dem_enable:
                h = v = half = coin = 0
            # pre 的四位字按本笔冻结的 v 在八槽间分摊小数，
            # 同时按该笔 qid 选中 SADC。比较器只见低段的电压贡献。
            pre_bits, _, upper, lower = pre(word, v)
            pre_mask = np.asarray(pre_bits, dtype=np.uint8)
            c, events = sar_conversion(float(sampled[i]), lower, upper)
            k = c + upper
            # 仅对范围内的实际命令记录写门。超过 520 会置事件错误，后续递增的前缀也不能写。
            arr["event_k"][i, : len(events)] = [e if e <= 520 else -1 for e in events]
            for key, value in (
                ("word", word),
                ("h", h),
                ("v", v),
                ("half", half),
                ("coin", coin),
                ("sadc_mask", ((word & 16) | (word & 7)) << 1),
                ("lower_eighths", int(lower * 8)),
                ("upper", upper),
                ("coarse", c),
                ("k", k),
                ("event_count", len(events)),
            ):
                arr[key][i] = value
            arr["acquisition_ids"][i] = ids
            arr["pre_slots"][i] = pre_mask @ pack_pre
            arr["sadc_over"][i] = sampled[i] < -VFS or sampled[i] >= VFS
            if k > 520:
                # 前级非法事件关闭物理写门，本笔无最终快照、ADC2 或退休码。
                arr["rdac_over"][i] = True
                continue
            post = conversion_mask(k, h, v, half, coin)
            arr["post_slots"][i] = post @ pack
            # 外部宏看真实物理片，加载表只能进入数字重构。主阵列采 Vin，
            # 子阵列采 pre 参考，转换后各自扣除实际 post 参考电荷。
            pg, pm, pp = weight_sums(self.device[ids], post, pre_mask)
            y = (float(sampled[i]) + VFS) * float(pg) - 6 * (float(pm) - float(pp)) + noise[i]
            if cfg.ra_clip_v is not None and abs(y) > cfg.ra_clip_v:
                arr["ra_sat"][i] = True
                y = max(-cfg.ra_clip_v, min(cfg.ra_clip_v, y))
            # 下限属于第一个码箱，上限已越窗。宏在越窗时回零原码并另送范围标志。
            # 这与最后一次输出舍入恰好达到数字满量程是不同的事件。
            in_window = ADC2_MIN <= y < ADC2_MIN + ADC2_SPAN
            raw = floor((y - ADC2_MIN) * (8192 / ADC2_SPAN)) if in_window else 0
            # 模拟响应已经形成后才读取已加载的数字表。
            # 切换表只应改变重构结果，不能回头改变同一笔 ADC2 原码。
            g, wm, wp = map(int, weight_sums(self.loaded[ids], post, pre_mask))
            arr["adc2_code"][i], arr["adc2_over"][i] = raw, not in_window
            arr["g_q30"][i], arr["wm_q30"][i], arr["wp_q30"][i] = g, wm, wp
            # ADC2 range 随响应单列，重构的 out_err 表示数字域/截断错误。
            arr["out_valid"][i] = True
            code[i], low[i], high[i], arr["out_err"][i] = reconstruct(raw, g, wm, wp, cfg.b_q24)
        # 索引、硬件事务标签和量化器号具有不同的起点与位宽。
        # 即使本笔没有有效退休，输入行仍保留，下一行不挤占它的位置。
        sample_id = np.arange(n, dtype=np.uint32)
        trace = DigitalTrace(
            sample=InputSamples(clean, sampled),
            sample_id=sample_id,
            tag=sample_id + np.uint32(1),
            qid=sample_id & 1,
            **arr,
        )
        codes = DigitalCodes(code, low, high, trace.analog_overflow)
        metadata = self._metadata(trace, codes, run_id, forced, phases)
        return DefaultRun(trace, codes, metadata)

    # 数字表以小端 Q30 整数字节求摘要，物理表以小端浮点数求摘要。
    # 在不同文件布局或重放工具之间，可据此检查输入身份。
    # 把 ADC2 越窗、有输入却无有效退休和数值限幅各自报告。
    def _metadata(self, trace, codes, run_id, forced, phases):
        cfg = self.cfg
        return {
            "profile": "adc_digital_ip_v21",
            "digital_contract": "adc_digital_ip_v21",
            "result_role": "DIGITAL_IP_TRANSACTION_REFERENCE",
            "source_reproduction": False,
            # 已知的真实权重仍是调用方提供的外部系数，不在本次运行内部反求。
            "digital_coefficients": "NOMINAL_NOT_FITTED"
            if cfg.weights_q30 is None
            else "EXPLICIT_Q30_TABLE",
            "loaded_weights_q30_sha256": sha256(
                self.loaded.astype("<i8", copy=False).tobytes()
            ).hexdigest(),
            "physical_weights_sha256": sha256(
                self.device.astype("<f8", copy=False).tobytes()
            ).hexdigest(),
            "precision_claim": "20-bit output format",
            "output_bits": 20,
            "n_samples": len(codes.code),
            "seed": self.seed,
            "run_id": run_id,
            "raw_invalid": int(np.count_nonzero(codes.analog_overflow)),
            "final_clip_low": int(np.count_nonzero(codes.clipped_low)),
            "final_clip_high": int(np.count_nonzero(codes.clipped_high)),
            "digital_errors": int(np.count_nonzero(trace.out_err)),
            "retired": int(np.count_nonzero(trace.out_valid)),
            "enabled": {
                "sampling_noise": bool(cfg.enable_sampling_noise and cfg.sampling_noise_rms_v),
                "ra_output_noise": bool(cfg.ra_enable_noise and cfg.ra_noise_rms_v),
                "unit_dem": cfg.dem_enable,
                "onchip_word_advances": True,
                "force_word_used": forced is not None
                and any(w is not None for w in forced)
                or not cfg.dither_enable,
            },
            "unmodeled": [
                "clock-edge handshakes and stalls",
                "transistor settling and silicon performance",
            ],
            "inferences": self.inference_table(explicit_phases=phases is not None),
        }

    # 上游的相位激励与芯片内部字源分开记录。
    # 论文披露三种片角色，实际的种子、位序与箱中心属于这份工程设计。
    def inference_table(self, *, explicit_phases: bool = False) -> dict:
        """返回实际单位、宏假设和控制输入来源；可在运行前检查配置。"""
        cfg = self.cfg
        return {
            "fs_hz": {
                "value": cfg.fs,
                "grade": "USER_OVERRIDE"
                if "fs" in self._overrides or cfg.fs != 40e6
                else "SAMPLE_STIMULUS",
            },
            "adc2": {
                "bits": 13,
                "window_v": [ADC2_MIN, ADC2_MIN + ADC2_SPAN],
                "grade": "ENGINEERING_CONTRACT",
            },
            "pool": {"slices": 18, "active": 8, "spare": 2, "grade": "DISCLOSED"},
            "pool_seed": {
                "value": int(cfg.scheduler_seed) or SEED_DEFAULT,
                "grade": "ENGINEERING_CONTRACT",
            },
            "configuration": {
                "weight_words": 360,
                "b_q24": int(cfg.b_q24),
                "alpha": 1,
                "grade": "ENGINEERING_CONTRACT",
            },
            "dither": {
                "seed": DEFAULT_DITHER_SEED,
                "upper_sign": "+",
                "sampling_weight_sign": "-",
                "grade": "ENGINEERING_CONTRACT",
            },
            "phase_inputs": {
                "source": (
                    "fixed zero"
                    if not cfg.dem_enable
                    else "explicit"
                    if explicit_phases
                    else "B8 upstream stimulus"
                ),
                "grade": "EXTERNAL_CONTROL",
            },
            "macro": {
                "model": "capacitor charge with half-open ADC2 bins",
                "unit_mismatch_std": cfg.unit_mismatch_std,
                "bridge_mismatch_std": cfg.bridge_mismatch_std,
                "feedback_mismatch_std": cfg.feedback_mismatch_std,
                "ra_clip_v": cfg.ra_clip_v,
                "sample_noise_rms_v": cfg.sampling_noise_rms_v
                if cfg.enable_sampling_noise
                else 0.0,
                "ra_noise_rms_v": cfg.ra_noise_rms_v if cfg.ra_enable_noise else 0.0,
                "grade": "ENGINEERING_ASSUMPTION",
            },
        }


# 3. 包顶层、命令行和库调用均走同一构造器。
# 旧 Fig.11 实验使用其专利几何及减法坐标；公共构造器只实例化生产事务模型。
# 输入若使用另一种格式或位宽，交给参数守卫明确拒绝。
def default_model(
    *,
    seed: int = DEFAULT_SEED,
    parameters: DefaultParameters | None = None,
    config_overrides: dict | None = None,
) -> DefaultModel:
    """创建唯一生产参考。制造种子、外部宏偏差及加载表均可独立指定。"""
    return DefaultModel(
        seed=seed,
        parameters=DefaultParameters() if parameters is None else parameters,
        config_overrides=config_overrides,
    )


# 一笔固定输入可直接用 dc_input 送入。补齐所有样本后一次性返回数组，
# 以同样的初始条件重放相同的标签、字和物理开关。
def simulate(
    input_fn,
    n_samples: int,
    *,
    seed: int = DEFAULT_SEED,
    run_id: int = 0,
    parameters: DefaultParameters | None = None,
    config_overrides: dict | None = None,
    dither_words=None,
    dem_controls=None,
) -> DefaultRun:
    """以差分电压函数运行 n 笔；返回初始复位状态下逐笔可复核的码和开关。"""
    return default_model(
        seed=seed, parameters=parameters, config_overrides=config_overrides
    ).simulate(
        input_fn, n_samples, run_id=run_id, dither_words=dither_words, dem_controls=dem_controls
    )
