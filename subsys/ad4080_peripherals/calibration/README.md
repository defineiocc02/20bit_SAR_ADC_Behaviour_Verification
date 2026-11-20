# 用 ADCToolbox 估计系统 OFFSET 和 GAIN

**主机估计系数，已有 DSP 在片上逐样本修正。** 要校准的是传感器、驱动和 ADC 组成的整个测量通道。输入为转换 IP 已经重构好的 20 位结果。ADCToolbox 的 `fit_sine_4param` 从实测序列拟合基波峰值和直流分量；将它们与独立标定的输入幅度、直流中心比较，即可求出绝对增益和零偏。也可按 AD4080 手册用两个已知直流电平求解。[ADCToolbox 源码](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L49) · [手册第 71 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) · [现有 DSP 修正 RTL](../../ad4080_dsp/rtl/sys_error_corr.sv)

## 快速运行

在仓库根执行。依赖仅装在独立主机环境，固定为此次核实的 ADCToolbox 0.9.1 源码提交。

```sh
python3.12 -m venv /tmp/ad4080-cal
. /tmp/ad4080-cal/bin/activate
cal=subsys/ad4080_peripherals/calibration
python -m pip install -r "$cal/requirements.txt"
python "$cal/system_cal.py" dc --csv "$cal/example_dc.csv" --vref-v 3 --code-format signed20 --k-gain 1 --shift 15
```

这份可运行 CSV 用 ±1.5 V 两电平和 6 笔有符号码演示。结果为 `GAIN=0x15D`、`OFFSET=-80`，12 位寄存器字段为 `0xFB0`。按地址列出的字节为 `0x25=B0, 0x26=0F, 0x27=5D, 0x28=01`。CLI 打印计划，实际同一寄存器的两个字节应按配置 SPI 的地址递增/递减模式原子发送，写后完整读回。数据手册第 71 页关于 OFFSET 的地址文字与第 92 页不一致，本工具采用第 92 页表 60。[样本 CSV](example_dc.csv) · [表 60](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=92) · [表 61](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93)

## 采什么，怎么算

先将 `OFFSET=0`、`GAIN=0x200`，关闭滤波或锁定所测通路的带宽，等输入稳定后采集。用 ADC 引脚处的已知差分电压与实测 VREF。`LSB = 2 × VREF / 2²⁰`，VREF=3 V 时约为 5.722 µV。CSV 的码制可选 `signed20`、`wire_twos20`、`offset_binary` 或 `spi24_sign_extended`；最后一种要求高四位与 20 位符号一致。

- **两点直流。** CSV 列为 `vin_diff_v,code`，各电平重复采样。令理想有符号码为 `t=Vin/LSB`，实读为 `x`，两组均值给出 `a=(xH−xL)/(tH−tL)` 与 `b=xL−a·tL`。连续值修正为 `(x−b)/a`。这部分按手册公式实现，定点应用直接复用原 DSP。
- **已知正弦。** CSV 的 `code` 列按均匀采样顺序排列，API 使用 `fit_sine_4param(codes, frequency_estimate=Fin/Fs, max_iterations=0)`。第 0 次迭代对余弦、正弦、常数三列做最小二乘，取返回的 `amplitude` 和 `dc_offset`。比例 `a=Ameas/(Aref/LSB)`，偏置 `b=DCmeas−a·DCref/LSB`。输入参考必须是**差分峰值**和直流中心。只有未知幅度的一串 ADC 输出时，仪器幅度和通道增益无法分别辨识。正弦幅度法假定通道为正极性，可用两个直流电平确认。[算法与返回值](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L60)

例如以下命令测 40 MS/s 下的一段已标定正弦。要求 `sine.csv` 实际对应 1.5 V 差分峰值、0.125 V 差分直流和给定频率。省略寄存器映射时，JSON 仅给连续值估计。`--max-rmse-codes` 可按台架噪声预算限制拟合残差。

```sh
python "$cal/system_cal.py" sine --csv sine.csv --vref-v 3 --fs-hz 40000000 \
  --fin-hz 1240234.375 --reference-peak-v 1.5 --reference-dc-v 0.125
```

