# 请审阅邮件（草稿）

> 用途：发给外部审查专家的自包含说明。正文可直接复制进邮件客户端；
> 代码块里的路径与命令都可直接运行。

---

**收件人**：审查专家
**主题**：【请审阅】20bit SAR 行为级模型分支 `merge/adi-repro`（PR #9）——机制补全、审查响应与统计口径更正

---

## 一、三句话说明我的诉求

1. 这个仓库是 ADI ISSCC 2024 §9.8（20b 40MS/s 两步式 SAR，40 nm）的**行为级复现**，不是 RTL、不是后仿、不是硅测。
2. 这一轮（P3/P4）我补了四个机制模型、按你上一版审查（A01–A07）改了七处并**撤回了三条结论**、补齐 12 张图；同时把静态指标从 8 次重复提到 32 次，**因此又改写了两个结论**。
3. 我需要你重点攻击的是**可辨识性、统计口径与证据层级**这三件事——不是"数字好不好看"。

---

## 二、仓库与范围

| 项 | 值 |
|:---|:---|
| 仓库 | `defineiocc02/20bit_SAR_ADC_Behaviour_Verification` |
| 分支 | `merge/adi-repro`（**未合并 main**） |
| PR | #9 `merge/adi-repro → main` |
| 基线 | `main bc9a16a` |
| 本轮起点 | `20ec656`（你上一版审查针对的提交） |
| 本轮终点 | `c817e91` |
| 证据层级 | L1 行为（模拟浮点 + 20 位定点解码）+ L2 数字 RTL 编译；**无后仿、无 PEX、无硅测** |

---

## 三、这轮改了什么

### 3.1 P3：四个机制模型（`src/adi_model/`）

| 文件 | 做的是什么 | 可被推翻的检查（我希望你验的就是这些） |
|:---|:---|:---|
| `follower.py` | RDAC 按**已决前缀**逐事件加载 | 前缀截断不变性：28 672 检查 / 0 违约；故意偷看最终码的实现被抓 24 576 次 |
| `dem3d.py` | 三维 DEM 的两套具名掩码（Fig.11 17/5、默认 63+8） | 名义守恒（1 LSB 电荷误差必须返回 1.0）；低/中/满码逐 (片,单元) 覆盖 |
| `dither_port.py` | dither 从"码减 U"改成独立电荷口 | 非法码计数（不夹紧）、非等间距阵列的码偏移残余、64 字输入域缺口 |
| `track_aux.py` | TRACK 预置的电荷记账与因果 | `ready < precharge` 时间判据；`lookback=1` 必须被判不因果 |
| `mask_calibration.py` | 逐码输出校准（**不是**逐单元权值）+ 逐单元拟合的负结果 | 合成数据上能精确恢复；真实数据上残差 14 V、权重 −43 V |

关键数字（细节在 `docs/reviews/20261113_p3p4/01`–`04`）：

- follower 与"最终码一次加载"的最终电荷**逐位相同**；传统试探 SAR 的往返电荷是它的 **3.01×**（`F06`）。
- DEM 把 INL 的失配斜率压到 **0.27×**（无 DEM 23 372 ppm/σ，DEM 6 327 ppm/σ）。
- dither 用码偏移实现时，早期候选码有 **6 368** 个非法事件；独立电荷口为 0；64 字输入下界缺口 **35.16 mV**。
- TRACK（**回看 2 笔**，因果）使输入电荷降 **3.20×**。上一版的 6.36× 用 lookback=1，**已撤回**。

### 3.2 对你上一版审查 A01–A07 的处置（`09_audit_response.md`）

| 编号 | 结论 | 我改了什么 | **撤回了什么** |
|:---|:---:|:---|:---|
| A01 | 属实 | 相位空间 15→64；序列改 `h=(i+7·(i//9))%63`，沿每片活动子序列为 `r+16k`，与 63 互质；审计改逐码+卡方 | "使用极差全 0"（只测满码的假通过） |
| A02 | 属实 | 同时报 C / V / LSB20 三个口径 | 旧 `max_spread_lsb20`（单位实为法拉） |
| A03 | 属实 | 改时间判据、预测值由数字码本重建、最小回看 2 笔 | **TRACK 6.36×（非因果）** |
| A04 | 属实 | droop 用保持时间、`residual_v` 单符号、噪声按 sample_id 键控 | AZ droop 旧数值（传 `n/fs` 凭空造漂移） |
| A05 | 属实 | peak/pp 分开 | **"350 µA ↔ 0.907 MHz"整段**（论文横轴是直流差分输入） |
| A06 | 属实 | 1 pF→1 fF、新增 `normalization_only` | "ENG_10M 是 1 pF" |
| A07 | 属实 | 非法 shifted 码不再夹紧混入统计 | 旧残差数值 |

