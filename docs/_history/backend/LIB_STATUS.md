# 工艺库可用性核查（2026-11-14，vm-meow）

来源：`/home/yian/PDK`，并对照该目录下的 `LIBRARY_INVENTORY.md`（2026-11-08）。

## 结论速览

| 工艺 | 标准单元 | SRAM / RF | IO | 能否综合完整顶层 |
|:---|:---|:---|:---|:---|
| **SMIC 180 MSERF** | ✅ fast/typ/slow 的 `.lib`+`.db` | ❌ **无任何存储库** | ✅ SP018N | 能跑，但存储只能用触发器 |
| **TSMC 28 HPC+** | ✅ `tcbn28hpcplusbwp7t40p140`，ss/ff/tt 的 CCS `.lib`+`.db` | ✅ **打包齐全**（见下） | ✅（extracted LEF 缺） | 可以，但需先解包 |

## SMIC 180 的具体情况

- 标准单元：`smic180/process_and_digital/standard_cells/synopsys/{fast,typical,slow}.{lib,db}`
- IO：`io/synopsys/SP018N_V1p0_{min,typ,max}.lib/.db`，LEF/Verilog 齐
- **存储：无**。清单原文：「no memory or eFuse library identified in this SMIC source」
- 因此 16.6 kbit 系数表只能映射为触发器阵列

## TSMC 28 HPC+ 的具体情况

标准单元（已解包，可直接用）：

```
digital_technology/TSMCHOME/digital/Front_End/timing_power_noise/CCS/
  tcbn28hpcplusbwp7t40p140_180a/   ss/ff/tt 的 .db 与 .lib（含 hvt 变体）
```

存储（**均为 tar.gz，尚未解包**）：

| 包 | 用途 | 关键文件 |
|:---|:---|:---|
| `temn28hpcphssrammacros_110a` | SRAM 宏 | `_nldm.tar.gz`(61 MB，时序)、`_vlg`、`_gds`、`_spi`、`_sef` |
| `tsn28hpcp1prf_20120200_130a` | **单口寄存器文件编译器** | 宏 tarball + doc + rln |
| `tsn28hpcp2prf_...` | 双口 RF 编译器 | 同上 |
| `tsmc_n28hpcpmc_20120200_110a` | 内存编译器 | 编译器 tarball + rln |

## 对当前项目的判断

**180 nm 不够用于"完整数字顶层 40 MS/s"**，理由两条：

1. **面积**：按 `recon_d5_w2` 实测 166,799 µm² / 4,940 cells（≈33.8 µm²/cell），
   16.6 kbit 触发器阵列按 70 µm²/bit 估 ≈ **1.16 mm²**，加重构逻辑约 **1.3 mm²**。
2. **时序**：单是重构块在 25 ns 下 slack 仅 +0.002 ns。再叠加
   8×20 系数查表 + 44 位累加 + 16.6 kbit 寄存器阵列读出，**几乎必然违例**。

**180 nm 仍然值得先把完整顶层跑一遍**：它能给出真实面积/关键路径基线，
证明"系数存储占比"这个数字，成本低且不依赖解包 28 nm 库。

## 28 nm 侧的前置工作（未做）

1. 解包 SRAM/RF 的 `nldm`/`vlg`/`lef`，拿到可用宏清单（容量 × 端口 × 时序）
2. 确认内存编译器是否需要额外 license（未验证）
3. 28 nm IO 的 extracted LEF 缺失，顶层带 IO 时需另找
4. 重新做 SDC（25 ns 在 28 nm 下过于宽松，应改 1~2 ns 或按 40 MS/s 重算）
