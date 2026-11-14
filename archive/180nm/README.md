# SMIC 180 nm 历史工程（归档）

**状态：已归档，不再作为当前目标工艺。**

切换原因：180 nm 无法支撑完整数字顶层的 40 MS/s 与面积目标。

| 事实 | 数值 |
|:---|:---|
| 重构块面积 | 166,799 µm² / 4,940 cells（≈33.8 µm²/cell） |
| 系数表 16.6 kbit（触发器）估算 | ≈1.16 mm² |
| 完整顶层估算 | ≈1.3 mm² |
| 重构块 25 ns slack | +0.002 ns（几乎无余量） |
| 存储库 | **无**（SMIC180 无任何 SRAM/eFuse 库） |

## 保留的历史证据（未删除）

| 内容 | 位置 |
|:---|:---|
| 重构块综合与时序 | `synth/results/w2/`（setup/hold/stage1~3/area/check_timing） |
| 名义路径时序 | `synth/results/setup_p_pipe_constrained.rpt` 等 |
| 失败候选（差 16.5 倍） | `rtl/recon_d5_pipe2.sv`、`docs/TIMING_PIPE2.md` |
| 倒数的发现过程 | `docs/TIMING_W2.md`、`docs/D5_PROD_VS_RTL.md` |
| 库清单 | `docs/LIB_STATUS.md` |

## 这些数字仍然有效，但用途变了

180 nm 的 **相对结论** 仍然成立：倒数乘把 421 ns 降到 24.46 ns、面积降 56%、
`DW_div` 计数归零。这些是算法与微架构层面的结论，与工艺无关。

**不能再引用的是绝对面积/时序**——那属于 180 nm，不代表 28 nm 的实现。

## 当前目标工艺

TSMC 28 HPC+（`tcbn28hpcplusbwp7t40p140`），SRAM/RF 库已解包，
详见 `docs/LIB_STATUS.md`。