回归入口（4/4 PASS）：`PYTHONPATH=src python3 tools/check_audit_findings.py`

### 3.3 统计口径更正（这条最影响结论）

按你第 4 节"静态指标用充分统计"的要求复查，发现 8 次重复的 INL 里有一大块是**测量噪声**：`INL 地板 ≈ σ_e/√R`。改成 32 次后：

| 配置 | INL @8 次 | **INL @32 次** |
|:---|---:|---:|
| 失配 σ=0.1% | 25.6 | **24.5** |
| +DEM | 14.5 | **6.2** |
| 逐码校准 | 11.0 | **10.5** |

**两个结论因此改写**：DEM 是 3.9×（不是 1.75×），且优于逐码校准（2.3×）；失配扫描里 DEM 斜率 0.27×。

### 3.4 校准：我为什么没做 LMS

- 逐单元权值在本观测下**不可辨识**：`fine_v` 是 RA 放大后的残差，式内 `G·C·x` 与 `G·Q_DAC` 各约 96 V、相减只剩 0.1 V，1e-3 抵消把 90 µV 噪声放大到 V 量级（实测残差 14 V、权重 −43 V、理想器件也解出负值）。
- 同一份代码在合成数据上能精确恢复（有测试），所以是**数据可辨识性**问题，不是实现 bug。
- 能做的是**逐码输出修正**（512 项表）：INL 2.3×、SFDR +10.5 dB、NSD 不变。
- 5 个制造种子下：**把一颗芯片的系数套到另一颗，INL 比不校准还差 58%**（32.5 vs 20.6 LSB20）——系数是芯片特定的。

### 3.5 图（`docs/reviews/20261113_p3p4/figures/`）

F01–F12 全部产出，每张带配置、seed、源码 SHA、重绘命令与"不能声称"。三张最值得先看：
`F02`（DEM 低码覆盖 32/63 → 63/63）、`F03`（INL/DNL 五条配置）、`F11`（5 种子校准泛化）。

---

## 四、我建议你怎么读这个仓库（按顺序）

| 顺序 | 文件 | 看什么 | **别被什么误导** |
|:---:|:---|:---|:---|
| 1 | `docs/reviews/20261113_p3p4/README.md` | 五条判定（整合/机制/系统/对标/RTL）分开写，不合并 | 不要把"工程整合 PASS"读成"性能 PASS" |
| 2 | `docs/reviews/20261113_p3p4/09_audit_response.md` | 七条处置 + 三条撤回 + §4.1/§4.2 追加 | 这里是"承认错误"的清单，先看它再看数字 |
| 3 | `docs/reviews/20261113_p3p4/05_p4_ablation.md` | 矩阵 + 噪声地板 + 为什么 8 次不能用 | 表里的 INL 是**20 位定点解码输出**上测的 |
| 4 | `docs/reviews/20261113_p3p4/figures/README.md` | 每图的读数与"不能声称" | F07 的 15.5→27 bit 是**自洽**，不是验证（tau 从论文反解） |
| 5 | `docs/reviews/20261113_p3p4/07_calibration.md` | 为什么逐单元不可辨识、逐码表的上限 | 不要把逐码表当成"论文的权值校正" |
| 6 | `docs/reviews/20261113_p3p4/10_dynamic_closure.md` | A7 全动态的 14 项 ASSUMED 参数 | 动态路径 `out` 仍是 NaN——**出不了 INL/SNR** |
| 7 | `src/adi_model/{follower,dem3d,dither_port,track_aux,mask_calibration}.py` | 每个模块的"设计说明"段与失败反例 | 这些模块有门禁测试，但门禁只证明合同，不证明性能 |

---

## 五、目录地图（一句话版）

```
src/adi_model/            行为模型主包
├─ pipeline_engine.py     唯一主循环：采样→SADC→RDAC→参考→RA→ADC2→数字解码
├─ conversion.py          ConversionEngine：逐事件参考 + RA 单极点 + ADC2 宽/窄带
├─ dem3d.py follower.py   本轮新增机制（DEM 掩码 / 逐事件加载）
├─ dither_port.py track_aux.py mask_calibration.py   本轮新增（dither 电荷口 / TRACK / 校准）
├─ defaults.py            DefaultModel：同一 seed 下"一颗芯片"的装配入口
└─ contracts/             profile、拓扑、事件与单位合同（A06 的 normalization_only 在这）
tools/                    薄脚本，只调用主包，不复制公式
├─ run_p3_mechanisms.py   生成 p3_mechanisms.json
├─ run_p4_ablation.py     A0–A7 矩阵
├─ run_mismatch_sweep.py  σ→INL 扫描
├─ run_review_figures.py  F01–F12
└─ check_audit_findings.py A01–A04 的修复后回归（4/4 PASS）
docs/reviews/20261113_p3p4/   本轮全部文档 + data/ + figures/
tests/unit/               机制门禁测试（46 项与本轮相关）
```

