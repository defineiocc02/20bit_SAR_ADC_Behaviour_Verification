# `adi_model.contracts` —— 类型、单位、格式与事件

**这一层不许依赖上层**：`contracts` 不 import `simulation`、`control`、`behavioral`，
也不 import `tools/`、`experiments/`、`tests/`（`tools/check_engineering.py` 的 C1 会查）。

## 1. 定点格式（冻结）

| 参数 | 值 | 含义 |
|:---|---:|:---|
| `W_FRAC` | 30 | 权重小数位 |
| `W_BITS` | 48 | 权重位宽（无符号，C/Cf） |
| `V_FRAC` | 32 | 电压小数位 |
| `V_BITS` | 64 | 电压位宽（有符号，V/Vfs） |
| `ACC_BITS` | 96 | 受检累加器 |
| `OUT_BITS` | 20 | 输出码 |

合法域是**半开区间** `[-2^95, +2^95)`——`+2^95` 本身越界，溢出**抛错不回绕**。

## 2. 舍入与除法

* 唯一的运行时舍入是 ADC2 码仓中心的 **ties-to-even**；
* 之后全是 **floor**。RTL 的 `/` 向零截断，负商必须减 1 修正。

这条是 Python 与 RTL 之间最容易出错的缝，`verification/reference/fixed_point.py`
把两者分开实现并互相钉死。

## 3. 事件与样本身份

`EventRecord` 带 `sample_id`、`time_s`、`order_key`；`DigitalObservation` 带
`slice_ids`、`main/sub_masks`、`signal_mask`、`known_voltage_term_v`、`fine_v`。

**`signal_mask`（信号连接 `a`）与 `main/sub_masks`（转换开关 `on`）是两个量**，
不能互用——本项目曾把 `on` 当 `a`，得出"gain 随 mask 变"的错误结论。

## 4. 已知问题

`DigitalObservation` 与 `FrozenCalibration` 在 `contracts/specs.py` 和
`calibration/weight_calibration.py` 各有**同名定义**，mypy 报 redefinition。
这是历史遗留的重名，未合并；`adi_model/__init__.py` 导出的是 calibration 那份。
