# 20bit_SAR_ADC_Behaviour_Verification

![状态](https://img.shields.io/badge/%E7%8A%B6%E6%80%81-%E7%A0%94%E7%A9%B6%E5%8E%9F%E5%9E%8B-0969da?style=flat-square) ![类型](https://img.shields.io/badge/%E7%B1%BB%E5%9E%8B-%E8%A1%8C%E4%B8%BA%E7%BA%A7%E5%BB%BA%E6%A8%A1-6e7781?style=flat-square) ![技术栈](https://img.shields.io/badge/%E6%8A%80%E6%9C%AF%E6%A0%88-Python-6e7781?style=flat-square) ![许可](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF-BSD--3--Clause-2ea043?style=flat-square)

> 用 Python 逐相位建模 20 位两级残差 SAR ADC，并用定点 RTL 与 CI 回归交叉验证。

## 这是什么

本仓库是 ISSCC 2024 Session 9.8（Bodnar 等，20 b / 40 MS/s 精度型 SAR）的**行为级**研究模型：把采样网络、两级 SAR 判决、残差 DAC、共享残差放大器、后端 ADC 与数字重构放在同一个逐样本相位机里计算，检查电荷、时序、噪声与校准是否自洽。

模型不追求器件级精度，重点是**可审计**：93 项参数逐项带来源等级，17 项机制逐条映射到公开论文/专利与代码落点，结论由测试与验收门禁约束，而不是写在注释里。公开资料未披露的电容分配、部分相位时间与 DEM 交换方式均保留为显式假设。

## 当前状态

| 项 | 值 |
|---|---|
| 阶段 | 研究原型：行为级与定点 RTL 验证完成，未流片 |
| 最近验证 | 2026-10-19，main `23b73d6`，CI [run 39199055538](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/39199055538) 6 类共 9 个作业全绿；同日仅有文档改动 |
| 关键指标 | ENOB 20.58 bit、SNDR 125.6 dB、SFDR 155.5 dB（`s1`：无失配、非理想关闭）；DEM 开/关 SNDR 93.54/93.55 dB（原电容 20.5 pF、无 KTC、默认 100 ppm 失配）；MC 最差 SNDR/SFDR 82.0/85.1 dB（`mc_pdk_off`：60 颗、PDK σ=1117 ppm、DEM 关闭）。原始值见 [`tools/results/results.json`](tools/results/results.json) |
| 结论边界 | 仅行为级计算，无 PDK、版图或硅片数据；RA 噪声由 94.6 dB DR 锚点反推（`[拟合]`），不是独立预测；20 位整数输出不等于实测 20 ENOB 或完整码密度 INL/DNL；PR #4 的校准/RTL 时序改进尚未合入 main，不得当作默认分支能力引用 |

## 快速开始

### 依赖

- Python 3.10–3.13
- 运行依赖：NumPy ≥ 1.24、SciPy ≥ 1.10、Matplotlib ≥ 3.7
- 开发/验证依赖：`pytest`、`pytest-cov`、`ruff 0.6.9`、`mypy`、`hypothesis`
- RTL 回归另需 Verilator 与 Tcl（CI 在 Ubuntu 24.04 上安装）

### 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'
adi-run-all --results-dir ./output/verification
adi-make-report --results-dir ./output/verification
```

### 验证

```bash
pytest -m "not slow" -q
adi-run-all --results-dir /tmp/run1 && adi-run-all --results-dir /tmp/run2
cmp /tmp/run1/results.json /tmp/run2/results.json
python tools/run_open_rtl.py   # 需 Verilator
```

通过标准：命令退出码为 0；`results.json` 的顶层验收记录全部为 `PASS`；两次全量跑批的 `results.json` 逐字节一致；RTL 回归无失败。

## 目录结构

```
src/       adi_model 行为模型，48 个模块（配置、物理链路、数字核、验收）
rtl/       可综合 SystemVerilog：双 SAR、共享 3-bit Flash、校准核
sim/       Verilator/VCS 测试台、向量与独立 Python 参照实现
synth/     DC 综合脚本与无 license 语法回归
tests/     unit / integration / regression / audit 回归套件
tools/     全量实验、报告、RTL 向量导出与变异测试
docs/      ADR、适用范围、RTL 契约、发布证据与历史审计
```

## 设计与用法

### 信号链与相位流水

一个采样周期被写成显式相位机：采集 → 预跟踪 → 9 位粗转换 → 残差建立 → 有限带宽/压摆放大 → ADC2 细转换 → 切片释放。每一相位都绑定物理约束与验收项，跨周期因果由 `conv[n] = acq[n-1]` 保证。设计细节见 [`docs/model_scope.md`](docs/model_scope.md) 与 [`docs/behavioral-closure.md`](docs/behavioral-closure.md)。

### 参数来源是运行时值

[`src/adi_model/provenance.py`](src/adi_model/provenance.py) 的 `PARAM_GRADES` 为每个 `Config` 字段登记等级；新增字段未登记会让测试失败。当前 93 项构成为：`[披露]` 9、`[派生]` 8、`[拟合]` 3、`[假设]` 65、`[原创扩展]` 8。`[假设]` 参数算出的结果不得表述为披露值。

### 三套独立校验

- `pipeline.py`（逐相位状态机）与 `sim_split.py`（向量化）在非理想因素关闭时逐位等价；
- `charge_ref.py` 用独立节点方程复算电荷，不复用主链路的闭式解；
- 数字核与 RTL 共用同一套定点契约，`recon_rtl_mirror.py` 复刻 RTL 位宽后与黄金模型逐样本对拍。

### 验收与证据

[`src/adi_model/acceptance.py`](src/adi_model/acceptance.py) 设定 `MIN_RECORDS = 52` 并维护必需判据集；未闭合项用 `xfail(strict=True)` 跟踪，修好后 XPASS 即失败。机制与来源的机器可读账本见 [`mechanism_inventory.json`](mechanism_inventory.json) 与 [`sources_manifest.json`](sources_manifest.json)。17 项机制、参数分级与关键技术的完整说明见 [`docs/key_technologies.md`](docs/key_technologies.md)、[`docs/engineering_vs_patents_papers.md`](docs/engineering_vs_patents_papers.md) 与 [`docs/adr/`](docs/adr/)。

### 定点 RTL

`rtl/` 交付与行为模型同口径的可综合实现（Q30/Q32、96 位受检累加、20 位 offset-binary 输出）。算术契约与接口说明在 [`docs/rtl/RTL_ARITHMETIC_CONTRACT.md`](docs/rtl/RTL_ARITHMETIC_CONTRACT.md)、[`docs/rtl/P1_INTERFACE.md`](docs/rtl/P1_INTERFACE.md)、[`docs/rtl/P2_INTERFACE.md`](docs/rtl/P2_INTERFACE.md)。

## 已知限制

- 无器件、版图、PDK 或硅片测量；失配、面积、功耗与良率均为敏感性假设，不构成硅级预测。
- RA 噪声由所选 DR 锚点反推（`[拟合]`）；`s1` 的 125.6 dB 是无失配、非理想关闭条件下的链路自洽结果，不是芯片实测性能。
- 参考负载在名义轨处线性化；量化器/RA/孔径相位与逐位试探次序为假设，未做晶体管级时序收敛或亚稳态概率预测。
- 20 位输出是受检整数重构，不等价于完整芯片码密度 INL/DNL 证明；`charge_ref.py` 缺少域守卫，极端输入仍需单独评审。
- KTC 观测支路为作者原创扩展，默认关闭，不属于 ISSCC 2024 或所引专利披露的机制，不得归属 Analog Devices。

## 相关仓库

| 仓库 | 关系 | 说明 |
|---|---|---|
| [12bit_CAAZ_SAR_ADC](https://github.com/defineiocc02/12bit_CAAZ_SAR_ADC) | 同作者，12 位版本 | 校准 SAR 的电路设计与验证，可与本仓库的 20 位行为模型对照 |
| [Behavioral-modeling-of-12bit-calibrated-sar-adc](https://github.com/defineiocc02/Behavioral-modeling-of-12bit-calibrated-sar-adc) | 同作者，方法同源 | Python + Verilog-A + 可综合 RTL 的 12 位行为级建模 |
| [Digital_process.srcs](https://github.com/defineiocc02/Digital_process.srcs) | 同作者，RTL 参考 | SAR ADC 数字处理与校准验证源码集 |

## 许可

代码采用 **BSD-3-Clause**，见 [`LICENSE`](LICENSE)。`LICENSE` 前 28 行为标准 BSD-3-Clause 正文，附加的范围与第三方权利声明见 [`NOTICE`](NOTICE)。

本仓库是独立学术模型，与 Analog Devices, Inc. 无从属、无背书、无授权关系；仓库不授予任何专利许可，也不得用于量产、安全关键或良率决策。
