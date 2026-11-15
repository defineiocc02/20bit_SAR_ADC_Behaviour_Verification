# 仓库地图

**打开仓库先看这一页。** 根目录有 40+ 项，但真正需要你操心的只有 8 个文件。

---

## 一、我该看什么

### 如果你是来读代码的 → 看这 8 个

| # | 文件 | 一句话 |
|:--:|:---|:---|
| 1 | [`rtl/d3d4_ip.sv`](rtl/d3d4_ip.sv) | **IP 顶层**：输入 k + dither，输出物理开关命令 |
| 2 | [`rtl/sched18.sv`](rtl/sched18.sv) | **D1** 18 片池调度 |
| 3 | [`rtl/follower_rt.sv`](rtl/follower_rt.sv) | **D2** RDAC follower 逐事件载入 |
| 4 | [`rtl/dem_mask_v3.sv`](rtl/dem_mask_v3.sv) | **D3** DEM 映射 |
| 5 | [`rtl/dem_phase_rr.sv`](rtl/dem_phase_rr.sv) | **D3** 相位发生器 |
| 6 | [`rtl/dither_pre.sv`](rtl/dither_pre.sv) | **D4** Split Dither |
| 7 | [`src/adi_model/dem_dither.py`](src/adi_model/dem_dither.py) | 唯一参考实现 |
| 8 | [`tools/verify_d3d4_ip.sh`](tools/verify_d3d4_ip.sh) | 唯一验收入口 |

### 如果你是来验收的 → 一条命令

```bash
D3D4_RUNNER=<ssh-host> bash tools/verify_d3d4_ip.sh
```

四层都要打印 `RESULT: PASS`。L3 需要额外给综合网表（见 `docs/SYNTH_D3D4.md`）。

### 如果你是来接手的 → 三份文档

1. [`docs/D1_D6_STATUS_20261115.md`](docs/D1_D6_STATUS_20261115.md) —— D1–D6 状态、原文定位、剩余缺口
2. [`docs/D3D4_IP_INTERFACE.md`](docs/D3D4_IP_INTERFACE.md) —— 端口/时序/位序/合法性标志
3. [`docs/HANDOFF_D3D4.md`](docs/HANDOFF_D3D4.md) —— 哪里最可能错

---

## 二、根目录 40+ 项，分四类

### A. 核心（7 个目录）

| 目录 | 文件数 | 装什么 |
|:---|--:|:---|
| `rtl/` | 39 | RTL 源码。**只看前 6 个 .sv**，其余是旧链与历史模块 |
| `tb/` | 47 | testbench + 金标向量。核心是 `tb_d3d4_ip` / `tb_event_align` / `tb_dem_dither` |
| `tools/` | 74 | 验收脚本、金标生成器、统计工具 |
| `src/` | 95 | Python 参考模型。核心是 `adi_model/dem_dither.py` |
| `synth/` | 19 | 综合脚本与 SDC（`synth/d3d4/`） |
| `build/` | 5 | 4 个编译清单（**先读 `build/filelists/README.md`**） |
| `docs/` | 257 | 顶层 14 份当前文档 + `_history/` 98 份历史 |

### B. 辅助（用到才看，4 个目录）

| 目录 | 文件数 | 什么时候用 |
|:---|--:|:---|
| `tests/` | 125 | CI 跑 pytest。改动 `src/` 后必跑 |
| `sim/` | 46 | CI 用 `sim/fixtures/` 做 RTL 比对 |
| `verification/` | 80 | 验证基线与向量生成 |
| `scripts/` | 3 | `clean.py` 清理临时产物（默认 dry-run） |

### C. 外围子系统（与本任务无关，4 个目录）

| 目录 | 文件数 | 是什么 | 为什么在仓库里 |
|:---|--:|:---|:---|
| `ad4080_dsp/` | 55 | AD4080 DSP 扩展（SINC/FIFO/SPI） | 被 CI 的 5 个 TB 引用 |
| `extensions/` | 12 | CAAZ 扩展 | 被 `tools/run_open_rtl.py` 引用 |
| `experiments/` | 3 | 实验配置 | 被 `tools/run_p4_ablation.py` import |
| `archive/` | 4 | 180nm 历史实现 | 存档，无代码引用 |

> 这 4 个目录看着"多余"，但都有 CI 或代码依赖，删了会断构建。
> 想彻底移出需要单独排期改 67 处硬编码路径。

### D. 仓库常规文件（不用看，20+ 项）

`LICENSE` `NOTICE` `CITATION.cff` `SECURITY.md` `CODE_OF_CONDUCT.md`
`CONTRIBUTING.md` `MANIFEST.in` `pyproject.toml` `Makefile` `CHANGELOG.md`
`.gitignore` `.gitattributes` `.github/` `.pre-commit-config.yaml`
`AGENTS.md` `HANDOFF.md` `mechanism_inventory.json` `sources_manifest.json`
`validation_summary.json` `README.md` `README_EN.md`

其中三个值得一提：

| 文件 | 作用 |
|:---|:---|
| `AGENTS.md` | **模块归属与仓库规则**（谁能改什么） |
| `HANDOFF.md` | 追加式交接日志 |
| `CHANGELOG.md` | 版本记录 |

---

## 三、两条链，不要混

| | 新链（本任务） | 旧链 |
|:---|:---|:---|
| 顶层 | `rtl/d3d4_ip.sv` | `rtl/digital_top_w.sv` |
| D3 | `dem_mask_v3`（**3+3+3**） | `dem_shuffle`（4+3+2） |
| D4 | `dither_pre`（`c+U`） | `dither_split`（`c−U`） |
| D5/D6 | **无** | 有（输出 20 位码） |
| 验证 | `tools/verify_d3d4_ip.sh` | `bash run_topw.sh`（8509 笔） |
| 端口 | 开关命令层 | 完整 20 位结果 |

**最容易犯的错**：拿旧链的 8509 笔 PASS 当作新链的证据。不能。

---

## 四、想快速验证"这仓库还活着"

```bash
make help                        # 列出四个目标
make verify-d3d4-ip              # 四层验收（需仿真器）
python3 tools/bridge_charge_check.py   # bridge 等电荷 66,688 组
python3 tools/sched18_stats.py        # D1 随机性 20000 拍
```

不需要仿真器就能跑的是后两个（纯 Python）。
