# PR #9 响应：本轮实际做了什么、没做什么

> 审查基线 `0752889`，本轮起点 `1f55b5b`，终点见 `git log`。
> 只写**已执行并有证据**的事；标 `NOT_RUN` 的不折算成通过。

---

## 1. 哪些错误由生产函数反例证明已修？

| # | 缺陷 | 反例（现在会失败，修前不会） | 证据 |
|:--|:---|:---|:---|
| 1 | **DEM 逐片审计统计的是逻辑 slot**：`command.main_on.sum(axis=0)` 得到 `[active_slot, unit]`，不是 `[physical_slice, unit]` | `test_physical_slice_audit_is_not_slot_aggregation`：构造"每片只用 21/63、各片窗口不同"。旧聚合报 63/63，新统计报 21/63 | `tests/unit/test_dem3d.py` |
| 2 | 未被选中的物理片会被过滤 | `test_slice_ids_reach_every_physical_row_once_per_rotation`：11..17 号片必须是零行且仍在报告里 | 同上 |
| 3 | **迁移漏了 `from adi_model import <模块名>`**，导致整组故障注入测试 ImportError | `test_independent_oracle_rejects_faulted_real_model_wiring[swap_d5_d4]` 修前直接 ImportError | `tests/integration/test_fig19_static_ports.py` |
| 4 | `.mem` 位宽按十六进制位数推断，63 位负数读成大正数 | `div_floor` 在 d≥1 时全错；修后 257/257 | `verification/cosim/scoreboard.py` |

### 一处**撤回**

上一轮把 `gain` 写成"合同 `Σ(W·a)` 与 RTL `G=T` 不一致"。**这是误读**：
合同 §3.2 明写"普通单位的 `a=1`"，`a` 是信号连接标志，`on` 才是转换开关；
普通单位下 `gain = ΣW = total`，与 RTL 完全一致。改正后 `recon_core` 仍
101/101 位真一致——RTL 一直是对的。

已加 `test_gain_uses_signal_connection_not_the_switch_mask` 钉住 `a ≠ on`，
报告与 README 同步标注"已撤回"。

---

## 2. 哪些文件真正拆分、迁移、合并？

上一轮已完成（本轮未再动）：

* `src/adi_model/` → `contracts / topology / control / behavioral / calibration /
  simulation / metrics` 七个职责包，43 个模块搬家；
* 搬家前后行为指纹 12 项 SHA256 完全一致，A0–A5 数字逐项不变；
* `metrics` 由模块变包后加了薄适配，`from adi_model import metrics` 仍可用。

**没有做**：`execute_pipeline`（743 行）仍未拆分；`experiments/` 层未建，
A/C 矩阵仍在 `tools/run_p4_ablation.py`。这两条是下一步。

---

## 3. 哪些 Python–RTL 功能已对拍？

VCS W-2024.09-SP1，主机 `vm-meow`，license `27080@localhost`。

| 模块 | 向量 | 结果 |
|:---|---:|:---|
| `sadc_enc` | 127 | 127/127 位真 |
| `unit_therm` | 96 | 96/96 |
| `adc2_dec` | 4195 | 4195/4195（全 4096 码） |
| `div_floor` | 257 | 257/257，延迟恒 9 拍 |
| `rdac_drv` | 55 | 55/55 |
| `slice_pool_ctrl` | 100 拍 | 100/100，状态逐字段 |
| `recon_core` | 101 | 101/101，含 4 个 flag 与 11 拍延迟 |
| **合计** | **4931** | **0 处不一致** |

**没有对应实现的数字功能**（标 `NOT_IMPLEMENTED`）：DEM 物理 mapper 的 RTL
（源 3D DEM 未实现，RTL 只有数值温度计展开）、dither 片内字生成、
校准系数在线训练。

**V3 顶层**：`ELABORATED_ONLY`。用生成的 `source_control_binding` /
`source_acquisition_binding` 在 VCS 下 elaborate 并跑到复位 idle
（`acquiring_mask=0xff`、无伪 `dout_valid`）。**不是 V3 通过**：配置写入
（71 权重 + 3 标量 + 18 窗口）与逐相位比较器判决都还没驱动。
**V4 闭环**：`NOT_RUN`。

---

## 4. INL/DNL 的定义与覆盖

* **INL**：256 个电平的平均传输误差，端点拟合后取残差，单位 LSB20。
  **不是逐码 INL**，不能直接对论文 ±2.2 ppm 声称"已复现"。
  覆盖 256 电平 × 32 次、σ=1e-3、单 seed；无置信区间。
