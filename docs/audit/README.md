# 审计与后端报告（权威版）

时间：2026-11-17　基线：`39a9bc9`

## 现行文档（5 份）

| 文件 | 内容 | 结论等级 |
|:---|:---|:---|
| **`AUDIT_ALGO_VS_SOURCE.md`** | 数字算法 vs 论文/专利披露逐项核对 | 12 项判定：符合 6、`[E]` 假设 5、待验证 2 |
| **`SYNTH_PER_MODULE.md`** | 13 个模块的 DC 单模块综合 | 160,951 µm²，全部满足 25 ns |
| **`FORMAL_SUCCEEDED.md`** | **`sched18` 的 Formality 通过** | **`SUCCEEDED` 234/0/0** |
| **`WSUM_PHYSICAL_COST.md`** | `wsum_v21` 物理实现代价分析 | 三工具交叉验证：不可接受 |
| `RECON_ALPHA_DIVIDER.md` | `recon` 的 alpha 除法器瓶颈 | 实验确认（`DW_div` 13→6、10min→99s）|

**外加 `artifacts/`**：`sched18` 的布线后网表与 SVF。

## 已被取代的文档（`_superseded/`）

**不得引用。** 这些记录了探索过程中的中间判断，其中多条已被后续证据推翻：

| 文件 | 当时的判断 | 后来被什么推翻 |
|:---|:---|:---|
| `FC_LIBRARY_NO_PG.md` | “库 839 cell 全无 PG 引脚” | `get_lib_pins */*/VDD` 返回 **839 个** |
| `PG_PIN_RETRACTION.md` | 撤回上一条 | 本身是正确记录，但已被 `FORMAL_SUCCEEDED.md` 取代 |
| `LGL397_ROOT_CAUSE_FINAL.md` | “PDK 缺技术 LEF，无法继续” | 查明 `.tf` 提供层、`site_via.lef` 提供 site，流程可通 |
| `FC_PLACE_OPT_BLOCKER.md` | “`place_opt` 连续 8 轮未突破，BLOCKED” | 后续通过补 `.tf`+tech LEF+site row 打通 |
| `FC_TLU_BLOCKER.md` | “TLU+ 绑定未解决” | `set_parasitics_parameters -early_spec/-late_spec` 解决 |
| `BACKEND_FINAL_STATUS.md` | “PARTIAL，卡在 site row” | 已完整跑通 |
| `FC_FLOW_BREAKTHROUGH.md` | “综合与 floorplan 首次通过，布线为 0” | 后续布线 14,899 条 |
| `FC_FLOW_STATUS_V2.md` | “CTS/布线首次返回 0，168 单元未放置” | `CHK_UNPLACED = 0` |
| `FC_RTL_TO_GDS_DONE.md` | “完整跑通，42 个单元未放置” | 实为 7 个编号重复计数，最终 0 |
| `UNPLACED_CELLS_ANALYSIS.md` | “7 个编号被重复报告” | 判断正确，但已被 `FORMAL_SUCCEEDED.md` 取代 |
| `FORMAL_RESULT_ANALYSIS.md` | “FAILED，20 个 failing 由时钟门控导致” | 判断正确，禁用门控后 `SUCCEEDED` |
| `FORMAL_CLOCK_GATE_STATUS.md` | “时钟门控问题现状” | 同上 |

## 后端结论（一句话）

**`sched18` 完整走通 RTL-to-GDS 并通过 Formality 等价性验证（234/0/0）；
其余 12 个模块只有 DC 综合数据；`wsum_v21` 因门级规模过大，物理实现代价不可接受。**

## 三条硬性限定

1. **技术 LEF 是推导生成的**（`flow/synth/v21/tsmc28_tech_derived.lef`），非 PDK 官方交付
2. **`sched18` 的结果无时钟门控**（带门控版 Formality FAILED）
3. **无 LVS、无 GDS 验证、单角、无 IR/EM** → 不是 tapeout signoff
