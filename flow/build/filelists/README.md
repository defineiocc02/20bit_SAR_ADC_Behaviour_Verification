# 编译清单（filelists）

从前这四个 `.f` 直接放在仓库根目录，和 `README.md`、`Makefile` 混在一起，
很容易误用错的那一个。现在统一收在这里。

## 四个清单分别是什么

| 清单 | 顶层 | 对应哪条链 | 验证入口 |
|:---|:---|:---|:---|
| `files.f` | `digital_top` | **历史链**（26 个源文件，含 `rtl/core/*`） | 无统一入口，仅供追溯 |
| `files_d3d4.f` | `tb_dem_dither` | **叶子级对拍**（3 个：`dem_mask_v3` + `dither_pre` + TB） | `bash tools/verify_d3d4.sh` |
| `files_d3d4_ip.f` | `d3d4_ip` | **本轮交付的数字 IP**（7 个：D1–D4 + 相位发生器） | `bash tools/verify_d3d4_ip.sh` |
| `files_topw.f` | `digital_top_w` | **旧完整链**（D1–D6，含 D5 重构与 20 位码输出） | `bash run_topw.sh` |

## 怎么选

```
只想验 D3/D4 两个叶子模块        -> files_d3d4.f
要综合/门级验证 d3d4_ip          -> files_d3d4_ip.f
要看 20 位码输出（D1-D6 全链）   -> files_topw.f
查历史实现（rtl/core/*）         -> files.f
```

## 两条链的关键区别（最容易搞混的点）

| | `files_d3d4_ip.f`（新） | `files_topw.f`（旧） |
|:---|:---|:---|
| D3 DEM | `dem_mask_v3`（**3+3+3**） | `dem_shuffle`（4+3+2） |
| D4 Dither | `dither_pre`（`c+U`） | `dither_split`（`c−U`） |
| D5 权重重构 | **无** | `recon_d5_w2` + `recip_q60` |
| 输出 | `mask_o` / `pre_o`（开关命令） | `code_o[19:0]`（20 位码） |
| 综合 | 已做（3017 cells / 1770 µm² / 2.82 ns） | 旧结果在 `synth/results/` |

**归纳一句**：新链口径正确但只到开关命令层；旧链有 20 位码输出但用的是过期映射与符号。

## 用法注意

清单里的路径是**相对仓库根目录**的（如 `rtl/dem_mask_v3.sv`）。
因此 `-f` 必须在仓库根执行：

```bash
cd <repo>
vcs -full64 -sverilog -timescale=1ns/1ps -f build/filelists/files_d3d4_ip.f ...
```

`tools/verify_d3d4.sh` / `tools/verify_d3d4_ip.sh` / `run_topw.sh` 都已经按这个约定处理好，
远端模式会把清单拷到与 `-f` 一致的相对位置。

## 加文件时

新增 RTL 要同步改清单，并且**只改它真正属于的那一个**。
改错了不会报错，只会让另一条链编译失败或者静默少编一个模块。
