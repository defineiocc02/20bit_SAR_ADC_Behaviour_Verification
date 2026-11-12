# 接手说明（Handover）

更新时间：2026-11-13
受测/交付版本：分支 `merge/adi-repro`（HEAD 见 `git log -1`；本文档随后续提交滚动更新）
远端 `main`：`bc9a16a`，**未合并、未改写**

---

## 1. 这个项目在做什么

用行为级模型（Python）复现 ADI ISSCC 2024 §9.8 那颗 20 位 40 MS/s SAR ADC，
并配一套数字 RTL 与 Python–RTL 逐位对拍。原始资料是用户提供的：

* 论文 `[00]_A_9.3nV_rtHz_20b_40MS_s_94.2dB_DR_...pdf`
* 讲稿 `[00_1]ISSCC2024_ppt.pdf`（44 页）
* 六份专利 `[09]`–`[14]`
* 均位于 `/Users/zhaoyi/Downloads/ISSCC2024_9.8_参考文献全集/`
* 目标产物是**行为级证据**，不是硅测、不是 signoff

用户的硬性要求（多轮任务书反复强调）：

1. 只有**核对过原件**的话才能写"专利披露/论文采用"；
2. 工程假设必须显式标注，不能用替代拓扑冒充专利结构；
3. **63+8 工程替代模型已被用户明确否决**，要求彻底删除、统一到专利 17/5；
4. 不许用"写了免责声明""图已入库""测试数多"代替实现与验证。

---

## 2. 现在的状态（一句话）

**主链已统一到专利 17/5；全量测试 0 失败（1758 + 119）；干净克隆可复现。**

| 维度 | 状态 |
|:---|:---|
| 拓扑 | 主链 17/5（主阵列 15×2C + B4 + S4；子阵列装配 4 列）；63+8 已从主链移除 |
| 单元测试 | `tests/unit + verification + audit + regression` **1758 passed, 4 skipped, 4 xfailed** |
| 集成测试 | `tests/integration` **119 passed, 48 skipped, 0 failed**（48 个 skip 需 VERILATOR）|
| 静态检查 | `tools/check_engineering.py` **7/7** |
| 行为指纹 | 与基线**逐位一致**（C7 门禁校验元数据）|
| 干净克隆 | `tools/verify_clean_clone.sh` **8/8 全过**（含矩阵重跑与入库数据逐项比对）|
| V1 RTL 对拍 | 7 模块 / 4931 向量 / 0 不一致（VCS）——**历史结论**，本机无 VCS 无法复跑 |
| V3 顶层 | `ELABORATED_ONLY` —— **本机无 VCS，NOT_RUN** |

### 2.1 仍然存在的已知缺口（不隐瞒）

| 项 | 状态 |
|:---|:---|
| `sim/fixtures/fig19_ideal_ports/` | **63+8 时代的遗留向量集**。整套按 63/8 构造，无生成器脚本、本机无 VCS 无法验证重建 ⇒ 保留为历史证据并加 README 警告，**不是当前 golden 向量** |
| `rtl_observation.py` / `export_rtl_params.py` 的 dither 合同 | 要求几何 `(63, 8, 0, 511)`。这与上面那份 fixture **自洽**；要改必须连 fixture 一起重建 |
| 4 个 skip | `unit_therm` dump 陈旧（96 行 vs meta 84），需仿真器重生成 |
| 48 个 skip | `test_source_bound_replay` 等需 VERILATOR，本机无 |
| `execute_pipeline` | 662 行；AST 分析显示两个最大块自由变量 42/53 ⇒ **不可机械抽取**，需重新设计数据流 |
| dither 配置（A6/C2/C3）| INL 4000+ LSB20，**深度失配**，数字不稳定、**不应引用**（见 §5） |
| 后仿 / PEX / 硅测 | **全部 NOT_RUN** |
| `source_conformance` | 硬编码 `NOT_ASSESSED`：单跑不能自证与原件一致，这是设计如此 |

## 3. 已完成（都有提交与证据）

### 3.1 确定性缺陷（有反例）

