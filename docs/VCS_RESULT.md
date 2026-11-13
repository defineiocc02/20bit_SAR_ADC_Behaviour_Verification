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
