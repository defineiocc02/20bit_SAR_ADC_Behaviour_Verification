# 20 位 / 40 MS/s 两步式 SAR ADC：按专利原图对照的行为级复现

[English](README_EN.md) · [当前状态](docs/STATUS.md) · [机制专题](docs/mechanisms/README.md) · [专利技术对照](docs/mechanisms/README.md) · [原文对照表](docs/current_digital_source_map.md) · [论文与讲稿](references/README.md) · [验收报告](docs/ACCEPTANCE_20261112.md) · [Python–RTL 一致性验证](verification/README.md)

**这个仓库要回答一个问题**：ADI ISSCC 2024 §9.8（20 b、40 MS/s、40 nm、两步式 SAR）的每个机制，
在我们自己的行为模型里**到底实现了没有、实现到什么程度、证据在哪**。

答案不是"做过哪些检查"，而是**沿专利原图逐个模块对上代码**：
每项技术给原图编号、器件/节点、对应函数、一笔真实样本、同条件开/关，以及**没实现的部分**。

---

## 1. 当前结论（一句话版）

| 机制 | 结论 | 关键数 | 专题 |
|:---|:---|:---|:---|
| RDAC follower | 已实现，**只走向已决码** | 与"一次加载"最终电荷相同；传统试探 SAR 的往返电荷是它的 **3.01×** | [follower](docs/reviews/20261113_p3p4/01_rdac_follower.md) |
| 三维 DEM | 两维掩码 + 一维调度已实现；**保留列/跨片分配未实现** | INL **24.5 → 6.2 LSB20**（3.9×），失配斜率 0.27×；代价 NSD **+29%** | [DEM](docs/mechanisms/dem_fig11.md) |
| 分路 dither | **独立电荷口已进主链**（v2）；**物理电容网络未实现** | INL **24.5 → 20.5**、DNL 分箱 **16.5 → 5.9 LSB20**；代价 SNDR **−0.53 dB**、范围 **−0.188 V** | [dither](docs/mechanisms/dither_fig19.md) |
| TRACK 预置 | 电荷记账 + 时间因果已实现；AUX 未接 | 输入电荷 **3.20×** 降低（回看 2 笔，因果） | [TRACK](docs/reviews/20261113_p3p4/04_track_aux.md) |
| 动态参考/RA/ADC2 | 能跑通，**出不了 INL/SNR** | 参考在 RA 入口 64.0 µV（15.5 bit），RA 内补 11.5 bit | [A7](docs/reviews/20261113_p3p4/10_dynamic_closure.md) |
| 权值校准 | **逐码表**可用；逐单元权值未闭环 | 本芯片 13.2、串芯片 **32.5** LSB20（比不校准差 58%） | [校准](docs/reviews/20261113_p3p4/07_calibration.md) |

**最重要的一句**：热噪声预算 8.60 nV/√Hz vs 论文 9.3（0.92×）已经对上；
**线性度还差 2.7×**（6.2 vs 2.3 LSB20），差在"逐单元权值/校准"，不在噪声。

## 2. 原论文的总体结构（先有这张图，再谈实现）

论文 §9.8 的链路：**输入 → 8 片采样（另 8 片转换、2 片空闲）→ 3b Flash + 6b SAR 的小量化器（SADC）
→ 大盘 RDAC 跟随已决码 → 共享 RA 放大余差 → 12b ADC2 → 20 b 输出**。

三个与常规 SAR 不同的地方，也是本仓库的三条主线：

1. **量化器与 RDAC 分离**：量化器只判决，RDAC 不试探、只跟随 → 参考电流从"试探往返"里省出来。
2. **模拟域交织（ADI）**：18 片池、每笔 8 片，采集与转换并行 → 采集窗口拉到整个周期。
3. **数字侧三重线性化**：DEM（重排单元）、dither（打散量化）、权值校正（修确定性误差）。

## 3. 专利技术对照总表

> 专利原件不在仓库内；**图号与器件编号凡标"待核对"者均来自审查转述**，未逐字核对原件。
> 状态：`已实现` = 有代码 + 有门禁；`等效` = 保留因果位置但换了实现；`部分` = 有缺口；`未实现` = 明确空缺。

| 技术 | 对照原图 | 当前实现 | 状态 | 缺什么 |
|:---|:---|:---|:---:|:---|
| 分离量化器 + 切片 RDAC | US10516408B2 Fig.7、Fig.16–17（待核对） | `follower.py`、`pipeline_engine` | 等效 | 逐事件电荷已建；真实建立/边沿未建 |
| 分路 dither | US10505561B2 **Fig.19**（待核对） | `dither.py`、`dither_port.py` | 等效 | 物理电容/开关网络、环内闭环 |
| DEM 与跨段映射 | US10511316B2 **Fig.11、Fig.19–21**（待核对） | `dem3d.py` | 部分 | 保留列、跨片分配规则 |
| 动态参考 | US10826519B1（实施例图待定位） | `conversion.py`、`pipeline_engine` | 部分 | 出不了 INL/SNR；参数敏感性未扫 |
| TRACK / AUX | US10707889、US10541702B1（待核对） | `track_aux.py` | 部分 | AUX 支路、RC 建立 |
| 普通 AZ | 论文/PPT 时序图 | `behavioral/ra_az.py` | 已实现 | 漂移/保持扫描未做 |

## 4. 各项技术的原图与实现摘要

### 4.1 分路 dither（Fig.19）→ [六段全文](docs/mechanisms/dither_fig19.md)

字分两段：**下段 L 进模拟**（`x−L` 送量化器）、**上段 U 进码**（RDAC 取 `c−U`），数字再加回 `T=L+UΔ`。
代码：`Fig19DitherProgram.from_words` → `lower_v/upper_code/total_v`；注入端 `DitherPort`（等效电荷口）。
真实样本见专题 §④（word 21：`L=2.714 LSB`、粗码 129→126）。
**缺口**：七片示例 vs 八片活动组、物理网络、环内闭环。

