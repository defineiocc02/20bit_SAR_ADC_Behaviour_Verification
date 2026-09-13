# HANDOFF — 阶段检查点与待办

> **用途**（任务书 §11）：跨执行轮次的交接。新轮次先读本文件与
> `mechanism_inventory.json`，**不得重新做状态总结后停止推进**。
> 每完成一个里程碑更新本文件（追加，不覆盖历史条目）。

---

## 当前检查点（2026-09-12，阶段 A 里程碑 1）

- **commit**：阶段 A 里程碑 1 检查点提交（父提交 = v7.0.10 `ffcc011`，本提交只新增阶段 A 文件，未改既有物理代码）。
- **基线**：231 passed + 4 xfailed（`pytest -m "not slow"`，v7.0.10 CI 绿）；
  参考输出指纹 `a1ccd92f…35ac70`（历史基线，非主线目标）。
- **已完成**：
  - [x] 固定版本与原文证据：`sources_manifest.json`（8 份核心原文 SHA256 + 页数 + 定位规则，pypdf 页数实测）。
  - [x] 机制总清单：`mechanism_inventory.json`（M01–M16 + X01，三轴线 × 四类配置 × 实验状态；T01–T14 全覆盖映射；10 项待建 P01–P10 如实登记 NOT_RUN/BLOCKED）。
  - [x] 统一物理口径：`docs/model_conventions.md`（电压/LSB/电容五量/时间频率/符号约定/量纲规则，变量名对齐 config.py）。
  - [x] 清单↔代码库一致性门禁：`src/adi_model/inventory_gate.py` + `tests/unit/test_inventory_gate.py`（代码路径/测试 ID/来源哈希/枚举/覆盖五类校验，产出严格类型 summary）。
- **执行记录**：
  - `python -m adi_model.inventory_gate --repo-root . --summary validation_summary.json` → 退出码 0（PASS/BLOCKED 明细见 summary；[00]–[14] 哈希比对 PASS，原文不随仓库分发）。
  - `pytest tests/unit/test_inventory_gate.py -q` → 4 passed。
- **材料目录**：原文 PDF 位于 `~/Downloads/ISSCC2024_9.8_参考文献全集/`
  （可用 `ADC_SOURCES_DIR` 覆盖；文件名以 sources_manifest.json 登记为准）。

## 下一条执行命令（新轮次从这里继续，不要重做总结）

```bash
cd <repo_root>
python -m adi_model.inventory_gate --repo-root . && pytest -m "not slow" -q
```

## 待办（按依赖排序）

1. **P02（T04）**：R1/R2 PhysicalSlicePool 接入 pipeline 主路径——摘
   `TestR1SampleOwnership`/`TestR2PhysicalPoolOnMainPath` xfail 钉子。
   **会移动已发布数值**：须附新旧差异表 + 全量门禁，单独一轮提交。
2. **P05（T10）+ M14 增量（T11）**：参考—RA—ADC2 一阶联立动态
   （τ_RA·dv_o/dt + v_o = G_RA[x_held − v_D(t)]），与 P02 同批发版。
3. **P04（T09）**：M12 一阶标度律升级为主滤波/采样/辅助三节点联立状态模型。
4. **P01（T01）**：短窗口 vs 交织延长窗口系统级对照实验。
5. **P03（T05）**：逐位切换 vs RDAC 直接设最终状态对照实验。
6. **P06（T12）**：有限字长数字核 + 浮点对照。
7. **P07（T13）**：低频 1/f 观察时长验证 + MC 良率置信区间。
8. **P09（T08）**：[12] 跟踪信息源合法性端到端因果测试 + 介电吸收记忆模型。
9. **P10（T11）**：AZ 相位级噪声传递（C_AZ/宽窄带切换/R_BW 扫描）。
10. **P08（T03）**：SPICE/Spectre 测试台——工具/PDK 可用时执行，否则保持
    BLOCKED 并交付可运行接口（不伪造）。
11. 引用页码复核：sources_manifest 中 `citation_status: inherited` 的条目
    （[00]/[00_1]/[09]/[10]/[11]）逐页对原文核实后改 `page_checked`。
12. 阶段 B：把 `docs/model_conventions.md` 的量纲规则升级为自动门禁
    （单位缩放等价测试进 pytest）。

## 纪律（沿用发版流程）

- 接入主路径/移动已发布数值的改动：单独 commit + 新旧差异表 + 四门禁全绿。
- 中断轮次的编辑必须当轮落盘并 grep 验证（历史教训：延迟写入回滚编辑混进提交）。
- 文件内批量替换用 Python 读改写，不用 `sed -i ''`；检索用 Grep 工具不用 BSD grep。
- 未经用户明确授权：不强推 main、不改旧 tag、不自动创建 Release。

---

### 历史检查点

| 日期 | 里程碑 | commit/版本 |
|:---|:---|:---|
| 2026-09-12 | 阶段 A 里程碑 1：四份清单/口径/门禁交付 | v7.0.10 + 未提交增量 |
| 2026-09-11 | v7.0.8–v7.0.10：专利 [12]/[13]/[14] 机制模型 + 第八份复核修复 | 20bf85c / 35dab64 / ffcc011 |


---

## 当前检查点（2026-09-13，v8.0.0 发版：v8 合并 + 清单重刷）

- **commit**：合并提交 0c83fdd（origin/main 的 PR #1 合并点 417f651 ∪ 阶段 A f74a5de），本轮在其上叠加：CHANGELOG 字节账、inventory 重刷、HANDOFF 本条。
- **基线**：v8 分支本地复跑 394 passed + 0 xfail；PR #1 CI 8 项全绿（py3.10–3.13、determinism 逐字节、wheel）。
- **本轮完成**：
  - [x] PR #1（codex/physical-behavioral-closure，v8.0.0 物理与行为闭环）经独立验证后并入 main。
  - [x] `mechanism_inventory.json` v1.1：M11/M12 升 INTEGRATED_SCENARIO（pretracking/auxiliary charge 已进 pipeline_engine，代码引用逐条核实）；M13 保持机制级（ref_track 未进 pipeline，联立动态部分由 M4.2/M4.3 关闭）；P02/P04/P05/P06/P09/P10 → PASS（挂 v8 检查点证据）；P01/P03 仍 NOT_RUN，P07 部分（低频已覆盖、MC 良率未做），P08 仍 BLOCKED。
  - [x] CHANGELOG 8.0.0 补参考产物字节账：results.json SHA256 `a1ccd92f…35ac70`（v7.0.10）→ `f3e1a796…c0eb`（v8.0.0），变更可溯源到所列机制集成。
  - [x] 门禁：inventory_gate 11 PASS / 0 FAIL（合并树上复跑）。
- **下一条执行命令**：发版收尾（四门禁 → build → commit/tag v8.0.0 → SSH push → gh release create 四资产 → 等 CI）。
- **合并后仍开放**：P01（采集窗口对照实验）、P03（逐位切换 vs RDAC 直设对照）、P07 尾（MC 良率置信区间）、P08（SPICE 台账，BLOCKED）；报告科学口径两处盯紧——`paper_literal(9b)` 的 ADC2 12b 窗口是新 [ASSUMED]，63+8 分段是显式模型选择（论文未披露该拓扑）。
