# 用 ADCToolbox 估计系统 OFFSET 和 GAIN

**主机估计系数，现有 DSP 在片上逐样本修正。** 要校准的是传感器、驱动和 ADC 组成的整个测量通道。输入为转换 IP 已经重构好的 20 位结果。ADCToolbox 的 `fit_sine_4param` 从实测序列拟合基波峰值和直流分量。把它们与独立标定的输入幅度、直流中心比较，可求绝对增益和零偏。也可按 AD4080 手册用两个已知直流电平求解。 [ADCToolbox 源码](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L49) · [手册第 71 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) · [DSP 修正 RTL](../../ad4080_dsp/rtl/sys_error_corr.sv)

## 快速运行

在仓库根执行。依赖仅装在独立主机环境，固定为此次核实的 ADCToolbox 0.9.1 源码提交。

```sh
python3.12 -m venv /tmp/ad4080-cal
. /tmp/ad4080-cal/bin/activate
cal=subsys/ad4080_peripherals/calibration
python -m pip install -r "$cal/requirements.txt"
python "$cal/system_cal.py" dc --csv "$cal/example_dc.csv" --vref-v 3 --code-format signed20 --k-gain 1 --shift 15
```

这份可运行 CSV 用 ±1.5 V 两电平和 6 笔有符号码演示。工程映射的输出为 `GAIN=0x15D`、`OFFSET=-80`，12 位寄存器字段为 `0xFB0`。按地址列出的字节为 `0x25=B0, 0x26=0F, 0x27=5D, 0x28=01`。对每个寄存器，依据配置 SPI 的地址方向在同一帧传齐两个字节，写后整字读回。第 38 页图 58 的降序从高地址发高字节，图 59 的升序从低地址发低字节，正文的起始地址叙述与图相反。OFFSET 地址采用第 74 页及第 92 页表 60，后者与第 71 页正文中的地址冲突。[样本 CSV](example_dc.csv) · [图 58/59](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=38) · [表 60](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=92) · [表 61](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93)

## 采什么，怎么算

先将 `OFFSET=0`、`GAIN=0x200`，关闭滤波或锁定所测通路的带宽，等输入稳定后采集。未滤波通路在校正块之后，采数时仍须核对用户寄存器为中性。用 ADC 引脚处的已知差分电压与实测 VREF。`LSB = 2 × VREF / 2²⁰`，VREF=3 V 时约为 5.722 µV。ADC 原生两补码可按整数 `signed20` 或线码 `wire_twos20` 导入。`offset_binary` 用于主机转码数据。SPI FIFO 样本按 `spi24_sign_extended` 导入时，高四位须与数据符号一致。[表 7](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=20) · [图 51/52](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=33) · [SPI 符号扩展](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=52)

- **两点直流。** CSV 列为 `vin_diff_v,code`，各电平重复采样。令理想有符号码为 `t=Vin/LSB`，实读为 `x`，两组均值给出 `a=(xH−xL)/(tH−tL)` 与 `b=xL−a·tL`。连续值修正为 `(x−b)/a`。这部分按手册公式实现，定点应用直接复用原 DSP。
- **已知正弦。** CSV 的 `code` 列按均匀采样顺序排列，API 使用 `fit_sine_4param(codes, frequency_estimate=Fin/Fs, max_iterations=0)`。第 0 次迭代对余弦、正弦、常数三列做最小二乘，取返回的 `amplitude` 和 `dc_offset`。比例 `a=Ameas/(Aref/LSB)`，偏置 `b=DCmeas−a·DCref/LSB`。输入参考必须是**差分峰值**和直流中心。只有未知幅度的一串 ADC 输出时，仪器幅度和通道增益无法分别辨识。正弦幅度法假定通道为正极性，可用两个直流电平确认。[算法与返回值](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L60)

例如以下命令测 40 MS/s 下的一段已标定正弦。要求 `sine.csv` 实际对应 1.5 V 差分峰值、0.125 V 差分直流和给定频率。省略寄存器映射时，JSON 仅给连续值估计。`--max-rmse-codes` 可按台架噪声预算限制拟合残差。

```sh
python "$cal/system_cal.py" sine --csv sine.csv --vref-v 3 --fs-hz 40000000 \
  --fin-hz 1240234.375 --reference-peak-v 1.5 --reference-dc-v 0.125
```

## 怎样得到可写的寄存器码

**本项目 RTL。** `--k-gain/--shift` 给出工程倍率 `m(k)=1+(k−512)·K_GAIN/2^SHIFT`，选最接近 `1/a` 的增益码。OFFSET 位于乘法前，取 `⌊−b+0.5⌋`，与实测表路径用同一规则。RTL 先以 21 位补码求 `t=x+OFFSET`，再计算 `t+⌊t·(GAIN−512)·K_GAIN/2^SHIFT+0.5⌋`，最后饱和到 20 位。OFFSET 用 12 位补码，GAIN 用 10 位直码。`K_GAIN=1, SHIFT=15` 是可复现的工程绑定。 [位真软件参考](../../ad4080_dsp/model/ad4080_cal_model.py) · [校准程序](system_cal.py)