| 缺陷 | 证据 |
|:---|:---|
| `ElementWeightCalibration.reconstruct` 底板项符号反了、分母未限活动片 | 审查反例 `x=0.2`：旧 `−0.466667` → 新 `0.200000`；`tests/unit/test_element_reconstruct_equation.py` |
| `run_review2_figure.py:91` `beta[:, None]` 广播轴错 | 合成 3×8×4 下旧形式直接 `ValueError`，新形式逐步相乘一致 |
| 7 个比较器里 6 个不查记录数（删末行仍通过） | 14 项参数化测试：删末行/空 dump 逐个被拒 |
| VCS runner 只要有非空 .out 就 `PASS_RUN` | 现查 `simv` 退出码 + `$fatal`/`FATAL`/`Error-[` |
| `unit_therm` 只比 slot 0 | 现逐片逐位；注入"翻 slot 5"被抓 |
| `recon_core` 漏比 `gain_err`/`adc2_ovf`/`result_sample_id` | 三个字段补齐，各一条注入测试 |
| `main_count=64/127` 在 6 位端口装不下，是**假覆盖** | 写出时禁止静默截断，向量 96→84 |
| `metrics_lsb20` 是 `metrics` 的独立副本（14/17 逐字节相同） | 收成薄转发，七入口 28 摘要零漂移 |
| `charge_ref` 与 `charge_ref_task` 电荷公式重复 | 收成薄转发，固定网络前后摘要一致 |
| 行为指纹工具只写不比对、静默覆盖 | 补 `--check`（漂移 exit 1）、拒绝覆盖（exit 2） |

### 3.2 结论层面的修正（重要）

| 旧结论 | 现状 |
|:---|:---|
| "合同 `Σ(W·a)` 与 RTL `G=T` 不一致" | **撤回**。`a` 是信号连接、`on` 是开关；合同明写"普通单位 a=1"，RTL 一直对 |
| "逐码 LUT 与 DEM 原理不兼容" | **撤回**。那是拿错配模式（DEM 关训练→DEM 开测试）得出的。同模式四组：A 7.1/24.1、B 20.5/13.6、**C 15.9/13.6**、D NOT_RUN。C 仍略差，原因是状态间方差（训练残差 82→105 µV），不是模式错配 |
| "逐单元权重不可辨识" | **部分撤回**。原实验夹带 1 mV 噪声 + beta 广播错；修正后残差 31.7 µV，与 ADC2 量化步长/√12 一致 |
| "SNDR 88 dB 是热噪声限制" | **不是**。纯热噪声 95.0 dB，**纯失配 89.1 dB 才是主因**，随机换片再吃 1.4 dB |
| "MX1 把 −1 送到负轨" | **撤回**。FIG.20 的 `0/1` 是选择控制 |
| "362 是跨片 crossbar" | **改口径**。正文里是 **signal gate** |
| "辅助输入为背栅寄生充电路径" | **降级**。FIG.1/FIG.2 上看不出背栅结构 |
| "SUB_WEIGHTS 5 项" | **错**。跨桥后只有 8/4/2/1 四个；第 5 个是桥接电容本身。改成 4 项后 β=1/16、端点比值 **1.000000** |

### 3.3 结构整理

* `execute_pipeline` 691 → 604 行（98 行纯校验提成 `_validate_request`，指纹全等）
* A/C 矩阵从 `tools/` 提到 `experiments/configs/matrix.py`
* `tools/check_engineering.py`：依赖方向 / CAAZ 隔离 / 文档链接 / 图-表同源 / 重复公式 / 入口，6 项，抓出并修掉 8 条死链

---

## 4. 未完成（按优先级）

### P0 拓扑统一 —— **已完成**（2026-11-13）

```
✅ unary_rotation_masks 已删除；主链跑的是专利的 17/5
✅ 码本律换成图上的 thermal / B4 / sub 三段
✅ 掩码绑定 = fig11_masks
✅ 逐单元权重非均匀（2C/1C、8/4/2/1）并贯通 nominal_decoder / CalibrationSpec
✅ RTL 参数与 ROM 已按 18×22 重导出
✅ 工具链里的 63+8 硬编码已清除（71/63 → chip.n_main+n_sub）
```

落地过程中修掉的**真实缺陷**（按发现顺序）：

