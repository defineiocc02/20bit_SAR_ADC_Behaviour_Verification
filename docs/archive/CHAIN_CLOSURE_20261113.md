# 链闭合表（2026-11-13）

> 判据只有三条：**同一笔样本能否贯穿**、**默认路径是否唯一**、**证据是否越级**。
> 每行必须指到具体文件/函数/字段。不写"已实现"。

---

## 1. 链闭合表

| 层 | 用的是哪套拓扑 | 物理单元编号怎么定 | 哪一笔样本 | 哪一组开关 | 与上一层是否一致 |
|:---|:---|:---|:---|:---|:---|
| **1 原图** | 17 主 + 5 子（`FIG.11/19/20`）；`cn=31+β·15=31.9375C` | 主段 `m=c>>4`、`thermal=min(m//2,15)` 个 2C 列 + `B4`；S4=哑位 | 不涉及 | 图上限定的列/开关集合 | — （链头） |
| **2 电荷方程** | 同上 | `topology/capacitors.py::cap_pool` 生成 18×22 权重；`unpack(mask,n)` 把掩码换成单元数 | — | `feedback_charge()` 按掩码求和 | ✅ 与层1 同几何 |
| **3 数字控制** | 同上 | `control/dem3d.py::fig11_masks`（每码逐位不变量测试 512 码覆盖）；`control/dither.py::FIG19_STEP_Q` | 由 `sample_id` 驱动 | 输出 `main_masks[17]`/`sub_masks[5]` 布尔数组 | ✅ 与层2 同编号 |
| **4 行为模型** | 同上 | `simulation/pipeline_engine.py::execute_pipeline`（671 行） | `sample_id=2`，物理片号 `[3,7,2,4,1,5,0,17]` | `raw.main_masks[2,0]`、`raw.sub_masks[2,0]` | ✅ 与层3 同mask |
| **5 定点重构** | **17/5，14-bit ADC2** | `contracts/fixed_point.py`；`weights_q.shape=(18,22)`；`spec.dac_n_main=17` | 同一 `sample_id`；`coarse=281`、`k=281`、`adc2_code=2040` | 同一 mask 展平成 popcount | ✅ 与层4 同码同mask |
| **6 RTL** | ❌ **63 主 + 8 子，12-bit ADC2** | `verification/contracts/rtl_params.vh`：`N_UNIT_MAIN=32'd63`、`ADC2_BITS=32'd12` | ❌ **向量独立生成**，`vectors/generate.py` 不 import `adi_model` | 独立生成的 `unit_therm` 向量 | ❌ **断：几何、位宽、样本身份三处都不同** |
| **7 验证** | 验的是层6（63/8） | `verification/reference/`（从合同正文推导，非抄 SV） | ❌ 与层1–5 无样本关联 | `vectors/generated/<module>/` | ⚠️ 与层6 自洽，但**验的不是层5 那套电路** |

---

## 2. 判据一：同一笔样本能否贯穿

**层 2→5：可以。** 实测一笔（`n_samples=4`, 输入恒 0.3 V）的 index=2：

```
sample_id            : 2
物理片号 (conv)      : [3, 7, 2, 4, 1, 5, 0, 17]
held_sample_ids[2]   : [2,2,2,2,2,2,2,2]      ← 8 片都持有同一笔
coarse / k           : 281 / 281
main_masks[2,0]      : [0,0,0,1,1,1,1,1,1,1,1,0,0,0,0,1,0]   (17 位)
sub_masks[2,0]       : [1,0,0,1,0]                            (5 位)
adc2_code            : 2040
fine                 : 0.018544
最终 code            : 576715
```

身份字段齐全且互相自洽（`coarse==k`、mask 是 17/5、`adc2_code<2^14`）。

**层 5→6：不能。** 三条独立证据：

1. `verification/vectors/generate.py` **完全没有** `import adi_model`
   → RTL 激励是独立定向+随机生成的，不是行为模型产出的那笔样本；
