# W0-E 论文/专利/PPT → 实现与验证覆盖矩阵

> 目标：把"原文事实"与"模型解释/工程假设"分开，并给出每条 claim 的代码/测试/证据状态。
> 当前机器台账：`20b10mtask@750a2e0/trace/source_matrix.csv`（43 行）。
> 证据等级：PAPER（原文）/ PATENT（权利要求）/ PPT（讲稿）/ OCR（转写）/ DERIVED / ASSUMED。

---

## 0. 结论

1. 台账 43 行：**PASS 18 / MISSING 11 / OUT_OF_SCOPE 8 / NOT_RUN 5 / PARTIAL 1**。
2. **只覆盖 US10505561B2（dither）一个专利的 8 行**；`US10516408B2`、`US10511316B2`、`US10707889B1`、`US10541702B1`、`US10826519B1` **未入机器台账**。main 的 `docs/archive/engineering_vs_patents_papers.md` 有文字对照，但没有 claim_id/测试绑定。
3. **台账滞后于代码**：PAPER-06（参考建立）、PAPER-07（DAC 带宽）、PAPER-08（随机时偏）仍标 MISSING，但 `src/adi_model/behavioral/reference.py`、`timing.py` 已在 M4/M5 落地。W1 必须重审这 3 行。
4. **dither 专利 OCR 只覆盖 page_pdf=22..36**；不能据此说"专利没写某参数"，只能说"OCR 覆盖范围内未见"。
5. **CAAZ 不属于 ADI 覆盖**：它来自 PKU VLSI 2025，是独立扩展；不能计入 ADI 复现完成度。
6. 论文的**实测性能**（94.2 dB DR、9.3 nV/√Hz、50 mW、INL/DNL）当前只能是 `NOT_RUN` 或 `NOT_REPRODUCIBLE`（无硅），不能拿模型数冒充。

---

## 1. 来源清单

| ID | 来源 | 仓库原件 | 页/图 | 用途 |
|:---|:---|:---|:---|:---|
| PAPER | Bodnar et al., ISSCC 2024 9.8 | `references/ISSCC2024_PAPER.pdf` | PDF1–2 + Fig.9.8.1–7 | 主架构 |
| PPT | 同工作 44 页讲稿 | `references/ISSCC2024_SLIDES.pdf` | 9–12、17–18、19–22、23–27、29–31、33–35 | 机制/实测 |
| PAT-D | US10505561B2 dither | `references/patent/US10505561B2_OCR.txt` + 裁图 | Fig.4–5、19；page 22/33 | dither |
| PAT-S | US10516408B2 stage | 外部链接（未入库） | Fig.7、15–17 | 逐位 follower |
| PAT-M | US10511316B2 DEM | 外部链接（未入库） | Fig.11、20、21 | DEM |
| PAT-I | US10707889B1 interleaving | 外部链接（未入库） | IDLE→TRACK | 交织/预跟踪 |
| PAT-A | US10541702B1 AUX | 外部链接（未入库） | 代表图 | 辅助输入 |
| PAT-R | US10826519B1 reference | 外部链接（未入库） | 代表图 | 低功耗参考 |

> 书目问题：PAPER PDF3 的 [12] 印成 **10,797,889**，但该号码对应其他主题；题名/发明人/ADI 归属吻合的是 **US10707889B1**。W1 台账必须同时保留 `printed_identifier=10,797,889` 与 `resolved_candidate_identifier=US10707889B1`，并记录按题名/作者校正的依据；不修改原 PDF。

---

## 2. 论文/PPT 覆盖矩阵