---

## 六、我想请你重点攻击的五个点

1. **A01 的修法是否真的解决问题**：`h=(i+7·(i//9))%63` 依赖"计划周期=9"这一事实。如果片选策略换掉（比如 stride 不是 8），这条序列的覆盖会退化。我在 `Dem3State.sequence` 里加了 `plan_period` 参数与互质检查，但**这条耦合本身是不是设计缺陷**？
2. **逐码校准的上限**：它压不掉"同一码不同片组合"的项。若要接近论文 2.2 ppm，只能上"校准态"观测（逐位翻码 + 比较器判决）。**这个判断对吗？** 有没有第三条路（比如用 21 位冗余 + dither 造出码-输入解耦）？
3. **统计口径**：32 次重复把地板压到 2–3 LSB20，但 256 个电平取 max 仍有偏。**你认为 20 位 INL 的最低可接受统计方案是什么**（码密度需要多少样本/码）？
4. **动态路径**：`out` 为 NaN 是模型"拒绝在标量诊断子集里假装能重构"。要做动态配置的 INL/SNR，必须反卷积 RA 响应或引入"已知参考态"的重构。**这条边界你认可吗，还是认为应该直接做反卷积？**
5. **证据层级**：我在 5 个地方写了"不能声称"（论文指标、RTL、动态 INL、校准泛化、dither 幅度）。**有没有哪一条我其实低估了、或者反过来高估了？**

---

## 七、复现（约 6 分钟）

```bash
git clone git@github.com:defineiocc02/20bit_SAR_ADC_Behaviour_Verification.git
cd 20bit_SAR_ADC_Behaviour_Verification
git checkout merge/adi-repro        # c817e91

PYTHONPATH=src python3 tools/check_audit_findings.py                    # 4/4 PASS
PYTHONPATH=src python3 tools/run_p3_mechanisms.py --samples 4096 \
  --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_p4_ablation.py \
  --dynamic-n 8192 --static-points 256 --static-repeats 32 \
  --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_mismatch_sweep.py --points 256 --repeats 32 \
  --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_review_figures.py --points 256 --repeats 16 \
  --dynamic-n 8192 --out docs/reviews/20261113_p3p4/figures
python3 -m pytest tests/unit/test_{follower,dem3d,dither_port,track_aux,mask_calibration}.py -q
```

---

## 八、证据层级与边界（请按这个尺度评价）

| 能声称 | 不能声称 |
|:---|:---|
| 四个机制的**行为模型**存在且有门禁 | 不能声称它们已进 RTL 或已流片 |
| 热噪声预算 8.60 nV/√Hz vs 论文 9.3（0.92×） | 不能声称 20 位性能已复现（INL 6.2 vs 2.3 LSB20） |
| 逐码校准 2.3× INL、+10.5 dB SFDR | 不能声称实现了论文的逐单元权值校正 |
| 动态路径能跑通、有相位波形 | 不能声称动态配置的 INL/SNR（`out` 为 NaN） |
| 5 个种子的校准泛化趋势 | 不能声称良率（不是 30–100 个种子的分布） |

---

## 九、附：本轮数字速查

| 量 | 值 | 出处 |
|:---|:---:|:---|
| follower / 直接加载 / 试探 SAR 电荷 | 61.3 / 61.3 / 184.3 pC | F06 |
| DEM 低码单元覆盖 | 32/63 → **63/63** | F02 |
| INL：失配 / +DEM / +校准 | 24.5 / **6.2** / 10.5 LSB20 | F03、05 |
| SFDR：失配 / +DEM / +校准 | 99.3 / 100.3 / **109.7** dB | 05 |
| 热噪声 NSD（模型 / 论文） | 8.60 / 9.3 nV/√Hz | 05 |
| σ→INL 斜率（无 DEM / DEM） | 23 372 / **6 327** ppm/σ | 08 |
| TRACK 电荷降低（lookback=2） | **3.20×** | 04 |
| 校准：本芯片 / 串芯片 | 13.2 / **32.5** LSB20 | F11 |
| 动态参考误差（RA 入口） | 64.0 µV（15.5 bit） | F07 |
| 输入电流满摆幅 / 论文点 | 2.46 mA / 350 µA | F09 |
| 定点 vs 浮点 | 0.28 rms / 0.40 max LSB20 | F12 |

—— 以上。若你只有一小时，请按 §四 的 1→3→5 顺序读，然后直接回答 §六 的五个问题。
