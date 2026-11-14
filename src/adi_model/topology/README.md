# `adi_model.topology` —— 电容网络、码本与电荷算子

| 功能 | 锚点 | 实现 |
|:---|:---|:---|
| 电容与电荷网络 | [`#caps`](#caps) | `capacitors.py`、`dac_arch.py` |
| 物理失配与布局映射 | [`#mismatch`](#mismatch) | `dac_arch.SplitChip` |
| 名义码本 | [`#codebook`](#codebook) | `dac_arch.NominalCodebook` |

标注：**【原文披露】**／**【依据原文推导】**／**【本工程实现】**／**【工程假设／未确认】**。

---

<a id="caps"></a>
## 1. 电容与电荷网络

【原文披露】US10511316B2 FIG.19（Sheet 17/29）：主阵列 15×2C + B4(1C) + S4(1C)，
桥接 C，跨桥后子阵列 8C/4C/2C/1C。

【依据原文推导】`beta = Cb/(Cb+ΣCs+Cp_sub) = 1/16`；
`Cnorm/group = 8×(32 + 15/16) = 263.5C`；`LSB = 1/16 C`，`512×LSB = 32C = main`（整除）。
端点比值 `drive(511)/cnorm = 1.000000`，与 mid-rise 码本自洽。

【本工程实现】`capacitors.MAIN_WEIGHTS`（17 项）、`SUB_WEIGHTS`（**当前 5 项，应为 4 项**）、
`BRIDGE_CAP_UNITS`。**已知缺陷**：`SUB_WEIGHTS` 末位那个 1C 是桥接电容被误当成
子阵列单元，导致 β 算成 1/17、端点差 0.33%。见 `control/README.md` §1.4。

**四种"电容"必须分开**（混用是本项目历史上出过错的地方）：

| 名称 | 含义 | 代码 |
|:---|:---|:---|
| 物理电容和 | 版图上所有电容之和 | `C_main/C_sub/C_bridge` |
| 信号归一化电容 `Cnorm` | 全 Signal 连接时的等效 | `split_capacitance_coefficients["c_signal"]` |
| 采样噪声等效 `Ceq` | `Cnorm²/Σ(Cm+β²Cs)` | `_noise_cap_f` |
| 反馈电容 `Cf` | RA 反馈 | `require_feedback()` |

【工程假设／未确认】20.5 pF 映射到名义 `Cnorm`，等级
`DISCLOSED_ANCHOR_WITH_NODE_ASSUMPTION`（见 `defaults.py` 推断表）——**不是提取值**。
单侧/差分口径未定。

---

<a id="mismatch"></a>
## 2. 失配与布局映射

【本工程实现】`SplitChip`：`C_unit·(1+ε)`，`ε ~ N(0, σ)`，σ 由 `unit_mismatch_std`
与 `bridge_mismatch_std` 给（默认 1e-3）。失配**绑定在物理片索引上**，不随 slot 变。

【工程假设／未确认】σ 是相对标准差，不是 PDK 保证值；没有空间相关、边缘效应、
桥邻域效应。40 nm 的 `σS/σR/σT` 划分本轮**未核对**（Webb 资料未读）。

---

<a id="codebook"></a>
## 3. 名义码本

【本工程实现】`NominalCodebook(code_min, code_max, voltage)`，512 点。
当前用 `drive = c//n_sub + β·(c%n_sub)`（63+8 解码）；换成 17/5 后必须改成
图上的 `thermal/b4/sub` 三段解码——这一步**未做**，是主链统一的阻塞点。
