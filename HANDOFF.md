# 交接文档

**写于 2026-11-17 20:25　分支 `merge/adi-repro`　我的最后 commit `674b0d8`**

> 本文档给接手的 AI 或人。**先读完第 0 节再动手。**

---

## 0. 最重要的一件事：仓库现在有并行修改

**我发现工作区有未提交改动，不是我做的**（是 Codex 或其他人）。
**数量在我写本文档时是 27，现在实测已变为 30 —— 说明并行修改仍在进行。**

| 文件 | 改动量 | 说明 |
|:---|:---|:---|
| `flow/verification/manifests/mechanism_inventory.json` | **+36 / −679** | 大幅精简 |
| `.github/workflows/ci.yml` | +79 / −36 | CI 重构 |
| `src/adi_model/defaults.py` | +9 / −18 | **重写了我写的注释**，数值仍是 13 位 / 0.75 V |
| 其余 24 个 | 小改 | 文档与脚本 |

**我做过的事：**
- ❌ **没有提交这些改动**
- ❌ **没有回滚这些改动**
- ✅ 恢复了 17 个**被误删但未提交**的文件（`flow/sim/fixtures/fig19_ideal_ports/` 等）

**接手时必须先做：**

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
git status                      # 看清当前状态
git stash list                  # 我不确定有没有遗留 stash
```

**然后问仓主：这些并行改动是继续还是回滚。不要自己决定。**

---

## 1. 这个仓库是什么

**ISSCC 2024 Session 9.8 论文的数字 IP 复现**：
20 bit / 40 MS/s / ±3 V 差分，SAR ADC 的两步式架构。

| 项 | 值 |
|:---|:---|
| 论文 | `references/ISSCC2024_PAPER.pdf`（3 页）、`_SLIDES.pdf`（44 页）|
| 工艺 | TSMC 28HPC+ `tcbn28hpcplusbwp7t40p140ssg0p81vm40c` |
| 时钟 | 25 ns（40 MS/s）|
| 主要语言 | SystemVerilog（RTL/tb）、Python（行为模型/向量/校验）、TCL（综合/后端）|
| EDA 主机 | `vm-yian`（ssh）|

---

## 2. 我这轮（49 个 commit）到底做了什么

### ✅ 真正有价值的：打通了 RTL-to-GDS

**起点**：`92ae967`（2026-11-16），当时后端完全走不通。

**踩过并解决的坑（每个都有错误码）：**

| 问题 | 错误码 | 解决 |
|:---|:---|:---|
| 没有技术 LEF | `NEX-001` × 9 层 | 用 `.tf`（46 层）生成，补 `DIRECTION` + `AP` |
| `read_db` 不适用 FC | `CMD-005` | `create_lib -ref_libs <NDM>` |
| TLU+ 绑定失败 | `SCN-1001` | `read_parasitic_tech` + `set_parasitics_parameters` |
| site row 为 0 | `LGL-397` × 49 | `create_site_row`，朝向必须 `R0`（`N` 报 `CMD-031`）|
| site row 重叠 | `LGL-010` | 先 `remove_site_rows` 再建 |
| 电源上下文为空 | `used supplies = 0` | `load_upf` + `set_voltage`（必须在 `set_top_module` 之后）|
| 时钟门控致 Formality FAILED | `clock_gate_*/*lat.00*` | `set_clock_gating_options -minimum_bitwidth 1000000` |

**脚本在**：
```
flow/synth/v21/fc_rtl_to_gds_derived.tcl   ← 完整流程（含自生成 tech LEF）
flow/synth/v21/tsmc28_tech_derived.lef      ← 自生成技术 LEF
flow/build/filelists/files_adc_v21.f        ← v21 生产清单
```

### ✅ 13 个模块全部 DC 综合

数据在 `docs/audit/SYNTH_PER_MODULE.md`。全部满足 25 ns。

### ⚠️ 7 个模块"完整通过"——但只剩 1 个有实物

**这是我这轮最大的失误。**

| 模块 | Formality | 网表/GDS 现状 |
|:---|:---|:---|
| `sched18` | SUCCEEDED 234/0/0 | ✅ `docs/audit/artifacts/sched18_final.v` + `.svf` |
| `adc2_capture` | SUCCEEDED 490/0/0 | ❌ **我清 `/tmp` 时删了** |
| `acq_conv_ctx` | SUCCEEDED 1037/0/0 | ❌ **已删** |
| `sar_evt_v21` | SUCCEEDED 133/0/0 | ❌ **已删** |
| `dem_phase_rr` | SUCCEEDED 97/0/0 | ❌ **已删** |
| `follower_rt` | SUCCEEDED 72/0/0 | ❌ **已删** |
| `dem_phase_gen` | SUCCEEDED 126/0/0 | ❌ **已删** |

**这 6 个的"通过"现在只有文档里的数字，无法重新验证。**
**接手后第一件事建议：重跑这 6 个并归档。**

### ✅ 审计

| 文档 | 内容 |
|:---|:---|
| `docs/audit/AUDIT_ALGO_VS_SOURCE.md` | 12 项算法判定，带证据等级 `[A]`/`[P]`/`[E]` 与页码 |
| `docs/audit/PORT_AND_ONCHIP_AUDIT.md` | 87 个顶层端口逐项对照论文要求 |

**关键裁定（仓主已定）：ADC2 = 13 bit / 0.75 V**，窗口 `[-0.1875, +0.5625)`。

---

## 3. 当前真实状态（不要相信更好的说法）

| 维度 | 状态 | 证据 |
|:---|:---|:---|
| RTL-to-GDS 流程 | ✅ 可复现 | 脚本 + `TOOL_WORKDIR_RULES.md` |
| DC 综合 13/13 | ✅ | `docs/audit/SYNTH_PER_MODULE.md` |
| 后端完整通过 | ⚠️ **7 个报过，只有 `sched18` 有实物** | 见 §2 |
| router DRC | ✅ 7 个模块为 0 | |
| **foundry DRC / LVS / PG / 多角 / IR-EM** | ❌ **全未做** | DEF 里 VDD/VSS 无 `ROUTED` 几何 |
| 顶层综合 | ❌ **从未成功** | `adc_digital_ip_v21` |
| 顶层实例化 | ⚠️ **8 个模块，缺 3 个** | `dem_phase_rr`/`dem_phase_gen`/`follower_rt` 未接入 |
| `recon_v21.sv:373` 缺陷 | ❌ **未修** | 见 `docs/audit/todo/README.md` §2 |

**不是 tapeout signoff。**

---

## 4. 必读的四个文件（按顺序）

| 顺序 | 文件 | 为什么 |
|:--:|:---|:---|
| 1 | **`README.md`** | 唯一入口，顶部有强制规范警告 |
| 2 | **`docs/STATUS.md`** | 唯一权威状态表（含"不能声称什么"与"作废数字清单"）|
| 3 | **`rtl/README.md`** | 顶层关系、8 个实例化、3 个未接入 |
| 4 | **`TOOL_WORKDIR_RULES.md`** | **远端 EDA 强制规范，违反会被仓主批评** |

---

## 5. 绝对不要做的事

| 禁止 | 原因 |
|:---|:---|
| **以 `$HOME` 作 CWD 跑 EDA** | 已污染过三次：`.ahdlSimDB` × 67、`d3d4*` × 21、`.svf` × 44 |
| 引用 `docs/_history/` 任何文档 | 107 份已作废 |
| 声称 tapeout signoff / foundry DRC clean / LVS clean | 全未做 |
| 声称整核集成完成 | 顶层从未综合成功 |
| 声称「D5 算法不可实现」 | 实测只支持「冻结输入与预算下未完成」|
| 引用「D5 占面积 95.8%」 | 已撤回：三层嵌套重复计数 |
| 按通配符批量删文件 | 74 个 `.svf` 里只有 44 个是我的 |
| 只报问题不给证据 | 仓主会追问 |

---

## 6. 我的错误清单（供你避坑）

| # | 错误 | 教训 |
|:--:|:---|:---|
| 1 | **清理 `/tmp` 时删掉了 6 个模块的网表** | 清理前必须确认已归档 |
| 2 | `place_opt` 失败归因**连错四次** | 改了变量数字没变 = 归因错，立刻换判据 |
| 3 | 把"我没跑完"写成"技术不可行" | 预算到期 ≠ 技术不可行 |
| 4 | 文档乱了就**新建文档**去"说明"它 | 应合并进已有入口，不是新增 |
| 5 | 说"四个顶层各自合理" | 实际是顶层漏接 3 个模块，我该改接线而不是写解释 |
| 6 | 未先 `cd` 就 ssh 跑 EDA | CWD = `$HOME` |
| 7 | 未验证文件存在就改脚本路径 | `ls` 五秒可发现，浪费了四轮 |

---

## 7. 接手后的建议顺序

| 优先 | 事项 | 理由 |
|:--:|:---|:---|
| **0** | **先问仓主：~30 个并行改动怎么处理** | 不能自己决定 |
| 1 | 重跑那 6 个模块，归档 `.v`/`.def`/`.gds` 进 `docs/audit/artifacts/` | 现在只有 `sched18` 有实物 |
| 2 | 修 `recon_v21.sv:373` 的 `err3_q` | 有明确判据，见 `docs/audit/todo/README.md` §2 |
| 3 | 顶层实例化 3 个缺失模块 + 顶层综合 | 不接通谈不上整核 |
| 4 | `place_opt` 门禁 + 降低利用率重试 4 个未完成模块 | 见 `docs/audit/todo/README.md` §3 |
| 5 | PG 布线 / foundry DRC / LVS | 需真实 DRC runset |

---

## 8. 复现命令

```bash
# RTL 回归（9 项）
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh

# 单模块 Formality
fm_shell -f /tmp/fm_run3.tcl      # 脚本在会话记录里，需按模块改路径

# 远端污染自查
bash tools/check_home_pollution.sh vm-yian

# 文档计数核对
python3 tools/check_doc_counts.py
```

---

## 9. 联系方式 / 决策人

**所有需要裁定的问题找仓主（zhaoyi）**，不要自己决定。
特别是：
- ADC2 位宽（已裁定 13 bit，不要再改）
- `[E]` 假设是否登记为合同条款
- 并行改动如何取舍