## 怎样得到可写的寄存器码

**本项目 RTL。** 已有 `sys_error_corr` 使用显式绑定的 `m(k)=1+(k−512)·K_GAIN/2^SHIFT`。脚本选最接近 `1/a` 的已授权增益码，再按量化后增益计算 `OFFSET=round(−m(k)·b)`，以抵消物理零输入时的误差。OFFSET 用 12 位补码，GAIN 用 10 位直码。饱和、输入触及端点、越出可表示范围等条件均应检查。主机选寄存器时的舍入与 RTL 逐样本乘积的半步处理分别测试。`K_GAIN=1, SHIFT=15` 是可复现的工程绑定。[位真软件参考](../../ad4080_dsp/model/ad4080_cal_model.py) · [校准程序](system_cal.py)

**实际器件。** 手册给出了单位码和端点，准确的内部增益码步、加乘顺序和逐样本舍入尚未从公开资料唯一确定。可以在选取的 GAIN 档用实物求表，再在拟使用的 GAIN 下单独测 OFFSET 灵敏度。下面是可执行的数据格式及命令。

1. 在同一颗器件、OFFSET=0 下，每个已回读的 GAIN 测两个已知直流电平，各取多笔，必须含单位码 `0x200`。保存 `gain_code,vin_diff_v,code` 到 `scan.csv`。每档用自己的两点斜率除以单位档斜率，得到可识别的实际代码增益。
2. 用这张表对中性设置下的训练记录选择已测档。程序不外推未覆盖范围。把所选 GAIN 写入并读回，OFFSET 保持零。若表步距太宽，在目标附近增测相邻码。
3. 固定所选 GAIN 和同一个已知直流，测 OFFSET=0 与一个非零试调码。CSV 列为 `gain_code,offset_lsb,vin_diff_v,code`，其中 `offset_lsb` 是有符号值。由输出均值差除以寄存器差得到该档的实际偏移步长，反算最终 OFFSET。最后在新的正负电平和零点复测。

```sh
python "$cal/characterize_registers.py" gain-table --csv scan.csv --vref-v 3 --output gain-map.csv
python "$cal/system_cal.py" dc --csv "$cal/example_dc.csv" --vref-v 3 --gain-table gain-map.csv
python "$cal/characterize_registers.py" offset-trim --csv trim.csv --vref-v 3
```

上例第二步演示参数格式，实物运行时换成**该器件在中性寄存器下的实测记录**。经调整的系统增益可能改变器件的最短读出时间。另验 CNV 到首位的设置，再评估留出样本。[实测档位处理代码](characterize_registers.py) · [手册接口延迟](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=48)

## 已跑过的对照

在合成通道 `x ≈ 1.005t + 80` 上分别训练，在另一批 1,024 个未参与拟合的电平上验证。表中误差来自冻结 Verilog 模块输出，单位为 LSB20；物理器件性能需另行测量。

| 方法 | OFFSET / GAIN | 留出 RMS，修正前 → 后 | 留出平均误差，修正前 → 后 | 修正后最大绝对误差 |
| --- | --- | ---: | ---: | ---: |
| 手册两点 DC | −79 / `0x15D` | 1348.1522 → 0.7718 | +22.6738 → +0.5957 | 1 |
| ADCToolbox 已知正弦 | −80 / `0x15D` | 1348.1522 → 0.6358 | +22.6738 → −0.4043 | 1 |

两组有不同训练输入，原始码取整噪声使最终 OFFSET 相差一个寄存器步。对实际 `sys_error_corr` 的 8,234 个有效输出（含两种设置、训练、留出和边界）逐字与原项目独立整数参考一致，另核了取消、复位和饱和标志。两点文件 `example_dc.csv` 是方便阅读的另一组对称 DC 激励。[Verilator 回执](rtl_verification.json) · [独立复跑代码](verify_rtl.py) · [源码与手册的对应关系](SOURCES.md)

```sh
python -B -m unittest discover -s "$cal" -p 'test_*.py'
python -B "$cal/verify_rtl.py" --repo . --verilator verilator --out /tmp/ad4080-system-cal.json
```