| claim_id | 来源/页图 | 原文事实 | 当前实现 | 状态 | 缺口/注意 |
|:---|:---|:---|:---|:---:|:---|
| ADI-01 | PAPER p1 / Fig.9.8.1 | 20b、40 MS/s、±3 V（6 Vpp） | main `DefaultParameters` 40MHz/±3V；task `ExperimentConfig` 10MHz/5V | **PASS（main）** | 两个 profile，不能混 |
| ADI-02 | PAPER p1 / Fig.9.8.1 | 8 转换 + 8 采集 + 2 备用，18 池 | main `slice_pool`；task `scheduler` | **PASS** | 18/8 唯一真源需统一 |
| ADI-03 | PAPER p1 | 9b 一级量化 | `codes.CARSE_BITS=9`；main `sadc` | **PASS** | — |
| ADI-04 | PAPER p1 | 量化器与 RDAC 分离 | main `sadc`/`rdac`；task `coarse_decision` | **PASS** | 不得共享判决状态 |
| ADI-05 | PAPER p1；PAT-S Fig.7/15–17 | RDAC 作 follower，已决结果逐步加载 | main `conversion` 有 follower；task 静态实验用最终码 | **PARTIAL** | "set directly to final state"是实验选择，不是原文因果；需逐位 trace |
| ADI-06 | PAPER p1；PPT 23–27 | 参考建立、粗/精两段切换、无缓冲 RA 相 | task `reference.py` 有建立/预充；ledger 仍 MISSING | **PARTIAL（台账滞后）** | 重审 PAPER-06/SLIDE-09；接 main `reference_charge` |
| ADI-07 | PAPER p1；PPT 19–22 | DAC 采样带宽 >10×（设计 15×） | task `timing.py::dac_pole_attenuation`；ledger MISSING | **PARTIAL（台账滞后）** | 重审 PAPER-07/SLIDE-05 |
| ADI-08 | PAPER p1；PPT 19–22 | 时偏/增益/带宽失配；10 ps 未补偿、0.6 ps 残余 | task `timing.py`；main `timing` 是相位，不是抖动 | **PARTIAL** | 重审 PAPER-08/SLIDE-06 |
| ADI-09 | PAPER p1；PPT 29–31；PAT-M Fig.11/20/21 | 8/18、水平 3b/垂直 3b/6b DEM、binary-to-unary 桥接 | task `fig11_dem`/`fig21_bridge`；main `dem.py` 基本分段 | **PARTIAL** | 三候选各自 topology_id；不得改名成原芯片 3D DEM |
| ADI-10 | PAPER p1；PPT 17–18；PAT-D Fig.4–5/19 | dither 分低/高路；范围增强 2b；补充 DEM | task `dither_word_gen`/`eight/model`；main `dither` program | **PARTIAL** | 七片≠八片；采样态注入未闭合 |
| ADI-11 | PPT 33–35；PAPER p1 | 普通逐笔 RA AZ；动态 ADC2 带宽 | main `chopping_az.conventional_az` 含两点平均；task 无普通 AZ | **MISSING/REBUILD** | 必须新建全速率 `ra_az.py`；不能拿 CAAZ 代替 |
| ADI-12 | PAT-I IDLE→TRACK；PAPER p1 | 交织 tracking/预跟踪 | main `pretracking`/`input_network`（无 binding 拒绝） | **PARTIAL** | 接真实物理状态；无未来信息反例 |
| ADI-13 | PAPER p1/PPT 32 | INL +2.2/−1.8 ppm、DNL ±0.27 ppm | ledger NOT_RUN；main `unmodeled` 含参考/RA 非线性 | **NOT_RUN** | 20b LSB20 逐码；K=10 |
| ADI-14 | PAPER p1/PPT 36–37 | DR 94.2 dB、NSD 9.3 nV/√Hz、SNR 93.7 dB | ledger NOT_RUN；task 无噪声源旧版 | **NOT_RUN** | 需 M3 噪声 + M7 对标；不得用失配地板冒充 |
| ADI-15 | PAPER p1/PPT 32–33 | THD/HD2/HD3 | ledger MISSING；无 C-V 非线性 | **MISSING** | 只能来自失配/dither 统计；设双界 |
| ADI-16 | PAPER p1 | 片外系数、片上位权修正 | main `calib/weight_calibration`；task NLMS/Q28 | **PARTIAL** | 训练/留出分离；Q 格式合同 |

---

## 3. 专利覆盖矩阵

| claim_id | 专利/权利要求 | 机制 | 当前代码 | 状态 | 缺口 |
|:---|:---|:---|:---|:---:|:---|
| PAT-D-01 | US10505561B2 claim 1 | dither 只加 sampling DAC、不加量化器；数字改码 | task `dither_word_gen`；main `dither` | **PARTIAL** | 注入/扣除/改码三要素需同笔绑定 |
| PAT-D-02 | claim 2 | 采样态置非零码（电荷注入） | main `dither`/`sampler` 有采样态模型 | **PARTIAL** | 8 片物理路径未闭合 |
| PAT-D-03 | claim 7 | fractional dither <1 LSB | task `fig19_seven` 分数位 | **PARTIAL** | 仅七片示例；八片需重解 |
| PAT-D-04 | claim 8 | integer dither 1 LSB 倍数 | task `fig19_seven` 整数位 | **PARTIAL** | 同上 |
| PAT-D-05 | claim 12 | n bit 拆 m bit + 数字修正 | task `fig19_seven` 6→m/L/u | **PARTIAL（PAT-06）** | 后级再减一次未独立实现 |
| PAT-S-01 | US10516408B2 Fig.7/15–17 | 逐位 follower/已决结果加载 | main `conversion` | **NOT_IN_LEDGER** | 需 claim_id/页图/测试 |
| PAT-M-01 | US10511316B2 Fig.11/20/21 | 2D DEM/3b 水平/垂直 | task `fig11_dem`/`fig21_bridge` | **NOT_IN_LEDGER** | 需补台账；排列空间 64 vs 512 |
| PAT-I-01 | US10707889B1 | IDLE→TRACK 交织 | main `pretracking`/`input_network` | **NOT_IN_LEDGER** | 需补台账；无 binding 拒绝 |
| PAT-A-01 | US10541702B1 | AUX 输入电荷 | main `input_network` auxiliary | **NOT_IN_LEDGER** | 需补台账；与 dither 辅助参考分开 |
| PAT-R-01 | US10826519B1 | 低功耗参考 | main `reference_charge` | **NOT_IN_LEDGER** | 需补台账；无控制回路 |

