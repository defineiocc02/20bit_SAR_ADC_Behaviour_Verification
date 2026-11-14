# ADI 20-bit SAR ADC · 片上数字模块

> 复现 ADI ISSCC 2024 §9.8 的**片上数字**部分：调度、Follower、DEM、Dither、权重重构。
> 模拟电路（RA / 参考 / 采样）不在本仓库范围内，只作为测试激励。
>
> **状态声明**：Python 数字链可运行并有实测记录；RTL 已过模块级对拍（VCS 11619 组 0 失配）与综合后 STA，
> 但**时序余量仅 0.16%、单角无 OCV、未布线**，顶层等价性与门级仿真均未做。任何性能数字都还没到可与论文比较的阶段。

## 文件与架构

```
src/adi_model/digital_chain.py       数字主链（D1–D5 一体，可单独跑）
├── D1 调度      dem3d.round_robin_plan        18 池 / 8 采 8 转 2 备
├── D2 Follower  control/follower.py           已决前缀 → 逐事件命令
├── D3 DEM       cross_slice_mux.fig21_*       Fig.19–21 保留列 + 纵向分配
├── D4 Dither    digital_chain._dither_terms   U/L/T 分路
└── D5 重构      digital_chain.reconstruct_int Q30 权重 / Q24 电压 / 96 位累加

rtl/*.sv                              18 个模块，扁平同级
files.f                               编译清单（verilator -f files.f）
```

模块清单：`div_floor` `unit_therm` `adc2_dec` `sadc_enc` `slice_pool_ctrl` `sar_trial_ctrl`
`sar_structural_ctrl` `rdac_drv` `recon_core` `weight_store` `cal_weight_reduce` `cal_residue_mac`
`cal_sample_context` `cal_output_stage` `calib_regs` `status_regs` `analog_phase_ctrl` `sar20_digital_core`

## 接口协议

- 单时钟域 `clk`，`reset_n` 低有效异步复位。
- 事务握手：`valid` 与 `ready` 同拍为 1 才传输；未接收时请求与参数保持稳定。
- `stall` 期间**不越过**当前事务：本项及其后全部押后，恢复后按原序重试。
- 计数守恒：`accepted = retired + cancelled + pending`，`pending == 0` 才算排空完成。
- 响应最早在请求被接收的下一周期给出；不支持零延迟响应。

## 错误位

| 位 | 含义 |
|:--:|:---|
| 0 | 忙时新命令 / 非法组合 |
| 1 | 未使能或权重无效时请求转换 |
| 2 | ADC2 码越界 |
| 3 | dither 字越界（>63） |
| 4 | `k` 越界 `[0,511]` |
| 5 | 重构分母 `Σw·a == 0` |
| 6 | 累加器超出 96 位约定 |
| 7 | 输出饱和 |

## 当前状态（2026-11-14，基线 89dde1a）

### 已验证（有原始日志/报告）

| 层 | 内容 | 证据 |
|:---|:---|:---|
| Python 算法 | DEM 位权 8192 组 0 违反；dither 384 组收敛；权重寻址双向精确 | `tests/unit/` |
| **DEM 相位排程** | `k=4` 时 8 片各覆盖 **16/16** 列（原 8/16），周期 **17280** 笔 | `digital_chain.dem_phases()`；实测脚本 |
| **非法码拒收** | `k∉[0,511]` → `valid=False` / `ERR_K_RANGE` / `code20=None` | `tests/unit/test_digital_chain_addressing.py` |
| **事务 stall/reset** | 四场景（正常/stall/reset/两者）`accepted == retired + cancelled + pending`、`pending == 0` | `tests/unit/test_run_sequence.py` |
| **D5 权重校正** | 真系数恢复输入 **1.3 µV**；名义系数留 **3284 µV**（相容观测） | `tests/unit/test_weight_calibration_closes.py` |
| **RTL 功能（VCS）** | 七组共 **11619 组向量、0 失配** | `docs/VCS_RESULT.md` |
| **RTL 权重路径** | `recon_d5_w` **400/400**（新相位向量重跑，VCS W-2024.09） | `docs/VCS_RESULT.md`；`e665dff` |
| 综合（SMIC180 slow） | 1485 cells / 50175 µm² | `synth/results/` |
| **权重路径时序** | `recon_d5_w2`（倒数乘替代除法）：S1 **24.46 ns slack +0.002 MET**，全设计 setup MET / hold +0.42，面积 **166,799 µm² / 4,940 cells**；`DW_div` 计数为 0 | `synth/results/w2/`、`docs/TIMING_W2.md` |
| STA（名义路径） | setup +0.04 ns / hold +0.58 ns @ 25 ns，**但仅 reg→out**，输入路径未验证 | `synth/results/setup_p_pipe_constrained.rpt` |

### 不能声称

| 项 | 原因 |
|:---|:---|
| 权重路径"余量 7 ns" | +0.002 ns 是 compile 刚满足约束那版；用紧周期回推 S1 能力约 17.3 ns（含 2 ns 输入延时），但**这是结构回推，不是签核** |
| 与精确除法逐位一致 | 倒数乘使 D_eff 最大差 2 个 Q24 单位（0.02 LSB20），400 组里 1 组输出差 1 LSB20；偏差随 G 增大（G=5.5e11 时 13 单位） |
| **40 MS/s 已达成** | setup 余量仅 **0.16%**，且只到综合后、单角、无 OCV；布线未做 |
| RTL 与网表等价 | Formality 跑 19 分钟未收敛，已停；未做门级仿真 |
| DEM 等价于 ADI 原序列 | 覆盖问题已修，但现排程是工程候选（每 9 笔推进一维），非专利两级 shuffler |
| RTL 顶层事务语义 | 计数守恒只在 Python 模型上验过；RTL 顶层仍停在 elaboration |
| 硅上行为 | 模拟侧不在本仓库范围 |

### 已知缺陷（按优先级）

> 本轮已修三条：DEM 相位锁定、非法码拒收、事务 stall/reset。

1. **时序余量只有 +0.04 ns（0.16%）** — 单角、无 OCV、未布线，不能据此声称 40 MS/s
2. **Formality 未收敛** — 19 分钟停止；需先对单模块试跑，确认耗时量级，再做门级仿真

## 怎么跑

```bash
PYTHONPATH=src python3 tools/trace_one_sample.py                     # 一笔样本
PYTHONPATH=src python3 -m pytest tests/unit/test_follower.py -q      # D2
PYTHONPATH=src python3 -m pytest tests/unit/test_reserved_column.py -q  # D3
PYTHONPATH=src python3 -m pytest tests -q                            # 全量
verilator --lint-only -f files.f --top-module sar20_digital_core     # RTL 静态检查（需 verilator）
```

## 文档

| 文件 | 内容 |
|:---|:---|
| [`docs/DIGITAL_SPEC.md`](docs/DIGITAL_SPEC.md) | **唯一权威规格**：接口、数学、定点格式 |
| [`docs/DIGITAL_PLAN.md`](docs/DIGITAL_PLAN.md) | D1–D6 执行顺序与进度 |
| [`docs/ACCEPTANCE_CHECKLIST.md`](docs/ACCEPTANCE_CHECKLIST.md) | 验收项与实际通过情况 |
| [`docs/MECHANISM_UNDERSTANDING.md`](docs/MECHANISM_UNDERSTANDING.md) | 各机制原理与原文依据 |
| [`docs/HANDOVER.md`](docs/HANDOVER.md) | 接手说明 |
| `docs/spec/` | 论文/专利原件索引与原图 |

历史报告全部在 `docs/archive/`，**不作为当前验收依据**。
