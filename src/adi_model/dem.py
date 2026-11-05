"""Split RDAC的数字开关命令层：只读名义几何，不读取或估计真实电容。

输入是RDAC单位码k和9位DEM状态sid；输出主/子阵列的物理单元顺序及每个活动slot的选通数。
物理slice ID另由调度器提供，slot不是固定物理slice。当前63+8候选通过8×8行列旋转、
删除缺失cell63及可选跨slot零和bridge实现。8个slot的名义平均码守恒，不等于每个物理单元等使用，
也不等于失配电荷相等。k=10遍历512状态时，主单元56被选16次、其余主单元各8次；
sid[8]门控又与子阵列旋转相关。真实物理使用分布还须结合allocator统计，不能从守恒式推断。
电容权重训练、输入时序误差估计和动态建立均不属于本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

import numpy as np

from .config import Config


@dataclass(frozen=True)
class SplitSwitchCommand:
    """压缩的物理开关命令，供物理plant与冻结位权重构共同解释。

    main_order/sub_order: (N, bank_size)，按选通先后排列的物理unit ID；
    main_counts/sub_counts: (N, n_active)，逐样本、逐逻辑slot的单位数。
    整数命令的mask是order前count个元素；分数count仅用于连续插值基准，不是可直接实现的二进制开关。
    将slot归到物理slice时必须使用同笔样本的ID。
    """

    main_order: np.ndarray
    sub_order: np.ndarray
    main_counts: np.ndarray
    sub_counts: np.ndarray


@dataclass(frozen=True)
class SplitSwitchGeometry:
    """生成mask所需的名义数字几何与开关；不含制造失配或学习得到的权重。"""

    dac_n_main: int
    dac_n_sub: int
    n_active: int
    dac_complete_range: bool
    dem_enable: bool
    dem_bridge_enable: bool

    @property
    def dac_levels(self) -> int:
        """返回名义RDAC电平数；complete_range允许主阵列选到全部单元。"""
        return (self.dac_n_main + int(self.dac_complete_range)) * self.dac_n_sub


def split_switch_command(
    cfg: Config | SplitSwitchGeometry, k: np.ndarray, sid: np.ndarray
) -> SplitSwitchCommand:
    """由RDAC单位码和DEM状态生成行/列/子阵列置换及可选零和bridge。

    Args:
        cfg: 名义几何/控制；不得用真实电容或拟合权重决定选通顺序。
        k: (N,) RDAC单位码；当前结构为0..511，越界值显式裁剪。整数产生物理mask；分数只表示插值。
        sid: (N,) 数字状态，调用合同为整数0..511；本函数转为int64。

    Returns:
        SplitSwitchCommand。63主单元嵌入8×8后跳过物理cell63，8子单元环旋转。
        dem_enable=False时按sid=0译码。当前8活动slot在bridge使能且sid[8]=1时
        进行4正/4负零和交换；整数内部码幅度1、端码0，分数按距端点限幅。因此每样本
        sum(8*main_count+sub_count)=8*k_clipped，但使用频次可有条件偏置。
        该bridge及状态编码是工程合同，不是原芯片开关矩阵的复原。

    Raises:
        ValueError: k非有限、不是一维或sid与k形状不同。

    Side effects:
        无；不读取物理真值、不学习权重，不报告模拟溢出或性能。
    """
    code = np.asarray(k, dtype=float)
    states = np.asarray(sid, dtype=np.int64)
    if code.ndim != 1 or states.shape != code.shape or not np.all(np.isfinite(code)):
        raise ValueError("DEM codes and states must be finite, equal-length vectors")
    code = np.clip(code, 0, cfg.dac_levels - 1)
    if not cfg.dem_enable:
        states = np.zeros_like(states)
    nm, ns = cfg.dac_n_main, cfg.dac_n_sub
    width = ceil(sqrt(nm))
    height = ceil(nm / width)
    cells = np.arange(height * width)
    row, col = np.divmod(cells, width)
    rr = (row[None, :] + (states // width % height)[:, None]) % height
    cc = (col[None, :] + (states % width)[:, None]) % width
    order = rr * width + cc
    main_order = order[order < nm].reshape(len(code), nm)
    sub_order = (np.arange(ns)[None, :] + (states // (width * height))[:, None]) % ns
    slice_codes = np.broadcast_to(code[:, None], (len(code), cfg.n_active)).copy()
    if cfg.dem_enable and cfg.dem_bridge_enable:
        rank = (np.arange(cfg.n_active)[None, :] - (states % cfg.n_active)[:, None]) % cfg.n_active
        half = cfg.n_active // 2
        signs = (rank < half).astype(float) - ((rank >= half) & (rank < 2 * half))
        amount = np.minimum(1, np.minimum(code, cfg.dac_levels - 1 - code))
        amount *= (states & 0x100) != 0
        slice_codes += signs * amount[:, None]
    main_counts = np.floor(slice_codes / ns)
    sub_counts = slice_codes - ns * main_counts
    return SplitSwitchCommand(main_order, sub_order, main_counts, sub_counts)