| 缺陷 | 症状 | 修复 |
|:---|:---|:---|
| 三处码→掩码律不一致 | 残差溢出、ra_sat 8/8 | 统一码本律/掩码律/电荷律 |
| ADC2 窗口缺中点 → 后改为**错误**中点 | 负残差全溢出 | 先加中点、后经判码规则证明残差是**单边** [0,1] LSB1 而移除 |
| SADC 阈值是等距表、码本是实测 | 理想配置 5.7% 增益误差 | 阈值改为直接取码本 |
| 占位单元（S4/哑位）给了真实权重 | 定点偏 −0.1766 V | 权重置为极小（定点仍见"严格为正"） |
| 子阵列装配了虚线占位的第 5 个 C | 三套电容口径差 3.14% | 器件只装 4 列（ΣB=15C），β 三处统一为 1/16 |
| ADC2 用 12 bit 配 14 bit 的窗口 | 二级 LSB 0.60 LSB20，吃不满 20 bit | 改 14 bit（依 ADR-0003） |

**成果**：A0 理想配置 INL **966 → 32.2 LSB20**，`adc2_over` 0/50，码本端点 ±3.000000、
步长极差 2.7e-15、`q_free/C = 1.00000000`；测试 1442 passed / 0 failed。

### P1 专利机制缺口 —— 大部分已交付**判据模块**（未接主链）

原则：原件没给"本芯片是否启用"的，只交付可核对判据，不擅自接主链。

| 机制 | 状态 | 交付物 / 依据 |
|:---|:---|:---|
| 保留列（预留 `0` 输入 + MUX） | ✅ 判据已交付 | `control/reserved_column.py`，`[11]` FIG.20 图面计数（350 平衡、352 输出 = 8×32C） |
| AUX 支路 | ✅ 判据已交付 | `control/aux_input.py`，`[13]` FIG.1/2 两源并联结构 + 三条不变式 |
| `[09]` 转换级 | ✅ **已核对** | `docs/mechanisms/patent09_reconciliation.md`（正文第 20–32 页逐字 9 条）；radix<2 冗余判据见 `control/redundancy.py` |
| 参考 buffer | ✅ 判据已交付 | `control/reference_buffer.py` + `docs/mechanisms/reference_fig5.md`，`[14]` FIG.5 三条语义（单位增益 / 尾电流封顶电荷 / 参考非静止） |
| 362 信号门 | ✅ 判据已交付 | `control/shuffle_gate.py` + `docs/mechanisms/fig19_shuffle_gate.md`，`[11]` FIG.19 原生图逐元件核对（两级分配 / B8 直通 / 选通不改权重） |
| 跨片分配 | ✅ 判据已交付 | `control/cross_slice_mux.py` + `docs/mechanisms/fig20_cross_slice_mux.md`，`[11]` FIG.20 原生图核对：**逐单元 2:1 MUX**（不是整片交换） |

**六份专利 [09]–[14] 均已完成至少一轮原件核对；P1 六项全部有判据或核对结论。**

### P2 验证与结果 —— 图已按新模型重生成

| 项 | 状态 |
|:---|:---|
| V2 事务 trace（`epoch+sample_id+event_index`） | ✅ 三段主键 + 唯一性 + 每笔连续 + 样本守恒；**逐事件**（E>1）待 joint 路径 |
| V3 真实事务（配置写入 + 样本流） | 未做 |
| V4 闭环 | 未做 |
| **指标与图重生成** | ✅ **已完成**：13 张图 + `figures.json`（带 `source_sha` 溯源）；C4 门禁会在图过期时输出 STALE 警告。数字见 `docs/reviews/20261113_p3p4/figures/RESULTS_20261113.md` |
| CI 10 项 | 部分环境相关项仍跳过（见 §5 坑）|

### P3 仍缺**原件依据**的两项（已定量，非"没做完"）