* **DNL**：斜坡步长粒度（每步 16384 LSB20）的差分指标，
  字段 `dnl_bin_note` 与图题都写明 **not 20b code-density DNL**。
  状态 **PARTIAL**：没有做转换阈值搜索，不能当单码码宽。
* 置信度：单 seed、无重复芯片；机制比较可用，绝对指标不可用。

---

## 5. DEM / dither / 校准实际改善或恶化了什么

同芯片同网格，256 电平 × 32 次：

| 配置 | INL | DNL(分箱) | SNDR | SFDR |
|:---|---:|---:|---:|---:|
| A2 基线 | 24.5 | 16.5 | 88.06 | 99.3 |
| A3 DEM | **6.2** | 9.0 | 86.93 | 100.3 |
| A5 校准 | 10.5 | **18.4 ↑** | 88.92 | **109.7** |
| A6 dither | 20.5 | **5.9** | 87.53 | **97.9 ↓** |
| C1 DEM+校准 | **26.9 ↑↑** | 19.2 | 86.25 | 100.0 |
| C2 DEM+dither | **3.3** | **2.6** | 86.43 | 99.5 |
| C3 全组合 | **20.4 ↑↑** | 4.7 | 85.77 | 98.7 |

**最重要的负面结果**：逐码校准与 DEM **互不兼容**。C1 的 INL（26.9）比单用
DEM（6.2）差 4 倍。原因是逐码表假设"一个粗码对应一套固定 mask"，DEM 打开后
同一码每笔换 mask，修正量对不上。这不是噪声，是原理性不兼容。

**这也解释了为什么必须做逐单元权值校准**：特征要用 `活动 mask × 单元权值`
（即 §2.3 给的形式），而不是"码 → 一个标量"。

---

## 6. 哪些原图已核对？

* **dither（US10505561 Fig.19）**：六段专题已有，含 v1/v2 两条拓扑的差异、
  一次"只在 DAC 节点注电荷不成立"的实测否定（余差 48 mV、RA 削顶）。
  缺：专利原件逐字核对、七片 vs 八片几何差异。
* **DEM（US10511316 Fig.11/19–21）**：专题已有；本轮修了逐片统计口径。
  缺：保留列与跨片分配规则仍是工程替代，未按原图证明。
* 其余四项（follower / 动态参考 / TRACK–AUX / 普通 AZ）**仍只有素材，没有专题**。

---

## 7. CI 每项结果

| Job | 结果 | 说明 |
|:---|:---|:---|
| test（pytest） | **1785 passed / 1 failed / 47 errors** | 1 failed + 47 errors **全部**是 `FAIL: simulator not found; configure VERILATOR`，不是断言失败 |
| lint（ruff 0.6.9） | **71 errors** | 基线实测 **95 errors**；本轮新增 0，修复 24。剩余全部为既有 D103/D105/D417/E402 |
| type（mypy） | **8 errors / 4 文件** | 基线实测同样 **8 errors / 4 文件**，本轮未增加 |
| rtl（verilator） | **NOT_RUN（依赖缺失）** | 本机无 Verilator；`tools/run_fig19_rtl_calibration.py` 里写死 verilator 命令，换 VCS 会与 CI 分叉。`vm-meow` 已装 Verilator 4.028，但其 Python 3.6 无 numpy，跑不了 pytest 编排 |

三类分开：**功能断言失败 0 项**；**依赖缺失 48 项**；**既有 lint/type 债务 79 项**（71+8）。

---

## 8. 当前版本仍然不能声称什么

1. 不能声称"整链无丢样/重样/乱序"——V3 只到 elaborate + idle。
2. 不能声称"模拟–数字闭环一致"——V4 NOT_RUN。
3. 不能声称"逐码/逐单元校准已在 RTL 落地"——RTL 侧的校准加载一致性未对拍。
4. 不能声称"20 位 DNL"——DNL 是斜坡步长分箱，PARTIAL。
5. 不能声称"论文指标复现"——INL 最好 3.3 LSB20，论文 ±2.2 ppm 口径不同且更严。
6. 不能声称"DEM 按专利原图实现"——保留列与跨片分配仍是工程替代。
7. 不能声称"CI 全绿"——lint/type 有既有债务，rtl job 依赖缺失。

---

## 复现

```bash
# 合同与向量
python -m verification.vectors.generate
python -m verification.run_verification --level contracts

# V1 模块对拍（需 VCS 主机）
python -m verification.run_verification --level module
# V3 顶层 bring-up（ELABORATED_ONLY）
python -m verification.run_verification --level rtl_top

# 性能矩阵（A0–A7 + C1–C3）
PYTHONPATH=src python3 tools/run_p4_ablation.py --static-points 256 \
  --static-repeats 32 --dynamic-n 8192 --out docs/reviews/20261113_p3p4/data
```
