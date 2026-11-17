# 效果图（2026-11-17 实际生成）

## 生成方式（可复现）

```bash
bash tools/pyenv.sh --run tools/run_review_figures.py --out docs/figures/20261117
```

**本文档中的 12 张图是本轮实际跑出来的，不是历史副本。**
（仓库 `tools/results/fig/` 下另有一批 2026-11-07 的旧图，与本文档无关。）

## 图清单

| 文件 | 内容 | 状态 |
|:---|:---|:---|
| `F01_ownership_events.png` | 片归属：每拍 8 采集 / 8 转换 / 2 空闲；RDAC 只移动到已解出码 | ✅ 可读 |
| `F02_dem_usage.png` | DEM 单元使用分布 | ✅ 可读 |
| `F03_inl_dnl.png` | INL/DNL（512 电平 × 8 重复）| ⚠️ **见下方已知问题** |
| `F04_psd.png` | 相干正弦 1.001 MHz 的 PSD（N=8192）| ✅ 可读 |
| `F05_dither_margin.png` | dither 余量 | ✅ |
| `F06_follower_charge.png` | follower 电荷 | ✅ |
| `F07_reference_ra_adc2.png` | 参考/RDAC/ADC2 | ✅ |
| `F08_az.png` | 自动调零 | ✅ |
| `F09_input_current.png` | 输入电流 | ✅ |
| `F10_interleaving.png` | 交织 | ✅ |
| `F11_calibration_generalization.png` | 校准泛化 | ✅ |
| `F12_fixed_vs_float.png` | 定点 vs 浮点 | ✅ |

## 与论文原文的对应（F01）

`F01a` 直接显示 **8 convert（18 之一）/ 8 acquire / 2 idle**。

论文第 1 页原文：

> "the RDAC is composed of **8 slice DACs (sDAC) converting the signal**, and
> **8 sDACs acquiring the signal**, dynamically selected from **a pool of 18 sDACs**.
> **Two spare sDACs** are introduced to allow shuffling..."

**这是功能与原文一致的直接可视化证据。**

## 已知问题

### F03 的绘图缺陷（未修复）

`F03_inl_dnl.png` 存在两个问题：

1. **曲线严重重叠**：9 条曲线（A0/A2/A3/A4/A5/A6/C1/C2/C3）几乎完全重叠成一片，
   无法区分单条曲线。
2. **量级失衡**：图例标注 `A0 ideal (max 0.6)`，而 `A6 dither` 的 max 为 **4145.9** ——
   scale 差约 7000 倍，导致理想曲线被压平在 0 附近看不见。

**这张图目前不可用于汇报。** 若要可用，需要：

- 拆成子图（按 A/C 分组，或 ideal 单独一张）
- 或改用对数纵轴 / 分面

**本轮未修改绘图脚本。**

## 数据来源

图的输入数据由 `tools/run_review_figures.py` 内部调用生成，
**不是**从论文截图，也不是手绘示意。

**注意**：这些图是**行为模型层面**的结果，不是硅实测，也不是后仿真。