1. **dither 幅度 vs ADC2 窗口** —— **已重新定性为"可选增强不兼容"，不是缺陷**：
   论文 [00] 正文中 "dither" 出现 **0 次**，dither 全部来自专利 [10]，
   是可选增强手段；论文描述的芯片未提及启用它。
   dither 的 upper 段需要 **5.5 LSB1** 残差摆幅，
   而窗口 `[-0.0375, 0.4125]` 只覆盖 **1.24 LSB1**（`0.45 = 1 LSB1·G + 2×0.0375`
   明显是为**无 dither 的残差**设计的）。默认路径 A0–A5 不带 dither，完整自洽。
   窗口 `0.45 V = 1 LSB1×G + 2×0.0375` 恰为"无 dither 的残差 [0,1] LSB1"量身定做，
   即它根本没留 dither 空间。论文/PPT/专利均未给 ADC2 窗口为何是这个值。
   判据：`simulation/parameter_consistency.py`（`inference_table()` 里可见）。
2. **S4 的编码规则**：定点归一口径（权重和 31.9375C）与物理采样口径（32.94C）
   本就不一致，差 1 个 S4 的 1C，造成 **37 LSB20** 的已知误差地板。
   论文正文 **"S4" 出现 0 次**；唯一的 "dummy" 指版图 dummying，与此无关。
   两次尝试证明三种口径无法同时相等（见
   `docs/mechanisms/p1_gaps_20261113.md` §14）。

---

## 5. 接手必须先知道的坑

1. **`vm-meow` 会掉线**：表现为 `Connection timed out during banner exchange`。
   此时 `run_verification` 返回 `NOT_RUN`（正确行为），相关测试标 skip 并写明原因。
   4 个 skip 就是 `unit_therm` dump 陈旧（向量 96→84），主机恢复后跑
   `python3 -m verification.run_verification --level module --only unit_therm` 即可消掉。

2. **专利 PDF 五份无文本层**：只有 `[12]` 有（45 868 字符）。
   其余按页图读：`pypdf` 抽 `page.images` → 缩到 1400px → 看图。
   `patents.google.com` 在本机 curl 502 / WebFetch 失败，**别浪费时间重试**。

3. **不要相信"测试全绿"**：本项目历史上多次出现"测试通过但覆盖是假的"
   （`main_count=64/127` 被截断、删末行不被发现、比较器只看 slot 0）。
   新增任何比较器，先写"删末行/空 dump/翻位"三种注入。

4. **`a` 与 `on` 是两个量**：`signal_mask` 是信号连接，`main/sub_masks` 是转换开关。
   把它们混用会得出"gain 随 mask 变"这种错误结论——已经踩过一次。

5. **图与表必须同 run_id**：`figures.json.source_sha` 与矩阵参数不一致时，
   `check_engineering.py` 的 C4 会报。历史上出过"图停在 12 个提交前"的事故。

6. **改完跑这三条**（顺序不能反）：

```bash
python3 tools/check_engineering.py
PYTHONPATH=src python3 tools/behavior_fingerprint.py --check docs/restructure/behavior_fingerprint.json
PYTHONPATH=src:. python3 -m pytest tests/unit tests/verification -q
```

---

## 6. 接手第一步（建议命令）

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
git log --oneline -20                      # 看最近改了什么
git status --short                          # 应为空
python3 tools/check_engineering.py          # 6/6
PYTHONPATH=src:. python3 -m pytest tests/unit tests/verification -q   # 1561 passed / 4 skipped
```

然后读这三份，按顺序：

1. `docs/decisions/20261112_fig11_unification.md` —— 拓扑统一的清单与卡点
2. `src/adi_model/control/README.md` —— DEM 与 dither 的原文对应、已撤回的说法
3. `docs/mechanisms/source_reconciliation.md` —— 我判读错过的 7 条（R1–R7）

---

## 7. 需要用户拍板但还没定的

* `[11]` col.23 明确写了 "31 unit cells in a 5 bit example or **63 unit cells in a 6 bit example**"，
  即 63 单元是**原文披露**的结构。用户仍要求删除 63+8。
  **接手人需要就这一点再确认一次**：是"删掉披露实施例"，还是"63 单元归到采样 DAC 层、
  Fig.19 归到 DEM 层"（两者本不在同一层）。

* 图上 `S4` 这个 1C 与 `B4` 并列，但它的编码规则（进主码 5 bit 的哪一位，还是 dither 上支路）
  图上没标。当前实现按"两者共同构成主阵列两个 1C 步进"处理，属未确认。
