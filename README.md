# ADI 20-bit SAR ADC · 片上数字模块

> 复现 ADI ISSCC 2024 §9.8 的**片上数字**部分：调度、Follower、DEM、Dither、权重重构。
> 模拟电路（RA / 参考 / 采样）不在本仓库范围内，只作为测试激励。
>
> **状态声明**：Python 数字链可运行并有实测记录；RTL 为静态可编译，**未做仿真、未做综合、未做时序收敛**。
> 任何性能数字都还没到可与论文比较的阶段。

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

## 当前状态（2026-11-13，基线 8920e89）

### 已验证（有原始日志/报告）

| 层 | 内容 | 证据 |
|:---|:---|:---|
| Python 算法 | DEM 位权 8192 组 0 违反；dither 384 组收敛；权重寻址双向精确 | `tests/unit/` |
| **D5 权重校正** | 真系数恢复输入 **1.3 µV**；名义系数留 **3284 µV**（相容观测） | `tests/unit/test_weight_calibration_closes.py` |
| **RTL 功能（VCS）** | 七组共 **11619 组向量、0 失配** | `docs/VCS_RESULT.md` |
| **RTL 权重路径** | `recon_d5_w` **400/400**（VCS） | `docs/VCS_RESULT.md` |
| 综合（SMIC180 slow） | 1485 cells / 50175 µm² | `synth/results/` |
| **STA（带 SDC）** | setup **+0.04 ns (MET)** / hold **+0.58 ns (MET)** @ 25 ns | `synth/results/setup_pipe_constrained.rpt` |

### 不能声称

| 项 | 原因 |
|:---|:---|
| **40 MS/s 已达成** | setup 余量仅 **0.16%**，且只到综合后、单角、无 OCV；布线未做 |
| RTL 与网表等价 | Formality 跑 19 分钟未收敛，已停；未做门级仿真 |
| DEM 已完整打乱 | 相位与片选相关，720 笔后重复，部分物理单元选不到 |
| 事务控制正确 | `reset` 计数曾出现 `pending=−1`，`k=−4` 非法码未被拒绝 |
| 硅上行为 | 模拟侧不在本仓库范围 |

### 已知缺陷（按优先级）

1. **DEM 相位锁定** — 需按 `q=n//9; reserved=q%16; v_phase=(q//16)%8; h_phase=(q//128)%15` 重排
2. **非法命令未拒** — `c=0, word=32` 得 `k=−4` 仍继续出码
3. **事务 reset 语义** — 计数为负、stall 后的 reset 不执行
4. **Formality 未收敛** — 需先对单模块试跑，确认耗时量级

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
