# ==============================================================================
# 文件名     : architecture.py
# 模块名     : ad4080_otp_cal.architecture
# 版本       : v1.0
# 功能       : 定义 AD4080 18 片 × 20 物理单元（360 权重）名义模型、二维 DEM/Dither
#              采集前向仿真器，以及与 rtl/sar_core/wsum_v21.sv 同口径的定点重构函数。
# 设计说明   :
#   * [RTL 口径对齐] 参照 wsum_v21.sv：
#       - 18 个物理片（sid=0..17），每片 16 个主阵单元（j=0..15）+ 4 个子阵单元
#         （j=16..19，其中 j=16..18 为 [2Cu, 1Cu, 1Cu]，j=19 为平衡位，计入 Wm、不计入 Wp）。
#       - 每次转换激活 8 片（ACTIVE_SLICES=8），G = 激活 8 片主阵权重之和，
#         Wm = post_mask 选通权重之和，Wp = pre_mask（不含 j=19）选通权重之和。
#   * [归一化单位] 所有模拟电压默认归一化到满量程 [-1.0, +1.0]（对应差分 ±VREF），
#     单次激活 8 片的主阵名义总权重 G_nom = 128.0（每片 16 个单位主电容，每单元名义 1.0）。
# 变更记录   :
#   2026-11-26  v1.0  初版实现。
# ==============================================================================
"""AD4080 18×20 物理权重架构模型与 wsum_v21 同口径前向/重构引擎。"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

# ==============================================================================
# 1. 架构常量（与 rtl/sar_core/wsum_v21.sv 严格一致）
# ==============================================================================
NUM_SLICES: int = 18         # 物理 RDAC 片池总数（8+8+2）
ACTIVE_SLICES: int = 8       # 单次转换激活片数
MAIN_PER_SLICE: int = 16     # 每片主阵单元数（cfg_sel=0, idx=0..15）
SUB_PER_SLICE: int = 4       # 每片子阵单元数（cfg_sel=1, idx=0..3；对应 j=16..19）
WEIGHTS_PER_SLICE: int = MAIN_PER_SLICE + SUB_PER_SLICE  # 20
TOTAL_WEIGHTS: int = NUM_SLICES * WEIGHTS_PER_SLICE      # 360
Q30_SCALE: int = 1 << 30     # Q30 定点缩放因子（2^30 = 1,073,741,824）
ADC_BITS: int = 20           # 目标输出位宽（20-bit 有符号补码）

# 单片内 20 个物理单元的名义电容比（单位：1.0 = 1 主阵单位电容 Cu_main）
# 主阵 16 个一元单元各 1.0；子阵 [2Cu_sub, 1Cu_sub, 1Cu_sub, 平衡位] 折合为 [0.25, 0.125, 0.125, 0.0625]
NOMINAL_SLICE_WEIGHTS = np.array(
    [1.0] * MAIN_PER_SLICE + [0.25, 0.125, 0.125, 0.0625],
    dtype=np.float64,
)


@dataclass(frozen=True)
class CaptureDataset:
    """一次 AD4080 采集数据集（兼容真实板卡观测导出与行为仿真生成）。

    Attributes:
        vin_true: 真实输入电压（归一化到 [-1, +1]），实测未知时可为 None。
        active_ids: shape (N, 8)，int32，每笔激活的 8 个物理片号（0..17）。
        post_mask: shape (N, 8, 20)，uint8，每笔激活 8 片的 20-bit 最终开关态（0/1）。
        pre_mask: shape (N, 8, 4)，uint8，每笔激活 8 片的 4-bit 预置扰动态（j=19 不计入 Wp）。
        adc2_residue: shape (N,)，float64，折回输入端的第二级细量化余差（归一化 [-1, +1] 量纲）。
        raw_codes_uncal: shape (N,)，int32，使用名义权重重构的未校准 20-bit 输出码。
        fs_hz: 采样率（Hz），默认 40e6（40 MSPS）。
        fin_hz: 标定正弦输入频率（Hz），未知时为 0.0。
    """

    active_ids: np.ndarray
    post_mask: np.ndarray
    pre_mask: np.ndarray
    adc2_residue: np.ndarray
    raw_codes_uncal: np.ndarray
    fs_hz: float = 40e6
    fin_hz: float = 0.0
    vin_true: np.ndarray | None = None


# ==============================================================================
# 2. 名义权重与失配注入
# ==============================================================================
def nominal_weights_18x20() -> np.ndarray:
    """返回 shape=(18, 20) 的名义物理电容权重矩阵（单位：Cu_main）。"""
    return np.tile(NOMINAL_SLICE_WEIGHTS, (NUM_SLICES, 1)).copy()


def inject_capacitor_mismatch(
    sigma_rel: float = 1.5e-3,
    seed: int = 20261126,
    gradient_ppm_per_slice: float = 120.0,
) -> np.ndarray:
    """在名义 18×20 电容阵上注入随机失配 + 跨片线性工艺梯度。

    Args:
        sigma_rel: 单位电容相对失配标准差（无量纲，例如 1.5e-3 代表 0.15% 1-sigma）。
        seed: 随机数种子，保证实验完全可复现。
        gradient_ppm_per_slice: 跨 18 片的线性工艺梯度（单位：ppm/片，用于模拟裸片梯度）。

    Returns:
        shape=(18, 20) 的真实物理电容权重矩阵（单位：Cu_main）。
    """
    rng = np.random.default_rng(seed)
    w_nom = nominal_weights_18x20()
    # 物理电容失配遵循 Pelgrom 定律：较小电容（子阵）相对标准差按 1/sqrt(C) 放大
    rel_scale = sigma_rel / np.sqrt(np.maximum(w_nom, 0.0625))
    rand_err = rng.normal(0.0, rel_scale)
    slice_idx = (np.arange(NUM_SLICES, dtype=np.float64) - 8.5)[:, None]
    grad_err = slice_idx * (gradient_ppm_per_slice * 1e-6)
    return w_nom * (1.0 + rand_err + grad_err)


# ==============================================================================
# 3. wsum_v21 同口径重构与设计矩阵展开
# ==============================================================================
def build_physical_activity_matrix(
    active_ids: np.ndarray,
    post_mask: np.ndarray,
    pre_mask: np.ndarray,
) -> np.ndarray:
    """将每笔 (active_ids, post_mask, pre_mask) 展开为 N×360 的净物理开关设计矩阵。

    设计理由（对齐 wsum_v21.sv）：
      * 对主阵 j=0..15：净贡献为 2 * post - 1（差分正负参考切换，总和除以 G 后落入 [-1, +1]）。
      * 对子阵 j=16..18：净贡献为 2 * (post - pre)（预置扰动 Wp 在重构中精确抵消）。
      * 对平衡位 j=19：按 wsum_v21.sv 注释，j=19 计入 Wm、不计入 Wp，故净贡献为 2 * post - 1（或仅 post）。
        为与全差分切换严格对称，此处采用差分净系数：主阵 (2*post - 1)，子阵 16..18 为 2*(post - pre)，
        j=19 为 2*post（扣除常值均值由回归截距项 b 吸收）。

    Returns:
        shape=(N, 360) 的 float64 设计矩阵 A，列索引 = sid * 20 + j。
    """
    n_samples = active_ids.shape[0]
    a_mat = np.zeros((n_samples, TOTAL_WEIGHTS), dtype=np.float64)

    # 向量化展开：每笔 8 个激活片直接映射到全局 360 列
    row_idx = np.repeat(np.arange(n_samples), ACTIVE_SLICES)
    sids = active_ids.reshape(-1)  # (N*8,)
    post_flat = post_mask.reshape(-1, WEIGHTS_PER_SLICE).astype(np.float64)
    pre_flat = pre_mask.reshape(-1, SUB_PER_SLICE).astype(np.float64)

    # 构造单片 20 维净开关向量
    net_20 = np.empty_like(post_flat)
    net_20[:, :MAIN_PER_SLICE] = 2.0 * post_flat[:, :MAIN_PER_SLICE] - 1.0
    net_20[:, MAIN_PER_SLICE:MAIN_PER_SLICE + 3] = 2.0 * (
        post_flat[:, MAIN_PER_SLICE:MAIN_PER_SLICE + 3] - pre_flat[:, :3]
    )
    net_20[:, 19] = 2.0 * post_flat[:, 19] - 1.0

    # 按物理片号写入 360 列矩阵
    col_base = (sids * WEIGHTS_PER_SLICE)[:, None] + np.arange(WEIGHTS_PER_SLICE)[None, :]
    a_mat[row_idx[:, None], col_base] = net_20
    return a_mat


def reconstruct_wsum_v21(
    active_ids: np.ndarray,
    post_mask: np.ndarray,
    pre_mask: np.ndarray,
    adc2_residue: np.ndarray,
    weights_18x20: np.ndarray,
    offset: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """按 wsum_v21 + recon_v21 同口径，用给定 18×20 权重重构浮点值与 20-bit 有符号码。

    Args:
        active_ids: shape (N, 8)，激活物理片号。
        post_mask: shape (N, 8, 20)，最终开关掩码。
        pre_mask: shape (N, 8, 4)，预置扰动掩码。
        adc2_residue: shape (N,)，折回输入端的第二级细量化余差。
        weights_18x20: shape (18, 20)，物理电容权重（浮点或已反量化 Q30）。
        offset: 直流失调修正量（归一化满量程单位）。

    Returns:
        (x_hat, codes_20b):
            x_hat: shape (N,)，归一化重构电压（约在 [-1, +1]）；
            codes_20b: shape (N,)，int32，20-bit 有符号饱和码（范围 [-524288, +524287]）。
    """
    w_flat = np.asarray(weights_18x20, dtype=np.float64).reshape(TOTAL_WEIGHTS)
    w_main_row_sum = np.sum(weights_18x20[:, :MAIN_PER_SLICE], axis=1)  # (18,)
    # 每笔动态 G = 实际激活 8 片主阵权重之和（与 wsum_v21.sv 一致）
    g_per_sample = np.sum(w_main_row_sum[active_ids], axis=1)  # (N,)

    a_mat = build_physical_activity_matrix(active_ids, post_mask, pre_mask)
    dac_contrib = (a_mat @ w_flat) / g_per_sample
    x_hat = dac_contrib + adc2_residue + offset

    # 20-bit 有符号量化并饱和截幅
    full_scale_half = float(1 << (ADC_BITS - 1))
    codes = np.rint(x_hat * full_scale_half).astype(np.int64)
    codes = np.clip(codes, -(1 << (ADC_BITS - 1)), (1 << (ADC_BITS - 1)) - 1).astype(np.int32)
    return x_hat, codes


# ==============================================================================
# 4. 含二维 DEM 与 6-bit Dither 的 AD4080 前向采集仿真器
# ==============================================================================
def simulate_ad4080_capture(
    vin: np.ndarray,
    true_weights_18x20: np.ndarray,
    fs_hz: float = 40e6,
    fin_hz: float = 0.0,
    noise_rms: float = 1.2e-5,
    offset_true: float = 1.5e-4,
    adc2_bits: int = 13,
    seed: int = 42,
) -> CaptureDataset:
    """模拟 AD4080 在二维 DEM + 6-bit Dither 下的真实采集过程。

    Args:
        vin: shape (N,)，输入差分电压（归一化 [-1, +1]，建议幅值 0.90 留足 dither 裕量）。
        true_weights_18x20: shape (18, 20)，含物理失配的真实电容权重。
        fs_hz: 采样率（Hz）。
        fin_hz: 输入正弦频率（Hz，供后续标定使用）。
        noise_rms: 等效输入热噪声 RMS（归一化满量程，1.2e-5 对应约 95 dB 噪底）。
        offset_true: 真实直流失调（归一化满量程）。
        adc2_bits: 第二级 ADC2 有效位宽（默认 13-bit，配合 ×32 增益覆盖 20-bit 动态范围）。
        seed: 随机种子。

    Returns:
        CaptureDataset，包含每笔物理开关态、细余差与未校准输出码。
    """
    rng = np.random.default_rng(seed)
    n_samples = len(vin)
    w_true = np.asarray(true_weights_18x20, dtype=np.float64)
    w_nom = nominal_weights_18x20()
    g_true_row = np.sum(w_true[:, :MAIN_PER_SLICE], axis=1)

    active_ids = np.zeros((n_samples, ACTIVE_SLICES), dtype=np.int32)
    post_mask = np.zeros((n_samples, ACTIVE_SLICES, WEIGHTS_PER_SLICE), dtype=np.uint8)
    pre_mask = np.zeros((n_samples, ACTIVE_SLICES, SUB_PER_SLICE), dtype=np.uint8)
    adc2_residue = np.zeros(n_samples, dtype=np.float64)

    # 生成覆盖全部 64 种扰动字的伪随机 6-bit dither 序列
    dither_6b = (np.arange(n_samples) * 19 + rng.integers(0, 64, size=n_samples)) & 0x3F
    thermal_noise = rng.normal(0.0, noise_rms, size=n_samples)

    # 跨片起始指针（二维 DEM 第一维：跨 18 片旋转选 8 片）
    slice_ptr = 0
    elem_ptr = 0
    adc2_lsb = (2.0 / 32.0) / float(1 << adc2_bits)

    for n in range(n_samples):
        # 1) 18 片池轮换选出 8 片（模拟 sched18 + 跨片 DEM）
        sids = (np.arange(ACTIVE_SLICES) + slice_ptr) % NUM_SLICES
        slice_ptr = (slice_ptr + 5) % NUM_SLICES  # 互质步进保证 18 片均匀覆盖
        active_ids[n] = sids

        # 2) 6-bit dither 拆为前 3-bit 整数 + 后 3-bit 分数注入子阵 pre_mask
        d_val = int(dither_6b[n])
        for s in range(ACTIVE_SLICES):
            pre_mask[n, s, 0] = (d_val >> 5) & 1
            pre_mask[n, s, 1] = (d_val >> 4) & 1
            pre_mask[n, s, 2] = ((d_val >> 3) ^ (s & 1)) & 1
            pre_mask[n, s, 3] = (d_val >> 2) & 1

        # 3) 粗量化器决定主阵开启单元数 k_main（0..128）与子阵逼近位
        v_eff = float(vin[n] + offset_true + thermal_noise[n])
        k_main = int(np.clip(np.floor((v_eff + 1.0) * 64.0), 0, 128))

        # 二维 DEM 第二维：在激活 8 片的 128 个主阵单元内按旋转指针分配开启位
        for idx_128 in range(k_main):
            pos = (elem_ptr + idx_128) % 128
            s_idx = pos % ACTIVE_SLICES
            j_idx = pos // ACTIVE_SLICES
            post_mask[n, s_idx, j_idx] = 1
        elem_ptr = (elem_ptr + max(k_main, 1)) % 128

        # 子阵 4 位（j=16..19）基于剩余余差做粗逼近（模拟 SADC 子阵位）
        g_true = float(np.sum(g_true_row[sids]))
        main_net = 2.0 * post_mask[n, :, :MAIN_PER_SLICE].astype(np.float64) - 1.0
        v_after_main = v_eff - float(np.sum(main_net * w_true[sids, :MAIN_PER_SLICE])) / g_true

        # 逐位决定子阵 j=16..19 的 8 片开关（同时保留一定逐片扰动以激发全部 360 列满秩）
        sub_target = v_after_main
        for j_sub in range(SUB_PER_SLICE):
            j_abs = MAIN_PER_SLICE + j_sub
            bit_val = 1 if sub_target >= 0.0 else 0
            for s in range(ACTIVE_SLICES):
                # 对末位平衡位 j=19 加入伪随机奇偶切变，保证所有 18×4 子阵单元在标定中充分激励
                if j_sub == 3:
                    p_bit = (bit_val ^ ((d_val + s) & 1)) & 1
                else:
                    p_bit = bit_val
                post_mask[n, s, j_abs] = p_bit
            step_w = float(np.sum(w_true[sids, j_abs])) / g_true
            sub_target -= (2.0 * bit_val - 1.0) * step_w

        # 4) 计算真实物理电容建立后的模拟余差，经 ADC2 细量化
        a_row = build_physical_activity_matrix(
            active_ids[n : n + 1], post_mask[n : n + 1], pre_mask[n : n + 1]
        )[0]
        v_dac_true = float(a_row @ w_true.reshape(-1)) / g_true
        v_res = v_eff - v_dac_true
        adc2_residue[n] = np.rint(v_res / adc2_lsb) * adc2_lsb

    # 未校准基线：用名义权重做重构
    _, raw_uncal = reconstruct_wsum_v21(
        active_ids, post_mask, pre_mask, adc2_residue, w_nom, offset=0.0
    )

    return CaptureDataset(
        active_ids=active_ids,
        post_mask=post_mask,
        pre_mask=pre_mask,
        adc2_residue=adc2_residue,
        raw_codes_uncal=raw_uncal,
        fs_hz=fs_hz,
        fin_hz=fin_hz,
        vin_true=vin.copy(),
    )