---

## 4. 台账缺口（当前 43 行的 MISSING/NOT_RUN）

| source_id | 状态 | 量 | 备注 |
|:---|:---:|:---|:---|
| PAPER-05 | MISSING | 差分满量程 6 Vpp | 默认 5.0 V 是 `baseline_10m_5v`；需 `paper_isscc2024` profile |
| PAPER-06 | MISSING | 参考支路动态建立 | 与 `reference.py` 冲突（M4 已建）；**台账滞后** |
| PAPER-07 | MISSING | DAC 采样带宽 >10× | 与 `timing.py` 冲突（M5 已建）；**台账滞后** |
| PAPER-08 | MISSING | 残余采样时偏 | `skew_s=5ps` 是确定性偏斜，不是高斯；语义不同 |
| PAPER-11 | NOT_RUN | 静态线性度 | 机制在，未按 paper 预设跑 12 格 |
| PAPER-12 | NOT_RUN | DR/SNR/NSD | 旧版无噪声源；M3 后需重跑 |
| PAPER-13 | MISSING | THD/HD2/HD3 | 无 C-V 非线性 |
| SLIDE-05 | MISSING | DAC 带宽设计值 15× | 与 PAPER-07 同参数；设计值/下界分开 |
| SLIDE-06 | MISSING | 时偏敏感度/杂散 | 10 ps 未补偿 vs 0.6 ps 残余，不能互换 |
| SLIDE-09 | MISSING | 参考两段式切换 | 与 PAPER-06 同指一事；无缓冲限定 |
| SLIDE-10 | MISSING | 参考低频噪声/失调 | "无 1/f"指缓冲本身；整机拐点 40 Hz |
| SLIDE-13 | NOT_RUN | 静态线性度（讲稿记法） | ppm vs LSB 单位冲突；20b 1 LSB=5.72 µV |
| SLIDE-14 | MISSING | INL 成因清单 | 参考/输入/数字串扰三项 |
| SLIDE-16 | NOT_RUN | 低频 DR/SNR/SFDR | DR 94.6 vs 94.2 冲突；窗口 94.2±0.5 |
| SLIDE-17 | MISSING | NSD/1/f 拐点 | 9.3 vs 8.8 冲突；窗口 9.3±1.0 |
| SLIDE-18 | NOT_RUN | 高频 SNR/THD | 与 PAPER-13 冲突；论文注明信号源噪声 |

> 其中 PAPER-06/07、SLIDE-05/09 的"代码已存在但台账 MISSING"是 W1 必须修的第一类台账错误；PAPER-05/08/13、SLIDE-06/10/14/17 是真实缺口，需 W6 补。

---

## 5. profile 与覆盖

| profile | 覆盖的 claim | 不能覆盖的 claim |
|:---|:---|:---|
| `adi_isscc2024_40m_6vpp` | ADI-01..16 | 10MS/s/CAAZ/DSP |
| `engineering_10m_10vpp` | 工程变体、task1/2/3 证据 | ADI 40MS/s 实测 |
| `patent_examples` | PAT-D/S/M/I/A/R 代表例 | 整芯片指标 |

**规则**：同一运行必须落 `profile_id` + `topology_id`；`preset` 列（paper_isscc2024/baseline_10m_5v/both/na）目前只是台账标签，不是运行时对象。W1 必须把它变成可拒绝的运行时字段，否则"paper 预设"会静默退回默认 10MS/s/5V。

---

## 6. 完成度口径

| 结论 | 当前 | 说明 |
|:---|:---:|:---|
| 工程整合完成 | ❌ | 尚未合并；CAAZ 未隔离；task 不可安装 |
| 公开机制完成 | ❌ | ADI-05/06/07/08/09/10/11/12 均 PARTIAL；ADI-11 MISSING |
| 系统行为完成 | ❌ | 无统一事件/样本事务；参考/RA/ADC2 未联立 |
| 定量/实现验证 | ❌ | INL/DNL/DR/NSD/THD NOT_RUN；RTL 未重跑 |

> 不能把"输出 20 位码"或"pytest 220 passed"当作复现完成。当前最多能说：**main 的 L1 名义链可跑；task 的 L1 任务证据可跑；ADI 公开机制的联合复现尚未开始。**