**实际器件。** 手册图 96 在滤波或旁路选择前画出先加 OFFSET、再乘 GAIN。第 71 页 `y=mx+c` 是外部仿射形式，乘前偏置为 `d` 时，外部截距 `c=m·d`。`--gain-table` 用实测倍率选择增益档，以名义输入码步给出 `OFFSET≈−b` 的初值。手册列出单位码与端点，精确中间码映射、内部位宽、逐样本舍入和 `GAIN=0x000` 的行为仍缺失。最终 OFFSET 由所选 GAIN 档下的灵敏度测量确定。[图 96](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=64) · [第 71 页公式](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) · [GAIN 表 61](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93)

JSON 的 `estimate.correction_offset_codes` 是连续仿射式中的外部截距 `c`。`registers.offset_code` 是乘前加法节点的整数码。

1. 在同一颗器件、OFFSET=0 下，每个已回读的 GAIN 测两个已知直流电平，各取多笔，必须含单位码 `0x200`。保存 `gain_code,vin_diff_v,code` 到 `scan.csv`。每档用自己的两点斜率除以单位档斜率，得到可识别的实际代码增益。
2. 用这张表对中性设置下的训练记录选择覆盖目标的已测档。先以 `OFFSET=0` 写入并读回所选 GAIN，供下一步量测。若表步距太宽，在目标附近增测相邻码。
3. 固定所选 GAIN 和同一个已知直流，测 OFFSET=0 与一个非零试调码。CSV 列为 `gain_code,offset_lsb,vin_diff_v,code`，其中 `offset_lsb` 是有符号值。由输出均值差除以寄存器差得到该档的实际偏移步长，反算最终 OFFSET。最后在新的正负电平和零点复测。

```sh
python "$cal/characterize_registers.py" gain-table --csv scan.csv --vref-v 3 --output gain-map.csv
python "$cal/system_cal.py" dc --csv "$cal/example_dc.csv" --vref-v 3 --gain-table gain-map.csv
python "$cal/characterize_registers.py" offset-trim --csv trim.csv --vref-v 3
```

上例第二步演示参数格式，实物运行时换成**该器件在中性寄存器下的实测记录**。变更 GAIN 后按相应时序重验 CNV 到首位的设置。表 2 的 `tMSB` 上限在增益校正旁路时为 18 ns，开启时为 22.4 ns。手册的 40 MS/s 单 lane 示例首位相位分别为 46.25 ns 和 51.25 ns，时钟计数转成寄存器字段须查表 20。[实测档位处理代码](characterize_registers.py) · [表 2](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=7) · [首位相位](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48) · [表 20](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=49)

**手册次序的定向检查。** 在项目增益 129/128、原码 0、OFFSET=1024 下，按图 96 得到精确整数 1032。修订前的反例输出 1024，修订后的 RTL 和位真模型均输出 1032。`--require-manual-order` 本次退出 0，今后实测有差异时退出 2，并仍写出回执。 [图 96](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=64) · [机器可读回执](rtl_verification.json)

## 已跑过的对照

在合成通道 `x ≈ 1.005t + 80` 上分别训练，在另一批 1,024 个未参与拟合的电平上验证。表中误差来自现有 Verilog 模块输出，单位为 LSB20。物理器件性能需另行测量。

| 方法 | OFFSET / GAIN | 留出 RMS，修正前 → 后 | 留出平均误差，修正前 → 后 | 修正后最大绝对误差 |
| --- | --- | ---: | ---: | ---: |
| 手册两点 DC | −80 / `0x15D` | 1348.1522 → 0.4038 | +22.6738 → −0.0166 | 1 |
| ADCToolbox 已知正弦 | −80 / `0x15D` | 1348.1522 → 0.4038 | +22.6738 → −0.0166 | 1 |

两组得到相同的量化寄存器码，因此共享留出结果。对实际 `sys_error_corr` 的 8,234 个工程有效输出逐字与整数模型一致，另有一笔独立图 96 对照。回归同时核对取消、复位和两侧饱和。`example_dc.csv` 提供另一组对称 DC 激励。 [Verilator 回执](rtl_verification.json) · [独立复跑代码](verify_rtl.py) · [源码与手册的对应关系](SOURCES.md)

```sh
python -B -m unittest discover -s "$cal" -p 'test_*.py'
python -B "$cal/verify_rtl.py" --repo . --verilator verilator \
  --out /tmp/ad4080-system-cal.json --require-manual-order
```

产品集成另用实际配置 SPI 写入并读回正弦估计的四个字节，并观察同一份 16K×20 结果 SRAM 的前后记录。中性样本为 `−301420、80、251330`，本版整数数学的校准后三笔期望为 `−300000、0、250000`。实际帧与 CRC 见产品级回执。 [SPI 和 SRAM 回读证据](../integration/product/evidence/calibration.txt) · [复跑命令](../integration/product/README.md#产品级-offsetgain-验证)
