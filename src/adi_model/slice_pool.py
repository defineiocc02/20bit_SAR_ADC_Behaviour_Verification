"""物理 slice 池：把固定电容、采样历史和实际开关选择绑定到同一物理身份。

本模块位于输入采样与残差 DAC 之间。公开的 18 个 slice 是可交错使用的
电容阵列；每周期 8 个转换、8 个采样、2 个备用，不是 18 颗完整 ADC。
``conv[n, slot]`` / ``acq[n, slot]`` 保存物理 slice ID；slot 是组内接线位置，
``sample_id`` 则标识一次转换，三者不能互换。下一周期必须转换前周期采过的组。

信号、电气负载和噪声使用同一组物理电容，但三个等效量不同：
``G = C_signal/C_feedback`` 是 RA 对已存残差电压的增益；采样 dither 移走
部分输入电荷时，外部输入到 RA 的信号系数为 ``K_signal = G*alpha``。
``C_noise`` 只定义独立采样热噪声的等效电容，不能用全部 18 片之和代替。
每个 split slice 的浮置子节点先单独求 beta，再合并其贡献，不能先短接求和。

单位与状态：unit_caps 为 [F]，v_top 为实际保持的顶板电压 [V]（采样后为
采样值，转换后可为残差），电荷须由电容乘电压得到 [C]。本池固定制造失配；
plan 只生成分配，acquire/release 才改变局部保持状态。主 split runner 每次调用
重置电气状态和使用计数，同时重用同一个物理池，不会因重跑而重抽电容。

18/8 数量来自 [00]；自由集合中选片的 shuffle_causal 是项目调度假设，
不同于 rtl16 分配器。这里没有完整寄生网表、连续开关瞬态或逐 slice 学习器。

Example:
-------
>>> import numpy as np
>>> from adi_model.config import Config
>>> from adi_model.slice_pool import PhysicalSlicePool
>>> cfg = Config()
>>> pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
>>> plan = pool.plan(64, np.random.default_rng(1), strategy="shuffle_causal")
>>> plan.conv.shape, plan.acq.shape
((64, 8), (64, 8))
>>> chk = check_causality(plan)
>>> chk["violations"]
0
"""

from __future__ import annotations

import numpy as np

from .config import Config, ConfigError
from .dac_arch import SplitChip, build_split_chip
from .dem import split_switch_command
from .provenance import SourceGrade
from .timing import SlicePlan, build_slice_plan

__all__ = [
    "SlicePlan",
    "PhysicalSlicePool",
    "check_causality",
    "SLICE_POOL_GRADES",
]

SLICE_POOL_GRADES = {
    "n_slices": SourceGrade.DISCLOSED,
    "n_active": SourceGrade.DISCLOSED,
    "strategy": SourceGrade.ASSUMED,
}


def check_causality(plan: SlicePlan) -> dict:
    """从计划数组独立检查相邻周期的采样组与转换组是否一致。

    Args:
        plan: SlicePlan，conv/acq 均为 (N, n_active) 物理 slice ID 数组。

    Returns:
        transitions=N-1；violations 为组交集不足 n_active 的转移数；
        mean_coverage 为交集平均片数；causal 表示 violations 是否为零。
        N<2 时没有可检查的转移，返回 causal=True、mean_coverage=NaN。

    Notes:
        仅检查 conv[n] 与 acq[n-1] 的组关系，不证明开关建立、实际存储电荷、
        ID 唯一性或首周期预热；这些由计划生成器和消费它的 runner 负责。
    """
    n = len(plan)
    if n < 2:
        return {
            "transitions": 0,
            "violations": 0,
            "mean_coverage": float("nan"),
            "causal": True,
        }
    violations = 0
    coverage = np.empty(n - 1, dtype=float)
    for i in range(1, n):
        # The group converting at cycle i must equal a group that acquired
        # earlier and has not been overwritten since.
        common = len(set(plan.conv[i].tolist()) & set(plan.acq[i - 1].tolist()))
        coverage[i - 1] = common
        if common != plan.conv.shape[1]:
            violations += 1
    return {
        "transitions": n - 1,
        "violations": int(violations),
        "mean_coverage": float(np.mean(coverage)),
        "causal": violations == 0,
    }


