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

## 当前未接入 / 未验证

- **RTL 未仿真**：无 testbench、无波形、无对拍记录；只做过静态编译与参数展开。
- **RTL 基于旧 63/8 几何**：与 Python 的 17/5 不一致，不能作新规格证据。
- **P4 主链的 DEM 开关是死的**：`dem_enable` 在 `defaults.py` 直接 `raise`。
- **旧模拟主链的 DEM 对输出无影响**：掩码逐笔在变，INL 开/关完全相同（4.731）。
- **专利未披露项**：LFSR、随机序列、流水级数均为工程选择，不声称与硅片一致。

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