2. `TransactionTrace` 只有 5 个字段
   （`epoch, sample_id, event_index, coarse_code, rdac_code`）
   → **不含**物理片号、DEM 状态、实际 mask、dither 字、系数版本；
   即使想做"同一笔样本对拍"，现有 trace 也带不过去；
3. RTL 契约的几何是 63/8、12 bit，与层 5 的 17/5、14 bit 不同
   → 同一笔样本在两侧连**字宽**都对不上；
4. 另一种 RTL 仿真用的是**缩小几何**（`n_main=2, n_sub=1, control_word_bits=2`），
   它保留了 `n_slices=18`/`n_active=8`/`code_max=511` 这些**片级**结构，
   但每片只有 2 个主单元 ⇒ **物理单元编号与位分解都不可能对应**。

**结论：判据一不通过（层 5→6 断）。**

---

## 3. 判据二：默认路径是否唯一

逐入口实测（同一台机器、同一次运行）：

| 入口 | n_main | n_sub | ADC2 bits | 来源 |
|:---|:---:|:---:|:---:|:---|
| Python 参考 `default_model` | 17 | 5 | 14 | `defaults.py::DefaultParameters` |
| 定点重构 `nominal_decoder` | 17 | 5 | 14 | `weights_q.shape=(18,22)` |
| CLI `adi_model simulate` | 17 | 5 | 14 | 与 `default_model` 同源 |
| 性能实验 `run_p4_ablation` | 17 | 5 | 14 | 用 `default_model` 构造 |
| **RTL 契约 `rtl_params.vh`** | **63** | **8** | **12** | 硬编码 `localparam` |
| **RTL binding（build）** | **2** | **1** | — | `control_word_bits=2` 的**缩小几何**；保留 `n_slices=18`/`n_active=8`/`code_max=511`，但每片单元数远少于 17/5 |
| `verification/vectors/generated/*/meta.json` | **63** | **8** | **12** | 冻结的 `profile` 字段 |

**结论：判据二不通过。6 个主入口里 2 个（RTL 契约、RTL 向量 profile）用旧拓扑；
另有 1 个（binding）用组件级缩小几何，根本不是整阵列。**

这正是用户硬性要求 3 说的"63+8 已被否决、要求统一到 17/5"——
**Python 侧统一了，RTL 侧没有。**

---

## 4. 判据三：证据是否越级

各层实际证明的东西：

| 证据 | 真正证明什么 | 不能用来证明什么 |
|:---|:---|:---|
| L1 行为浮点（DR 93.1 dB / NSD 9.59 nV/√Hz、INL 3.04 LSB20 端到端测试） | 模型与论文同量级 | 位真、RTL 正确、硅测 |
| L2 定点解码（`FixedPointReconstructor`） | 20 bit 码能还原成电压 | 模拟电路正确 |
| V1 模块位真（7 模块、4931 向量） | 7 个**独立模块**的整数算术在 SV 与定点参考间一致 | 模块串起来对、顶层事务对 |
| V2 逐拍状态（`slice_pool_ctrl` 100 拍 / `rdac_drv` 55 拍） | 这两个模块的时序状态一致 | 整链调度对 |
| V3 顶层 / V4 闭环 | **NOT_RUN** | 任何结论 |
| 后仿 / PEX / 硅测 | **无** | 任何结论 |

**发现一处证据自相矛盾：**

```
verification/reports/module_matrix.md   started 2026-11-12T22:06:13  overall: PASS  (7/7)
verification/reports/results_module.json started 2026-11-12T23:11:10  status: NOT_RUN
                                        reason: no simulator on vm-meow: Connection timed out
```

后一次运行**失败**并把 JSON 覆盖成 `NOT_RUN`，但 `module_matrix.md` 仍是前一次
`PASS` 的产物；`VERIFICATION_REPORT.md` 声称"数据来源：results_module.json"，
而该 JSON 现在**不含**那 4931 个数字（`modules` 为空字典）。

⇒ V1 的 7/7 是**归档证据**（那一晚确实跑过），**当前不可复现**（本机无 VCS）。
`verification/README.md` 写明 V3/V4 NOT_RUN 是诚实的；
但 `module_matrix.md` 与 `results_module.json` 并存会让人误读成"现在也是 PASS"。