class PhysicalSlicePool:
    """一个虚拟芯片的固定电容池和可重置的电气状态。

    改变 conv 中的物理 ID，会同时改变 DAC 权重、等效电容和逐样本 RA 增益。
    显式传入 aggregate chip 时，把同一聚合电容按 n_active 等分到各 slice；
    未传入时才按配置抽取片级和单元级失配。不要把前者当作独立片间失配实验。

    Attributes:
        cfg: 制造时配置；运行时掩码可由部分方法的 cfg 参数另行指定。
        unit_caps: (n_slices, n_unit_per_slice)，物理电容 [F]；split 按主、子排列。
        c_slice_total: (n_slices,)，物理电容直接求和 [F]，尚未按 beta 折算。
        v_top: (n_slices,)，各物理 slice 最近保持的顶板电压 [V]。
        held_sample: (n_slices,)，由 runner 维护的样本 ID，-1 表示未登记。
        held_valid: (n_slices,)，保持电压是否可被 held_voltage 消费。
        n_conversions: (n_slices,)，release 更新的使用计数；runner 可重置/回填。
    """

    def __init__(
        self,
        cfg: Config,
        rng: np.random.Generator,
        *,
        slice_sigma: float | None = None,
        unit_sigma: float | None = None,
        n_unit_per_slice: int | None = None,
        chip: SplitChip | None = None,
    ) -> None:
        """建立固定制造失配和空的保持状态，不执行采样。

        Args:
            cfg: 阵列拓扑、标称电容及失配配置；电容单位为 [F]，失配 sigma 为相对值。
            rng: 制造随机流；与运行时采样噪声分开，避免改噪声设置时重抽电容。
            slice_sigma: 片级相对标准差覆盖；None 时按 mismatch_split 方差份额推导。
            unit_sigma: 单元级相对标准差覆盖；None 时按方差份额推导。
            n_unit_per_slice: unary 每片单元数覆盖；split 按主、子单元数之和确定。
            chip: 可选固定聚合 SplitChip；其电容等分后复制，不再次叠加随机失配。

        Side effects:
            消费 rng 的制造随机数，建立 unit_caps 和查找表。tau_rel、t_skew、v_os
            使用独立时钟随机流，分别为相对建立系数、静态时偏 [s] 和失调 [V]。
            初始 v_top=0、held_valid=False、held_sample=-1、使用计数为零。

        Raises:
            ConfigError: 梯度非法，或没有聚合 chip 却要求依赖版图坐标的空间梯度。
            ValueError: split 物理电容非有限/非正，或寄生非有限/为负。
        """
        self.cfg = cfg
        gradient = np.asarray(cfg.mismatch_gradient, dtype=float)
        if gradient.shape != (2,) or np.any(~np.isfinite(gradient)):
            raise ConfigError("mismatch_gradient must contain two finite relative gradients")
        if np.any(gradient != 0) and (chip is None or cfg.dac_arch != "split"):
            raise ConfigError(
                "mismatch_gradient requires an explicit aggregate chip: "
                "PhysicalSlicePool has no layout coordinates; use build_split_chip "
                "and pass chip= for the documented aggregate gradient model"
            )
        self._mismatch_gradient = tuple(gradient)
        n_slices = int(cfg.n_slices)
        n_up = int(n_unit_per_slice or cfg.n_unit_per_slice)
        self.is_split = cfg.dac_arch == "split"
        self.template_chip = (chip or build_split_chip(cfg)) if self.is_split else None
        if self.is_split:
            n_up = cfg.dac_n_main + cfg.dac_n_sub
        self.n_slices = n_slices
        self.n_unit_per_slice = n_up
        self.n_units = int(cfg.n_active) * n_up

        # 失配拆分为全局、片级、单元级方差份额；归一化后再开方得到 sigma。
        g_frac, s_frac, u_frac = cfg.mismatch_split
        tot = max(g_frac + s_frac + u_frac, 1e-12)
        s_frac, u_frac = s_frac / tot, u_frac / tot
        c_unit_nom = (
            cfg.dac_unit_cap() / cfg.n_active
            if self.is_split
            else (cfg.c_total0 * cfg.cap_scale) / self.n_units
        )
        sig0 = cfg.sigma_mismatch_unit(c_unit_nom) if self.is_split else float(cfg.mismatch_sigma0)
        sig_slice = float(slice_sigma) if slice_sigma is not None else sig0 * np.sqrt(s_frac)
        sig_unit = float(unit_sigma) if unit_sigma is not None else sig0 * np.sqrt(u_frac)

        self.c_unit_nom = float(c_unit_nom)

        # 片级偏差与物理 ID 绑定，在该虚拟芯片的全部运行中保持不变。
        self.slice_dev = (
            rng.normal(0.0, sig_slice, n_slices) if sig_slice > 0 else np.zeros(n_slices)
        )
        # 同一片内再叠加单元级偏差；后续 DEM 只改变选中单元，不重新抽取偏差。
        unit_dev = (
            rng.normal(0.0, sig_unit, (n_slices, n_up))
            if sig_unit > 0
            else np.zeros((n_slices, n_up))
        )
        if not cfg.mismatch_enable:
            self.slice_dev = np.zeros(n_slices)
            unit_dev = np.zeros((n_slices, n_up))
        global_dev = rng.normal(0, sig0 * np.sqrt(g_frac / tot)) if cfg.mismatch_enable else 0.0
        self.unit_caps = c_unit_nom * (1.0 + global_dev + self.slice_dev[:, None] + unit_dev)
        if self.is_split:
            assert self.template_chip is not None
            template = self.template_chip
            if chip is not None:
                self.unit_caps = np.tile(
                    np.concatenate((chip.C_main, chip.C_sub)) / cfg.n_active,
                    (n_slices, 1),
                )
            self.bridge_caps = np.full(n_slices, template.C_bridge_nom / cfg.n_active)
            self.sub_parasitic = np.full(n_slices, template.c_p_sub_nom / cfg.n_active)
            if chip is not None:
                self.bridge_caps[:] = chip.C_bridge / cfg.n_active
                self.sub_parasitic[:] = chip.c_p_sub / cfg.n_active
            elif cfg.mismatch_enable:
                self.bridge_caps *= 1 + cfg.dac_bridge_mismatch_sigma * rng.standard_normal(
                    n_slices
                )
                self.sub_parasitic *= 1 + cfg.dac_parasitic_spread * rng.standard_normal(n_slices)
            # 后续 beta 和电荷分母要求电容有限且正、寄生有限且非负。
            if (
                np.any(~np.isfinite(self.unit_caps))
                or np.any(self.unit_caps <= 0)
                or np.any(~np.isfinite(self.bridge_caps))
                or np.any(self.bridge_caps <= 0)
                or np.any(~np.isfinite(self.sub_parasitic))
                or np.any(self.sub_parasitic < 0)
            ):
                raise ValueError(
                    "physical split capacitances must be finite and positive; parasitics finite and nonnegative"
                )
        self.c_slice_total = self.unit_caps.sum(axis=1)
        self.c_total_nom = c_unit_nom * self.n_units

        # 制造参数与运行时保持状态分开；runner 可清空后者并保留前者。
        self.v_top = np.zeros(n_slices)
        self.held_sample = np.full(n_slices, -1, dtype=np.int64)
        self.held_valid = np.zeros(n_slices, dtype=bool)
        self.n_conversions = np.zeros(n_slices, dtype=np.int64)
        # Clock/switch properties are chip state, not observation noise. Their
        # dedicated stream is unchanged when capacitor/noise draws are enabled.
        clock_rng = np.random.default_rng(cfg.seed + 8301)
        self.tau_rel = 1 + clock_rng.normal(0, cfg.slice_bw_spread, n_slices)
        self.t_skew = clock_rng.normal(0, cfg.slice_timing_skew_s, n_slices)
        self.v_os = clock_rng.normal(0, cfg.slice_offset_sigma_v, n_slices)
        self.v_top_q = np.zeros(2)

        # ---- cumulative-sum table for vectorised DAC selection ------------
        # CUM_ALL[j * n_up + u, m] = sum of the first m units of slice j after
        # rotating its unit order by u. Built once per chip (~600 kB at
        # 18 x 64 x 65 float64) and then gathered per sample.
        self._cum_all = np.empty((n_slices * n_up, n_up + 1), dtype=float)
        for j in range(n_slices):
            cj = self.unit_caps[j]
            for u in range(n_up):
                rot = np.roll(cj, -u)
                self._cum_all[j * n_up + u] = np.concatenate([[0.0], np.cumsum(rot)])
        self._built = True

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        n_samples: int,
        rng: np.random.Generator,
        strategy: str = "shuffle_causal",
    ) -> SlicePlan:
        """生成满足跨周期组归属的计划，不推进池内电气状态。

        Args:
            n_samples: 计划周期数 N，非负。
            rng: 独立的调度随机流，不读取物理失配或噪声真值。
            strategy: pingpong 固定两组交替；shuffle_causal 从本周期不转换的
                自由集合中选下一采样组。18/8 配置下是从 10 片选 8 片，余 2 片备用。

        Returns:
            SlicePlan；conv/acq 为 (N, n_active) 物理 ID。conv[n+1]=acq[n]
            按集合相等；第 0 周期 valid=False，需要消费方显式预热。

        Raises:
            ValueError: 非法策略、负周期数，或池不能容纳两组互不重叠的 n_active 片。

        Notes:
            pingpong 默认只用前 2*n_active 片；该 helper 不生成 rtl16 的游标分配。
        """
        return build_slice_plan(self.cfg, n_samples, rng, strategy)

    # -------------------------------------------------------------- DAC math
    def signal_capacitance(self, conv: np.ndarray) -> np.ndarray:
        """返回转换组的残差电压归一化电容 C_signal [F]。

        Args:
            conv: (N, n_active) 实际物理 slice ID，列是逻辑 slot。

        Returns:
            (N,) 等效电容。unary 为活动物理电容之和；split 为各片 A+beta*B
            之和，仍包含采样 dither 保留单元。RA 增益为 G=C_signal/C_feedback；
            若输入信号因掩码衰减 alpha，则输入到 RA 的信号增益是 G*alpha。
        """
        if self.is_split:
            return self.split_coefficients(conv)["c_signal"]
        return self.unit_caps[np.asarray(conv, dtype=np.int64)].sum(axis=(1, 2))

    def split_coefficients(
        self, conv: np.ndarray, *, cfg: Config | None = None
    ) -> dict[str, np.ndarray]:
        """按每个物理子节点求解 split 的信号、负载和噪声系数。

        Args:
            conv: (N, n_active) 实际物理 ID；slot 次序只改变组内接线位置。
            cfg: 可选运行时采样掩码控制；不改变池中制造电容。

        Returns:
            字典内 c_signal/c_load/c_noise/alpha 为 (N,)；其余字段为
            (N, n_active)。电容单位 [F]，alpha、beta 无量纲。
            对每片 i：A_i=sum(C_main)，B_i=sum(C_sub)，
            beta_i=C_bridge_i/(C_bridge_i+B_i+C_parasitic_i)，
            c_slice_i=A_i+beta_i*B_i。先逐片求 beta，不能把各片浮置子节点短接求和。
            c_signal=sum(c_slice_i)，是残差电压分母，不等于移除掩码后的输入电容。
            signal_slice_i=c_slice_i-w_i*C_mask_i，alpha=sum(signal_slice_i)/c_signal；
            子阵列 w_i=beta_i，主阵列 w_i=1。
            c_load=sum(A_i+B_i-C_mask_i)，用于输入驱动负载；
            c_noise=c_signal**2/sum(A_i+beta_i**2*B_i)，用于独立热噪声折算。

        Notes:
            sampling 保留所选阵列末尾 2D 个物理单元；掩码不随 sid 轮转，因此
            固定 conv 和 cfg 时 alpha 与 DEM 状态无关。保留单元仍参与噪声公式。
            这些是集总线性电容模型，不含开关噪声相关性或完整寄生网络。

        Raises:
            ValueError: 池不是 split 拓扑。
        """
        if not self.is_split:
            raise ValueError("split_coefficients requires a split pool")
        cfg = self.cfg if cfg is None else cfg
        ids = np.asarray(conv, dtype=np.int64)
        caps = self.unit_caps
        nm = cfg.dac_n_main
        a, b = caps[:, :nm].sum(axis=-1)[ids], caps[:, nm:].sum(axis=-1)[ids]
        beta = self.bridge_caps[ids] / (self.bridge_caps[ids] + b + self.sub_parasitic[ids])
        c_slice = a + beta * b
        c_signal = c_slice.sum(axis=-1)
        mask = np.zeros_like(a)
        weight = np.ones_like(a)
        nd = cfg.dither_units_total
        if nd:
            if cfg.dither_split_bank == "sub":
                mask = caps[:, -nd:].sum(axis=-1)[ids]
                weight = beta
            else:
                mask = caps[:, nm - nd : nm].sum(axis=-1)[ids]
        # 保留掩码不随 DEM 状态变化；信号项移除，噪声项仍保留其热噪声。
        signal_slice = c_slice - weight * mask
        return {
            "c_signal": c_signal,
            "c_load": (a + b - mask).sum(axis=-1),
            "c_noise": c_signal**2 / (a + beta**2 * b).sum(axis=-1),
            "alpha": signal_slice.sum(axis=-1) / c_signal,
            "signal_slice": signal_slice,
            "c_slice": c_slice,
            "load_slice": a + b - mask,
            "beta": beta,
        }

    def split_dac_voltage(
        self, conv: np.ndarray, k: np.ndarray, sid: np.ndarray, *, cfg: Config | None = None
    ) -> np.ndarray:
        """用实际转换组和主/子 DEM 开关求残差 DAC 电压。

        Args:
            conv: (N, n_active) 物理 slice ID，须与该样本实际采样组一致。
            k: (N,) 细 RDAC 单位码，包含已知 dither；物理求值前钳在
                [0, dac_levels-1]。它不是 coarse code、ADC2 raw code 或 20 位输出码。
            sid: (N,) DEM 状态；split_switch_command 解出主、子顺序及 bridge 选择。
            cfg: 运行时 DEM/掩码控制，不重抽物理电容。

        Returns:
            (N,) RDAC 顶板等效电压 [V]；与存储采样电压使用相同 C_signal 分母。
            每片正负参考电容差先按 beta 折算，求和后乘 v_fs/C_signal。

        Notes:
            DEM 关闭时 sid 归零。分块只限制临时内存，不缓存可能被显式扰动的电容。
        """
        if not self.is_split:
            raise ValueError("split_dac_voltage requires a split pool")
        cfg = self.cfg if cfg is None else cfg
        ids = np.asarray(conv, dtype=np.int64)
        code = np.clip(np.asarray(k, dtype=float), 0, cfg.dac_levels - 1)
        states = (
            np.asarray(sid, dtype=np.int64)
            if cfg.dem_enable
            else np.zeros(len(code), dtype=np.int64)
        )
        result = np.empty(len(code))
        # 每块最多 512 个样本，直接读取物理池，避免电容被显式扰动后使用旧缓存。
        for start in range(0, len(code), 512):
            sl = slice(start, start + 512)
            caps = self.unit_caps[ids[sl]]
            coeff = self.split_coefficients(ids[sl], cfg=cfg)
            command = split_switch_command(cfg, code[sl], states[sl])
            counts = (command.main_counts, command.sub_counts)
            orders = (command.main_order, command.sub_order)
            # charge 暂存带符号电容差 [F]；乘 v_fs 后才成为电荷 [C]。
            charge = np.zeros(caps.shape[:2])
            offset = 0
            for bank, size in enumerate((cfg.dac_n_main, cfg.dac_n_sub)):
                bank_caps = np.take_along_axis(
                    caps[..., offset : offset + size], orders[bank][:, None, :], axis=2
                )
                take = np.clip(counts[bank][..., None] - np.arange(size), 0, 1)
                selected = (bank_caps * take).sum(axis=-1)
                # ±v_fs 参考：正参考电容减负参考电容；子阵列再乘各片 beta。
                q = 2 * selected - bank_caps.sum(axis=-1)
                charge += q if bank == 0 else coeff["beta"] * q
                offset += size
            result[sl] = cfg.v_fs * charge.sum(axis=-1) / coeff["c_signal"]
        return result

    def split_sampling_charge(
        self, conv: np.ndarray, x: np.ndarray, bank_code: np.ndarray, *, cfg: Config | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """计算采样掩码后的存储电压及实际 dither 分量。

        Args:
            conv: (N, n_active) 本次采样的物理 slice ID。
            x: (N,) 外部输入节点的孔径电压 [V]。
            bank_code: (N,) 已知采样 dither 码；离散模式按 2D 个保留单元选择
                D+bank_code 个接 +v_fs，其余接 -v_fs；连续模式按电荷线性插值。
            cfg: 运行时采样掩码；固定为所选阵列末尾 2D 个单元，不读取 sid。

        Returns:
            两个 (N,) 电压 [V]：(alpha*x+injected, injected)，均在 RDAC 顶板
            等效节点。实际 injected 使用保留单元真值，数字端名义扣除不能自动
            消除这些单元的失配。随后重构还需除以名义 alpha 恢复外部输入码域。

        Notes:
            此函数不产生热噪声；调用方用同一组系数的 c_noise 单独采样。
        """
        cfg = self.cfg if cfg is None else cfg
        ids = np.asarray(conv, dtype=np.int64)
        coeff = self.split_coefficients(ids, cfg=cfg)
        injected = np.zeros(len(ids))
        nd = cfg.dither_units_total
        if nd:
            nm = cfg.dac_n_main
            idx = slice(nm - nd, nm) if cfg.dither_split_bank == "main" else slice(-nd, None)
            caps = self.unit_caps[:, idx][ids]
            weights = coeff["beta"] if cfg.dither_split_bank == "sub" else np.ones(ids.shape)
            if cfg.dither_discrete:
                take = np.arange(nd)[None, :] < (nd // 2 + bank_code[:, None])
                q = (caps * (2 * take[:, None, :] - 1)).sum(axis=-1)
            else:
                q = 2 * bank_code[:, None] * caps.mean(axis=-1)
            injected = cfg.v_fs * (q * weights).sum(axis=-1) / coeff["c_signal"]
        return coeff["alpha"] * x + injected, injected

    def selected_capacitance(
        self,
        conv: np.ndarray,
        k: np.ndarray,
        sid: np.ndarray,
    ) -> np.ndarray:
        """求实际开关对应的正参考有效电容 [F]。

        Args:
            conv: (N, n_active) 物理 slice ID。
            k: (N,) RDAC 单位码；unary 先就近取整并钳至 [0, n_units]。
            sid: (N,) DEM 状态，不能代替物理 ID 或 sample ID。

        Returns:
            (N,) 有效电容。unary 把 k 分成每 slot 的商 q 与余数 r，sid 决定
            哪 r 个 slot 多一个单元及片内轮转；split 则从实际桥接 DAC 电压反解
            等效正参考电容，不能把它当作未加 beta 的物理电容直接和。
        """
        conv = np.asarray(conv, dtype=np.int64)
        if self.is_split:
            voltage = self.split_dac_voltage(conv, k, sid)
            return 0.5 * (voltage / self.cfg.v_fs + 1) * self.signal_capacitance(conv)
        n_act = conv.shape[1]
        k = np.rint(np.asarray(k, dtype=float)).astype(np.int64)
        k = np.clip(k, 0, self.n_units)
        sid = np.asarray(sid, dtype=np.int64)

        q, r = np.divmod(k, n_act)
        counts = np.repeat(q[:, None], n_act, axis=1).astype(np.int64)
        sid_s = (sid // self.n_unit_per_slice) % n_act  # 组内哪个 slot 多一个单元
        sid_u = sid % self.n_unit_per_slice  # 每片内部的单元轮转
        for o in range(n_act):
            m = o < r
            if not m.any():
                continue
            j = (sid_s + o) % n_act
            counts[m, j[m]] += 1

        idx = conv * self.n_unit_per_slice + sid_u[:, None]
        sel = np.zeros(conv.shape[0], dtype=float)
        for j in range(n_act):
            sel += self._cum_all[idx[:, j], counts[:, j]]
        return sel

    def dac_voltage(
        self,
        conv: np.ndarray,
        k: np.ndarray,
        sid: np.ndarray,
        *,
        nominal: bool = False,
    ) -> np.ndarray:
        """返回物理或名义 RDAC 顶板等效电压 [V]。

        Args:
            conv: (N, n_active) 物理 slice ID。
            k: (N,) RDAC 单位码；split 名义支路直接应用名义步长，不自动钳码。
            sid: (N,) DEM 状态；仅实际物理支路使用开关映射。
            nominal: True 使用名义单位权重，不读取失配；False 使用固定物理电容。

        Returns:
            (N,) 电压。unary 使用 v_D=v_fs*(2*C_selected/C_total-1)；
            split 物理支路使用逐片桥节点，名义支路为 -v_fs+k*nominal_rdac_step。
            两支路之差是 DAC 误差，不能称作已训练的有效 C/Cf 权重。
        """
        v_fs = float(self.cfg.v_fs)
        if self.is_split:
            if nominal:
                return -v_fs + np.asarray(k, dtype=float) * self.cfg.nominal_rdac_step
            return self.split_dac_voltage(conv, k, sid)
        c_tot: float | np.ndarray
        if nominal:
            c_sel = np.asarray(k, dtype=float) * self.c_unit_nom
            c_tot = self.c_total_nom
        else:
            c_sel = self.selected_capacitance(conv, k, sid)
            c_tot = self.signal_capacitance(conv)
        return v_fs * (2.0 * c_sel / np.maximum(c_tot, 1e-30) - 1.0)

    def dac_error(self, conv: np.ndarray, k: np.ndarray, sid: np.ndarray) -> np.ndarray:
        """返回实际 DAC 电压减名义 DAC 电压的误差 [V]。

        Args:
            conv: (N, n_active) 物理 slice ID。
            k: (N,) RDAC 单位码。
            sid: (N,) DEM 状态。

        Returns:
            (N,) RDAC 顶板等效误差；正值表示实际 DAC 电压更高，因而在
            residue=stored-v_D 中会被减去。此处计算真值，仅供行为 plant 和诊断。
        """
        return self.dac_voltage(conv, k, sid) - self.dac_voltage(conv, k, sid, nominal=True)

    # ---------------------------------------------------------- held charge
    def acquire(
        self,
        acq: np.ndarray,
        x: float,
        *,
        eps: np.ndarray | float = 0.0,
        skew: np.ndarray | None = None,
        offset: np.ndarray | None = None,
        dxdt: float = 0.0,
    ) -> float:
        """把一组 slice 的保持电压向输入目标推进一次。

        Args:
            acq: (n_active,) 物理 slice ID。
            x: 外部输入节点目标电压 [V]。
            eps: 标量或 (n_active,) 建立残留比例；
                v_saved=target-eps*(target-v_previous)。0 表示完全建立。
            skew: 可选 (n_slices,) 静态时偏 [s]。当前 helper 使用选中组时偏
                的平均值乘 dxdt，属于一阶组平均近似，不是逐片孔径波形求值。
            offset: 可选 (n_slices,) 输入等效失调 [V]，按物理 ID 加到各片目标。
            dxdt: 孔径附近外部输入斜率 [V/s]。

        Returns:
            组平均建立误差加平均 offset [V]。未显式加回 skew*dxdt，
            因此不能把返回值当作相对原始 x 的全部孔径误差。

        Side effects:
            仅更新选中片的 v_top 和 held_valid；不登记 held_sample，也不增加
            n_conversions。样本 ID 由 runner 记录，计数由 release 更新。
        """
        acq = np.asarray(acq, dtype=np.int64)
        eps_arr = (
            np.full(acq.size, float(np.asarray(eps, dtype=float)))
            if np.isscalar(eps)
            else np.asarray(eps, dtype=float)
        )
        x_eff = float(x)
        if skew is not None:
            x_eff += float(np.mean(np.asarray(skew)[acq])) * dxdt
        if offset is not None:
            pass  # offset 在下方按物理 slice ID 逐片加入 target。
        v_prev = self.v_top[acq]
        target = np.full(acq.size, x_eff)
        if offset is not None:
            target = target + np.asarray(offset)[acq]
        e = -eps_arr * (target - v_prev)
        self.v_top[acq] = target + e
        self.held_valid[acq] = True
        if offset is not None:
            return float(np.mean(e) + np.mean(np.asarray(offset)[acq]))
        return float(np.mean(e))

    def held_voltage(self, conv: np.ndarray) -> float:
        """读取转换组的保持电压算术均值，不消耗电荷。

        Args:
            conv: (n_active,) 物理 slice ID。

        Returns:
            顶板保持电压的算术均值 [V]，不是 split 电容加权重构。

        Raises:
            ValueError: 任一片 held_valid=False。此检查只确认有效保持状态；
                样本 ID 连续性和采样/转换组归属仍由消费方验证。
        """
        conv = np.asarray(conv, dtype=np.int64)
        if not bool(self.held_valid[conv].all()):
            bad = conv[~self.held_valid[conv]].tolist()
            raise ValueError(f"slices {bad} convert without a completed acquisition")
        return float(np.mean(self.v_top[conv]))

    def release(self, conv: np.ndarray) -> None:
        """标记转换组已消费，允许后续重新采样。

        Args:
            conv: (n_active,) 物理 slice ID。

        Side effects:
            清除选中片 held_valid 并累加 n_conversions；保留 v_top 残差和
            held_sample 历史，便于后续建立和诊断，不把顶板电压复位到零。
        """
        conv = np.asarray(conv, dtype=np.int64)
        self.held_valid[conv] = False
        self.n_conversions[conv] += 1
