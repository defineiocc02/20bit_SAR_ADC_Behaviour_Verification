# `ad4080_otp_cal` — AD4080 360 物理权重正弦标定、ADCToolbox 评估与 Q30 OTP 打包工具包

面向 **AD4080 自主两级交织 SAR ADC（18 片 RDAC × 20 物理电容 = 360 权重、二维 DEM、6-bit Dither、Q30 定点重构）** 的开箱即用独立 Python 工具包。

## 核心能力

1. **ADCToolbox 同源正弦前景标定（`sine_calibration.py`）**：
   - 将 `ADCToolbox` 经典的单音正弦前景标定思想（`fgCalSine` / `wcalsin` / `calibrate_weight_sine`）从传统单核 $B$ 列固定位序 SAR，扩展到 **18 片可重构 RDAC（360 物理列，含二维 DEM 与 6-bit Dither `pre/post` 掩码）+ 第二级 ADC2 细码**；
   - 不依赖 20 位超精密直流阶梯源，仅需单音相干低失真正弦波即可联合求解 360 个物理权重误差、直流失调与输入正弦幅相，内置正则化 SVD 与独立留出集（Holdout）泛化误差检验。
2. **ADCToolbox 无缝协同频谱与线性度评估（`spectrum.py`）**：
   - 若本机已安装 `adctoolbox`（`pip install adctoolbox`），自动调用 `adctoolbox.analyze_spectrum` 与 `find_coherent_frequency`；
   - 若未安装，自动切换为同口径纯 NumPy 引擎（支持 Hann 窗、相干频率计算、ENOB / SNDR / SNR / SFDR / THD / NSD 及直方图/码误差 INL/DNL 评估），**零外部编译依赖、开箱即用**。
3. **与 RTL `wsum_v21` 逐位对齐的 Q30 OTP 打包与导出（`otp_packer.py`）**：
   - 按 `rtl/sar_core/wsum_v21.sv` 严格口径（`18 × 20 = 360` 个 **40-bit Q30 定点权重**，`cfg_sel=0` 主阵 `0..15`、`cfg_sel=1` 子阵 `0..3`，`j=19` 计入 `Wm`、不计入 `Wp`）完成量化、可编程 CRC-24 校验打包、一致性解包验证，并直接导出 **JSON / OTP HEX / SystemVerilog `$readmemh` 初始化文件**。

## 快速开始

```bash
# 1. 安装（纯 NumPy 即可开箱运行；可选装 adctoolbox 联动）
cd ad4080_otp_cal
pip install -e .
# 可选：pip install -e ".[adctoolbox]"

# 2. 一键运行端到端演示（注入电容失配 -> 相干正弦采集 -> 360权重标定 -> Q30 OTP 打包 -> 校准前后频谱对比）
ad4080-otp-cal demo --samples 4096 --mismatch-sigma 1.5e-3 --out-dir ./demo_out

# 3. 校验已生成的 OTP 镜像与 CRC-24
ad4080-otp-cal verify-otp ./demo_out/otp_image.json
```

## 目录结构

```text
ad4080_otp_cal/
├── pyproject.toml
├── README.md
├── ad4080_otp_cal/
│   ├── __init__.py            # 公开 API
│   ├── architecture.py        # 18×20 名义权重阵、DEM/Dither、wsum_v21 同口径前向与重构模型
│   ├── sine_calibration.py    # 360 物理列正弦前景标定（SVD/Ridge + 留出集校验）
│   ├── spectrum.py            # ADCToolbox 联动 / 纯 NumPy 频谱与线性度分析
│   ├── otp_packer.py          # 360×40-bit Q30 量化、wsum_v21 配置流、CRC-24 打包与多格式导出
│   └── cli.py                 # 命令行入口（demo / calibrate / verify-otp）
└── tests/
    └── test_ad4080_otp_cal.py # 单元测试与端到端闭环回归
```