**结论：判据三部分不通过**——不是 README 越级（它写清了 L1/L2 边界），
是 `verification/reports/` 内部两个文件互相矛盾。

---

## 5. 断点清单

| # | 哪一层 | 缺什么 | 最小修复 | 判据 |
|:--:|:---|:---|:---|:---|
| **B1** | 层5→6 | RTL 契约与向量 profile 还是 63/8、12 bit | 把 `rtl_params.vh`、`digital_interfaces.yaml`、`vectors/generated/*/meta.json` 的 profile 一并改成 17/5、14 bit；重生成向量与 RTL 参数包 | 逐字段 diff：`N_UNIT_MAIN=17`、`N_UNIT_SUB=5`、`ADC2_BITS=14`；`unit_therm` 的 `main_logical` 位宽变 17 |
| **B2** | 层5→6 | 没有"同一笔样本"的载体 | 给 `TransactionTrace` 补 `slice_ids`、`main_masks`、`sub_masks`、`dither_word`、`coeff_version`；再用行为模型的 trace 直接喂 RTL 的 `$readmemh` | 一笔 `sample_id=2` 的 mask/k 同时出现在 `raw.*` 与 `vectors/<module>/<field>.mem` 里，逐位相同 |
| **B3** | 层6→7 | 顶层事务未验（V3/V4 NOT_RUN） | 有 VCS 环境后跑 `python -m verification.run_verification --level top`；或先做无仿真器的**结构等价检查**（端口/位宽/连接逐条比对） | `results_rtl_top.json` 的 status 为 PASS 且含逐拍比较 |
| **B4** | 层7 | `module_matrix.md` 与 `results_module.json` 矛盾 | 重跑成功后同时刷新两者；或给 `.md` 加 `source_sha`/时间戳横幅，并在 JSON 非 PASS 时显式标注"本表来自归档运行" | `module_matrix.md` 里出现生成它的 `results_module.json` 的时间戳与 status；两者不一致时 CI 失败 |
| **B5** | 层7 | 本机无 VCS，V1 不可复现 | 记录可复现环境（镜像/容器），或把 V1 降级标注为"归档证据，需外部环境复跑" | README 的验证章节明确写"V1 = 归档（2026-11-12），本机不可复跑" |

**B1 是唯一的"拓扑不一致"断点**，也最严重——它直接违反用户硬性要求 3。
B2 是"身份带不过去"，B3/B4/B5 是证据链问题。

---

## 6. 整条链闭合了多少

按**关节**算（7 层 = 6 个关节）：

| 关节 | 状态 |
|:---|:---|
| 原图 → 电荷方程 | ✅ 闭合 |
| 电荷方程 → 数字控制 | ✅ 闭合 |
| 数字控制 → 行为模型 | ✅ 闭合 |
| 行为模型 → 定点重构 | ✅ 闭合 |
| **定点重构 → RTL** | ❌ **断（B1/B2）** |
| RTL → 验证 | ⚠️ 半闭合（模块级有归档证据，顶层/闭环 NOT_RUN；B3/B4/B5） |

**4/6 关节闭合 = 约 67%。**

但按**"同一套电路"这个更强标准**算：
RTL 与验证这两层验的是**另一套电路**（63/8），对主链（17/5）**零覆盖**。

若按"整条链指向同一个芯片"算，**实质闭合约 4/7 ≈ 57%**。

两个数字都不该说成"基本完成"。

---

## 7. 该先动手的地方

**先 B1。** 理由：
* 它同时是"拓扑不一致"和"违反用户明确要求"；
* 它挡住 B2（几何不对，谈什么同一笔样本）；
* 它不依赖任何外部环境（不需要 VCS），本机就能做完；
* 做完后 B2 才有意义。

**再 B4/B5。** 成本最低（改文档/加横幅），但直接决定"读者会不会把归档当现状"。

**B3 最后。** 依赖 VCS，本机做不了；可以先用结构等价检查（端口/位宽/连接）拿一部分证据。
