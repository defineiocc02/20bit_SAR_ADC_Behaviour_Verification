# VCS 仿真验证结果（全部通过）

工具：**VCS W-2024.09-SP1**，`SNPSLMD_LICENSE_FILE=27080@localhost`
日期：2026-11-13 · 基线提交 `7a66156`

## 结果

| 模块 | 向量数 | 失配 | 结果 |
|:---|---:|---:|:--|
| `cdac_decode` | 512 | 0 | **PASS** |
| `dither_split` | 64 | 0 | **PASS** |
| `dem_shuffle` | 8192 | 0 | **PASS** |
| `slice_pool` | 64 | 0 | **PASS** |
| `follower` | 200 | 0 | **PASS** |
| `recon_d5` | 1987 | 0 | **PASS** |
| `digital_top`（六路输出） | 600 | 0 | **PASS** |
| **合计** | **11619** | **0** | |

## 意义

此前所有对拍都在 Icarus 12.0 上完成。这次换成商业仿真器重跑，
**同一批 RTL、同一批向量，结果完全一致**——排除了"Icarus 特有行为"的可能。

golden 来源：`src/adi_model/digital_chain.py` 的整数参考
（`_mask_bits` / `_horizontal_columns` / `vertical_bits` / `_dither_terms` / `reconstruct_input_q24`）。

## 复现

```bash
mkdir -p ~/vcs && cd ~/vcs
scp <repo>/rtl/*.sv <repo>/tb/*.sv .
# 向量按 tb 期望的文件名放好（vec.txt / top_vec.txt / dem_vec.txt ...）
export SNPSLMD_LICENSE_FILE=27080@localhost VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1
$VCS_HOME/bin/vcs -full64 -sverilog -timescale=1ns/1ps -o simv <rtl> <tb> && ./simv
```

## 边界

- 这是**功能对拍**，不是形式化等价；只覆盖向量覆盖到的输入空间。
- `recon_d5_w`（权重路径）**不在本轮 VCS 覆盖内**，它仍是 398/400。
- 未做：门级网表仿真、SDF 反标、时序仿真。

---

## 补充（2026-11-13 23:45）：`recon_d5_w` 权重路径 400/400 通过

### 根因（审阅定位，实测复核）

Python 的 `r_hat_q24` 里写了 `_div_round(n + (1<<21), 1<<22)`，
而 `_div_round` 内部**还会加 `d//2 = 2^21`** —— 等于加了两次舍入偏置。
RTL 只加一次（`+2097152` 后 `>>>22`）。

差异不在 DAC 除法，也不在最后的 `/96`：遍历 4096 个 ADC2 码，
两边余差项有 **2458 个不同**，只是多数没跨过最终 20 位舍入边界，
所以只暴露成 2 个输出码差。

### 修法（用仓库已有定义，不新增规格）

```python
r_hat_q24 = ((2 * adc2_code + 1) * 144 + 2) // 5      # 精确除 5
```
```systemverilog
rc = ((2*adc2_i + 1) * 144 + 2) * 838861;   // 不再加 2^21
r_q24 = rc >>> 22;
```

### 验证

| 检查 | 结果 |
|:---|:---|
| 4096 个 ADC2 码：Python 精确除 5 vs RTL 乘移 | **4096/4096 一致** |
| 400 组完整向量（VCS W-2024.09） | **400 组 0 失配** |
