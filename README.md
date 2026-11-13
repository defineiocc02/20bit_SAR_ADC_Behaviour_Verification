# 20 位 / 40 MS/s 两步式 SAR ADC：片上数字算法复现

[English](README_EN.md) · [执行计划](docs/DIGITAL_PLAN.md) · [验收表](docs/ACCEPTANCE_CHECKLIST.md) · [论文与专利原件索引](docs/spec/授予专利_可检索正文入口与关键事实索引.md) · [Python–RTL 验证](verification/README.md)

> 五分钟读懂：**这个仓库只做一件事——复现 ADI ISSCC 2024 §9.8 的片上数字控制与数字重构算法。**
> 模拟电路（RA、参考、TRACK/AUX）只作为测试激励，不属于主线。

---

## 1. 在复现什么

**目标**：D1–D6 六个数字模块，判据是「原文依据 → 算法 → Python 真跑 → RTL 真实现 → 逐事件对拍」。

**边界**：不做晶体管级、不做 PEX、不建通用仿真平台。
论文披露的只是架构与指标；未披露的随机序列、LFSR、流水延迟属**工程设计**，不声称与芯片逐位一致。

---

## 2. 数字架构

```
                    Digital Top (D6)
                          │
   ┌──────────┬───────────┼───────────┬──────────────┐
   │          │           │           │              │
D1 Scheduler  D2 Follower  D3 DEM   D4 Dither   D5 Weight Recon
18 片池        已决前缀     编码/     扰动字/      物理片 ID
8 采 8 转 2 备  逐事件命令   shuffler  码修正/去扰   + mask + 系数
   │          │           │           │              │
   └──────────┴───────────┴───────────┴──────────────┘
                          │
                    20-bit 输出
```

---

## 3. 六个模块完成到哪（只记有实测证据的）

| 模块 | Python | RTL | 对拍 | 最大缺口 |
|:---|:---|:---|:---|:---|
| **D1** Scheduler | 有（`dem3d.round_robin_plan`） | 待核对 | **旧几何** | reset/stall 状态机行为未测（现有测试只证纯函数可重复） |
| **D2** Follower | 有（`control/follower.py`，4 项模块级 PASS） | 待核对 | 无 | **未进主链**（已核实 `src/` 零 import） |
| **D3** DEM | 部分（`fig11_masks` 进主链；Fig.21 纵向/列分配模块级 PASS） | 待核对 | **旧几何** | 横向 shuffler 未替换现有逐片旋转；512 码 × 全状态未覆盖 |
| **D4** Dither | 有（v1 进主链，分路 3 项 PASS） | 待核对 | 无 | 64 字未全遍历；`/7`（七片示例）与八片等效性未证明 |
| **D5** Recon | 有（方程已修，反例通过） | `recon_core.sv` | **旧几何**（101 向量为 63/8） | 系数加载/版本切换/reset 未验 |
| **D6** Digital Top | 无 | `sar20_digital_core.sv` 仅 `ELABORATED_ONLY` | **NOT_RUN** | 整链事务与逐事件对拍全缺 |

**读法**：「有」= 有实现且模块级判据通过；**「对拍」列的「旧几何」表示该记录来自 63/8 拓扑，不能作为当前 17/5 规格的证据。**

---

## 4. 怎么跑（三条命令都实际执行过）

```bash
# 一个数字样本（含 SADC 判决、RDAC 事件、ADC2 码、20 位输出）
PYTHONPATH=src python3 tools/trace_one_sample.py

# 单个模块测试
PYTHONPATH=src:. python3 -m pytest tests/unit/test_follower.py -q            # D2
PYTHONPATH=src:. python3 -m pytest tests/unit/test_scheduler_identity.py -q  # D1
PYTHONPATH=src:. python3 -m pytest tests/unit/test_reserved_column.py -q     # D3

# 全量
PYTHONPATH=src:. python3 -m pytest tests -q

# 工程自检（7 项；C4 目前报图陈旧）
python3 tools/check_engineering.py
```

> Python–RTL 对拍入口见 `verification/README.md`；**当前无有效对拍**（向量仍是旧几何），随 RTL 重建后再补。

---

## 5. 源码在哪（按 D1–D6 直接跳）

| 模块 | Python | 测试 | RTL |
|:---|:---|:---|:---|
| D1 | `src/adi_model/control/dem3d.py`（`round_robin_plan`） | `tests/unit/test_scheduler_identity.py` | `rtl/core/slice_pool_ctrl.sv` |
| D2 | `src/adi_model/control/follower.py` | `tests/unit/test_follower.py` | 待核对（`sar_trial_ctrl.sv` / `sar_structural_ctrl.sv`） |
| D3 | `src/adi_model/control/fig11_dem.py`、`dem3d.py`、`cross_slice_mux.py` | `tests/unit/test_reserved_column.py`、`test_dem3d.py` | `rtl/core/rdac_drv.sv`、`unit_therm.sv` |
| D4 | `src/adi_model/control/dither.py`、`dither_port.py` | `tests/unit/test_dither_split_causality.py` | 待核对 |
| D5 | `src/adi_model/calibration/reconstruction.py`、`contracts/fixed_point.py` | `tests/unit/test_reconstruction_gain_pairing.py` | `rtl/core/recon_core.sv` |
| D6 | 无 | 无 | `rtl/top/sar20_digital_core.sv` |

模块说明：`src/adi_model/control/README.md`（D1–D4）、`src/adi_model/calibration/README.md`（D5）、`verification/README.md`（对拍）。

**候选、未接主链**：`reserved_column.py`、`shuffle_gate.py`、`cross_slice_mux.py`、`aux_input.py`、`reference_buffer.py`、`track_aux.py` —— 它们是判据模块，**不等于已复现**。

---

## 6. 还差什么（只列真正卡住验收的）

1. **D2 follower 未进主链** —— 21 KB 实现 + 14 项测试全在测试里跑，对实际转换无影响。
2. **D3 横向 shuffler 未替换逐片旋转** —— Fig.21 的纵向与列分配已实现，主链仍用旋转。
3. **RTL 全部基于旧 63/8 几何** —— 与当前 17/5 规格不一致，任何 RTL 结论都不能作为新规格证据。
4. **D6 整链不存在** —— 无真实事务、无逐事件逐字段对拍。
5. **性能数字不可引用** —— 图停在 `59d17e5`（C4 报 stale）。

逐条判据与执行顺序见 [`docs/DIGITAL_PLAN.md`](docs/DIGITAL_PLAN.md)。
