# 外部审查处置（A01–A07）：改了什么、哪些结论被撤回

> 审查对象：本仓库 `merge/adi-repro` @ `20ec656`。
> 审查方给了 4 个可复算的最小反例（`minimal_counterexamples.py`）+ 7 条缺陷。
> 本文逐条给：**核实结果 / 改动 / 撤回了哪句结论 / 现在的证据**。
> 其中 A01、A02、A03、A05、A07 是**我的代码/结论**的问题，A04、A06 是既有代码与我接线共同的问题。

---

## 1. 逐条处置

| 编号 | 核实 | 处置 | 撤回的结论 |
|:---|:---:|:---|:---|
| A01 DEM 相位覆盖 | ✅ 属实 | 相位空间 15→64、序列改为按计划周期铺满、`usage_uniformity` 改为逐码 × 逐(片,单元) + 卡方 | "三个维度使用极差全 0"（那是只测满码的假通过） |
| A02 电荷→LSB 量纲 | ✅ 属实 | 同时给 `max_spread_c` / `max_spread_v` / `max_spread_lsb20`，后两者用 `c_norm_f` 与 `lsb20_v` | 旧 `max_spread_lsb20` 数值（单位其实是法拉） |
| A03 TRACK 因果 | ✅ 属实 | 删"值相等即偷看"判据；改为 `source_ready_s < precharge_s`；`values_v` 改由数字码本重建；最小回看 2 笔 | **TRACK 电荷降 6.36×**（那是 lookback=1，非因果）→ 现 3.20× |
| A04 普通 AZ | ✅ 属实 | droop 改用**保持时间**（不是绝对时间）、`error_v`→`residual_v` 并统一为"加到输入"、噪声按 sample_id 键控以支持分块 | AZ 的 droop 数值（旧接线传 `n/fs`，等于凭空造一条随记录增长的漂移） |
| A05 输入电流口径 | ✅ 属实 | `delta_v_peak` 与 `delta_v_pp` 分开；删"350 µA ↔ 0.907 MHz"反解 | **"350 µA_pp 对应 0.907 MHz"整段结论**——论文横轴是**直流差分输入**，不是频率 |
| A06 profile 身份 | ✅ 属实 | `c_unit_f` 描述 1 pF→1 fF；新增 `normalization_only`，Cf=0 的 profile 不得喂物理 RA | "ENG_10M 是 1 pF 单位电容" |
| A07 dither 残余 | ✅ 属实 | 非法 shifted 码**不再夹紧**参与统计；只对合法样本求残差并单独计数 | 旧 `max_abs_residual_*`（混入了夹紧后的非法样本） |

## 2. A01 的关键细节（不是"改个数字"）

旧实现：`Dem3State.horizontal ∈ [0,16)`，而主阵列有 63 列 → `start = (h+pid) % 63 ≤ 32`，
低码下固定物理片只访问前 33 列；再叠加片选计划周期 9 与 `h = index % 15` 的混叠，
实测只覆盖 15 个单元。审查的最小反例（63 列、码 8、固定片）完全复现。

现在：相位空间 6 bit；序列为 `h = (index + 7*(index // 9)) % columns`。
沿子序列（`index = r + 9k`）变成 `h = r + 16k`，`gcd(16,63)=1`，因此一个周期
（63×9 = 567 笔）内每片走遍全部 63 列。纵向 `(11*index) % 16`，沿子序列为 `3k`，与 16 互质。

实测（`p3_mechanisms.json` → `dem.usage`）：

| 码 | 主单元覆盖 | chi2/dof | 判定 |
|---:|:---:|---:|:---:|
| 8（1 个主单元） | 63/63 | ≈0.01 | 均匀 |
| 255（31 个） | 63/63 | ≈0.04 | 均匀 |
| 511（63 个） | 63/63 | 0 | 均匀（退化） |

**判据也改了**：等频的正确统计量是卡方，不是极差。极差在 63 个桶上天然有
`~4·sqrt(mean)` 的涨落，把涨落当偏置会误报；确定性轮转的 chi2 接近 0，是最均匀的情形。

## 3. A05：必须撤回的一段

旧文档写："20.5 pF、±3 V、40 MS/s 下，350 µA_pp 对应约 0.91 MHz 的相邻样本差分"。
这句有两处错：

1. `2A·sin(pi f/fs)` 是相邻差的**峰值**，峰峰值是它的 2 倍——所以即便按正弦口径，
   对应频率也要再折半；
2. 更根本：论文 Fig.9.8.5 的输入电流横轴是**直流差分输入电压**，不是正弦频率。
   用正弦差分去反解频率这件事本身不成立。

现在只保留可辩护的两条：`charge = C_total × ΔV`（直流电平切换的吞吐上界），
以及正弦相邻差的峰值/峰峰值分列，**不再声明与 350 µA 的对应关系**。

## 4. A03：TRACK 的因果回看必须是 2 笔

两相流水线里，第 n−1 笔的判决在本笔孔径时刻才完成，没有预置窗口。
所以 `lookback=1` 的"预测"在物理上不可能，旧 6.36× 是**非因果**的结果。
改成 `lookback=2` 后为 **3.20×**（同一 1 MHz、同一池），并且 `late_sources` 审计
会直接把 `lookback=1` 判为不因果（已有负例测试）。

## 5. 仍然没解决的（不掩盖）

| 项 | 状态 | 说明 |
|:---|:---|:---|
| A04 的 AZ 噪声代价 | 未量化 | 接口与时间基修正了，但没有跑"AZ 开/关 × 噪声 PSD"的对照 |
| A07 两套 dither 实现并存 | 未收敛 | 电荷口（新）与 Fig19 码偏移（旧）仍在，缺一个"来源说明 + 适配层" |
| 图 F01–F12 | 未产出 | 审查要求的 12 张图本轮没有做，现有只是数据表 |
| 逐单元权值校准 | 不可辨识 | 见 07；需要审查建议的"校准态"观测（逐位翻码 + 比较器判决） |

## 6. 复算命令

```bash
PYTHONPATH=src python3 tools/check_audit_findings.py  # 4 项修复后回归，应 4/4 PASS
python3 /path/to/minimal_counterexamples.py          # 审查方的最小反例（复算旧表达式）
PYTHONPATH=src python3 tools/run_p3_mechanisms.py --samples 4096 --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_p4_ablation.py --dynamic-n 8192 --static-points 256 --static-repeats 8 \
  --out docs/reviews/20261113_p3p4/data
PYTHONPATH=src python3 tools/run_mismatch_sweep.py --out docs/reviews/20261113_p3p4/data
python3 -m pytest tests/unit/test_dem3d.py tests/unit/test_track_aux.py tests/unit/test_ra_az.py \
  tests/unit/test_dither_port.py -q
```
