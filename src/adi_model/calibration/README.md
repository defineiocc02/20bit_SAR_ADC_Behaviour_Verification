# `adi_model.calibration` —— 观测、系数提取与重构

| 功能 | 锚点 | 实现 |
|:---|:---|:---|
| 数字观测合同 | [`#obs`](#obs) | `contracts/specs.DigitalObservation` |
| 逐码修正表 | [`#lut`](#lut) | `mask_calibration.fit_code_correction` |
| 逐单元有效权重 | [`#elem`](#elem) | `weight_calibration.fit_unit_weights` |
| 逐单元重构 | [`#recon`](#recon) | `mask_calibration.ElementWeightCalibration.reconstruct` |
| 定点重构 | [`#fixed`](#fixed) | `contracts/fixed_point.FixedPointReconstructor` |

标注：**【原文披露】**／**【依据原文推导】**／**【本工程实现】**／**【工程假设／未确认】**。

---

## 0. 这个模块解决什么问题，做到哪一步

DAC 单元失配让实际权重偏离名义值，输出出现随码变化的误差。校准要回答三件事：
**观测什么、估什么参数、怎么反解**。

论文只说到"权重修正在片上执行、系数在片外取得"【原文披露】，
**没有公开具体算法**【工程假设／未确认：算法属于本工程选择】。

当前状态：逐码表可用并进主链；逐单元权重的**重构方程本轮刚修好**（曾有符号与
分母错误）；定点重构有独立 RTL 对拍（`recon_core` 101/101）。

---

<a id="obs"></a>
## 1. 观测合同

【本工程实现】`DigitalObservation` 字段：

| 字段 | shape | 单位 | 含义 |
|:---|:---|:---|:---|
| `slice_ids` | `[N, n_active]` | id | 本笔活动物理片 |
| `main_masks` / `sub_masks` | `[N, n_active, n_main/n_sub]` | 0/1 | **实际转换开关** |
| `signal_mask` | 同上 | 0/1 | **采样信号连接** `a`，与开关 `on` 不是一回事 |
| `known_voltage_term_v` | 同上 | V | 由参考/dither 连接得到的已知项 `t` |
| `fine_v` | `[N]` | V | 后级观测（RA 输出域） |
| `adc2_code` | `[N]` | code | 原始码 |
| `sample_ids` | `[N]` | id | 样本身份 |
| `overflow` | `[N]` | flag | 越界 |

**`a` 与 `on` 必须分开**：`on` 翻底板、改 `s`；`a` 是信号连接资格。
本项目曾把 `on` 当成 `a`，导致"gain 随 mask 变"的错误结论——见
`verification/reports/VERIFICATION_REPORT.md` §3.1。

---

<a id="lut"></a>
## 2. 逐码修正表（mask_calibration）

【本工程实现】用已知斜坡覆盖全部 512 个粗码，对每码求平均输出误差，
冻结成 512 项表。**只修正该模式下的条件均值**。

关键约束：训练模式必须与评估模式一致（见 §4）。

---

<a id="elem"></a>
## 3. 逐单元有效权重（weight_calibration）

【本工程实现】最小二乘 / SVD 解 `w[p,j]`，按 `(slice, unit)` 地址。

【已确认的负结果】在**残差域**直接拟合逐单元权值秩满但全节点残差约 991 µV，
不可辨识。原因是 `G·C·x` 与 `G·Q_DAC` 各约 96 V、相减后只剩 0.1 V，
权值在这种抵消下没有分辨力。**这不是数学错误，是参数化问题。**

【本轮修正】上一轮该结论还夹带了两个错误：开 1 mV RA 噪声、且 `beta[:, None]`
广播轴错。修正后（关噪声、`beta[:, :, None]`）真实权重预测的残差约 **31.7 µV**，
与 ADC2 量化步长/√12 一致——说明方程本身是对的，问题在参数化。

---

<a id="recon"></a>
## 4. 逐单元重构：本轮修复的方程错误

【本工程实现】训练方程与反解必须互为逆：

```
fine[n]  = Σ_{p∈A_n, j} w[p,j] · A[n,p] · (x[n] − Vbottom[n,p,j]) + o
x̂[n]    = ( fine[n] − o + Σ A·w·Vbottom ) / Σ A·w
```

其中 `A[n,p]` 是本笔活动片掩码。**两处曾是错的**：

1. **符号**：旧代码是 `− Σ w·Vbottom`，与自己的训练方程相反；
2. **分母**：旧代码用 `ones_like(term)`，把**未活动片**当成"活动且接低轨"计入。

审查给出的最小反例：`x=0.2, w=[0.1,0.05], Vbottom=[+1,−1]`，则
`fine = 0.1·(−0.8)+0.05·1.2 = −0.02`。旧实现输出 **−0.466667**，正确 **0.2**。

修复后 `tests/unit/test_element_reconstruct_equation.py` 钉死该反例
（`atol=1e-12`）。

【未覆盖】"3 片存在、2 片活动"的合成用例：`_mask_matrix` 对布局要求比手搭
`namespace` 严，需要真正的 `DigitalObservation` fixture。代码已改，用例待补。

---

<a id="fixed"></a>
## 5. 定点重构

【本工程实现】`FixedPointReconstructor`：Q30 权重 / Q32 电压 / 96 位受检累加 /
20 位 offset-binary 输出，唯一舍入是 ADC2 码仓中心的 ties-to-even，之后全是 floor。

RTL `recon_core` 与本参考 **101/101 位真**，含 `clip_low/clip_high/acc_ovf/
gain_err/adc2_ovf/result_sample_id` 与 11 拍延迟。

合同细节见 `verification/contracts/fixed_point.yaml`。

---

## 6. 逐码 LUT 与 DEM：同模式四组（本轮新增）

**旧结论已撤回**：此前说"逐码 LUT 与 DEM 原理不兼容"，是用**错配模式**
（DEM 关训练 → DEM 开测试）得出的。补齐同模式后（训练 20480 点 ≈ 40 样本/码，
静态 128 电平 × 8 次）：

| 组 | 训练 | 测试 | 校准后 INL | 未校准 |
|:---|:---|:---|---:|---:|
| A | DEM 关 | DEM 关 | **7.1** | 24.1 |
| B | DEM 关 | DEM 开 | 20.5 | 13.6 |
| C | DEM 开 | DEM 开 | **15.9** | 13.6 |
| D | DEM 开 + 逐单元 | DEM 开 | NOT_RUN | — |

- A：LUT 本身有效（3.4×）
- B：错配必然变差，只是负例
- **C：同模式训练仍略差**。DEM 让同码状态间方差变大（训练残差 82 → 105 µV），
  逐码表只修条件均值，压不住状态间波动

准确说法：**逐码 LUT 能修正该模式下的条件均值，不能消除同码不同状态的误差。**

数据：`docs/reviews/20261113_p3p4/data/lut_mode_matrix.json`。

---

## 7. 差异与缺口

| 原文/合同 | 当前 | 影响 |
|:---|:---|:---|
| 系数片外取得、片上修正 | 逐码表 + 逐单元 LS | 算法是工程选择，不宣称复现 |
| 逐单元权重可辨识 | 残差域不可辨识（991 µV） | 只能走逐码表 |
| 与 DEM 组合 | C 组仍变差 | 需换"活动 mask × 单元权值"特征 |
| RTL 侧校准加载 | 未对拍 | 加载一致性未验证 |

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_element_reconstruct_equation.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_fixed_point.py -q
python3 -m verification.run_verification --level module --only recon_core
```

---

# 附：算法原理与计算实例

## 可观测量与待估参数

**可观测量**（只来自公开接口）：`fine_v`（ADC2 输出域）、`coarse`、`main/sub_masks`、
`signal_mask`（信号连接 `a`）、`known_voltage_term_v`（参考/dither 的已知项）。

**待估参数**：逐单元有效权重 `w[p,j]`（C/Cf）或逐码修正量。

**可辨识性**：要分四种，不能混成"不可辨识"一句：

| 类别 | 含义 | 本项目证据 |
|:---|:---|:---|
| 秩亏 | 列不满秩，某些方向完全观测不到 | `rank` / `condition` 字段 |
| 病态 | 满秩但条件数大，噪声被放大 | 同上 |
| 噪声敏感 | 样本不足或噪声过大，参数方差大 | 训练样本数 |
| 训练方程错误 | 训练式与重构式不一致 | 已修（底板项符号+活动片分母） |

**撤回绝对结论**：此前写"残差域逐单元权重不可辨识、只能逐码 LUT"**过强**。
修正噪声与 β 广播后，真实权重预测的残差约 31.7 µV，与 ADC2 量化步长/√12 一致，
说明方程本身成立；是否可辨识取决于**完整观测矩阵**与训练条件，不是该参数化天然不行。
"只能逐码 LUT"这个说法一并撤回。

## 训练与重构必须互为逆

```
F[n,p,j] = A[n,p] · (x[n] − Vbottom[n,p,j])
fine[n]  = Σ w[p,j]·F[n,p,j] + o
x̂[n]    = ( fine[n] − o + Σ A·w·Vbottom ) / Σ A·w
```

其中 `A[n,p]` 是本笔活动片掩码；`a`（信号连接）与 `on`（转换开关）不是一回事。

## 计算实例（审查给出的最小反例）

取 `x = 0.2 V`，`w = [0.1, 0.05]`，`Vbottom = [+1, −1]`：

```
F = x − Vbottom = [−0.8, +1.2]
fine = 0.1·(−0.8) + 0.05·1.2 = −0.02
正确反解 x̂ = (−0.02 + (0.1·1 + 0.05·(−1))) / 0.15 = 0.03/0.15 = 0.2  ✓
```

旧实现（符号反了 + 分母含未活动片）给出 **−0.466667**。
现已修，并由 `tests/unit/test_element_reconstruct_equation.py` 钉死。

## 冻结系数怎么进重构

`FrozenCalibration` → `FixedPointReconstructor`：Q30 权重、Q32 电压、96 位受检累加、
20 位 offset-binary；唯一运行时舍入是 ADC2 码仓中心的 ties-to-even，之后全 floor。

**尚未实现**：逐 slice 的 offset/gain 校准（全局 LUT 不等于逐片校准）。