### 4.2 DEM（Fig.11 / Fig.19–21）→ [六段全文](docs/mechanisms/dem_fig11.md)

主阵列 15×2C + 2×1C、B4 在 16 个 1C 里选 8、子阵列二进制经 β=1/17 耦合。
横向按**物理 ID** 旋转 15 列，纵向按**组内位置**选 8/16——这个不对称是刻意的：
纵向若按 ID 取模，18 片池会选出 9~10 个单元，B4 权重当场变。
**缺口**：保留列、跨片分配规则、子阵列二进制不可 DEM。

### 4.3 其余四项

* **follower**：7 个已决事件逐次加载；前缀截断不变性 28 672 检查 / 0 违约。
* **TRACK**：两相流水线下最小回看 2 笔；`lookback=1` 会被时间判据直接否掉。
* **动态参考**：参考误差在 RA 入口 15.5 bit、孔径处 27 bit（`tau_ref` 由论文 65% 反解，属自洽）。
* **校准**：逐码表 `fit_code_correction`；逐单元 `fit_element_weights` 秩满但全节点残差 991 µV，未闭环。

## 5. 同条件开/关（可重跑，数据在 `docs/reviews/20261113_p3p4/data/`）

| 量 | 关 | 开 | 条件 |
|:---|:---:|:---:|:---|
| INL（σ=0.1%） | 24.5 LSB20 | **6.2**（DEM） | 256 电平 × 32 次，63+8 池 |
| INL / DNL 分箱 | 24.5 / 16.5 LSB20 | **20.5 / 5.9**（dither） | 同上；dither 另扣 0.188 V 输入范围 |
| SNDR（dither 代价） | 88.06 dB | **87.53 dB** | 同上；SFDR 同时 99.3 → 97.9 dB |
| SFDR | 99.3 dB | **109.7**（逐码校准） | 同上，1 MHz 相干 |
| 热噪声 NSD | — | **8.60 nV/√Hz**（论文 9.3） | A2−A1 功率差 |
| 参考电荷（同码） | 一次加载 61.3 pC | follower 61.3 / 试探 **184.3** | F06 |
| 输入电荷 | 39.05 pC | **12.2 pC**（TRACK） | 1 MHz 正弦 |

dither 这行的读法：**它对 DNL 最有效（2.8×），INL 只降 16%，SFDR 反而差 1.3 dB**。
dither 把"用哪个码"随机化，抹平码间台阶；压不平整条传输曲线的弯曲——
那是 DEM 和校准的活。

图：`docs/reviews/20261113_p3p4/figures/`（F01–F13，每张带配置、seed、SHA、重绘命令）。
本页所有数字与图都来自同一份源码（`figures.json` 的 `source_sha` 与矩阵 JSON 一致）。

## 6. 未完成（不掩盖）

| 项 | 状态 | 影响 |
|:---|:---|:---|
| ~~dither 环内闭环（A6）~~ | **已接**（v2 独立电荷口，2026-11-12） | 见 §5；仍不含物理 dither 电容网络 |
| 逐单元权值闭环 | 未闭环（全节点残差 991 µV） | 卡在 6.2 LSB20，进不到 2.3 |
| 动态路径数字输出 | `out=NaN` | A7 只能看波形，不能出 INL/SNR |
| AUX 支路 | 未接 | 输入电流只能给上界 |
| RTL 逐笔 scoreboard | 未做 | 无 RTL 级结论 |
| ~~工程结构迁移~~ | **已完成**（43 模块进 7 子包，指纹逐位一致） | 见 `docs/restructure/migration_table.md` §8 |
| `execute_pipeline` 拆分 | 未做（仍约 760 行） | 单文件仍偏长，是下一步可读性工作 |
| `metrics` 两套口径合并 | 未做 | `metrics` 与 `metrics_lsb20` 仍并存 |

## 7. 运行与调试入口

```bash
# 1) 一次名义运行（默认 4096 笔）
PYTHONPATH=src python -m adi_model simulate --samples 4096 --output /tmp/adi-demo

# 2) 机制门禁（follower / DEM / dither / TRACK / 校准）
PYTHONPATH=src python -m pytest tests/unit/test_{follower,dem3d,dither_port,dither_charge_port,track_aux,mask_calibration}.py -q

# 3) 外部审查的修复回归（应 4/4 PASS）
PYTHONPATH=src python3 tools/check_audit_findings.py

# 4) 复算矩阵与图（两处 repeats 必须一致，否则图与矩阵数字对不上）
PYTHONPATH=src python3 tools/run_p4_ablation.py --dynamic-n 8192 --static-points 256 \
  --static-repeats 32 --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_review_figures.py --points 256 --repeats 32 \
  --dynamic-n 8192 --out docs/reviews/20261113_p3p4/figures

# 5) 一笔样本的完整链路
PYTHONPATH=src python3 tools/trace_one_sample.py --out docs/mechanisms/data
```

**读代码顺序**：`docs/mechanisms/dither_fig19.md` 或 `dem_fig11.md`（先看原图与映射）
→ `src/adi_model/simulation/pipeline_engine.py`（唯一主链）→ `src/adi_model/defaults.py`（一颗芯片怎么装配）
→ `tools/run_p4_ablation.py`（矩阵怎么跑）。

## 8. 证据层级

本仓库只到 **L1 行为级（模拟浮点 + 20 位定点解码）** 与 **L2 数字 RTL 编译**；
没有后仿、没有 PEX、没有硅测。任何"论文指标已复现"的说法都不成立——
最大差距是 INL 6.2 vs 2.3 LSB20。
