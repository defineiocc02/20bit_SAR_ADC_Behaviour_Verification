# AD4080 系统 OFFSET/GAIN 的源码依据

## 两点直流和正弦拟合

以 `OFFSET=0`、`GAIN=0x200` 采集有符号 20 位码。记录两个已知差分电平，靠近两端并留削顶余量。令 $\Delta=2V_{\mathrm{REFIN}}/2^{20}$，$s_i=V_i/\Delta$ 为理想码，$\bar x_i$ 为实测组均值。前向模型是 $x=as+b$，其中 $a=(\bar x_2-\bar x_1)/(s_2-s_1)$、$b=\bar x_1-as_1$。校正系数是 $m=1/a$、$c=-mb$。两点分组、反解与 AD4080 选号由本包实现。固定版 ADCToolbox 0.9.1 的 `calibration` 公开入口围绕 SAR 位权求解。上游 `cb3636c` 新增通用有监督 `fit_calibration_polynomial`，可用单特征和 `degree=1, ridge=0` 拟合给定参考的仿射关系，寄存器映射仍由本包或台架提供。 [ADI Rev. B p.20](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=20) · [p.71](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) · [固定版校准入口](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/calibration/__init__.py#L1-L13) · [上游通用拟合](https://github.com/Arcadia-1/ADCToolbox/blob/cb3636cd3b68ba3a6ec321a6b5c71e244d9a85d3/python/src/adctoolbox/calibration/foreground.py#L121-L167) · [本包工具](system_cal.py)

`fit_sine_4param` 拟合 $A\cos(2\pi fn)+B\sin(2\pi fn)+C$。横轴 $n$ 是样本号，`frequency_estimate` 和返回的 `frequency` 均为 $f=F_\mathrm{in}/F_s$。`amplitude` 是峰值 $\sqrt{A^2+B^2}$，`phase` 是余弦形式的 `atan2(-B,A)`，单位为弧度。`dc_offset` 为 $C$，与幅值、`rmse` 一样沿用输入单位。已知差分正弦峰值 $V_\mathrm{pk}$ 和 DC 值 $V_\mathrm{dc}$ 时，在正极性前提下取 $a=\mathrm{amplitude}/(V_\mathrm{pk}/\Delta)$，$b=C-aV_\mathrm{dc}/\Delta$。本包显式设 `max_iterations=0`，使用已知频点做定频三参数拟合。上游默认迭代数从 1 改为 100，且在最终频率重算 $A,B,C$。定频调用及归一化单位兼容。 [固定版实装](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L49-L114) · [上游签名和默认值](https://github.com/Arcadia-1/ADCToolbox/blob/cb3636cd3b68ba3a6ec321a6b5c71e244d9a85d3/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L10-L47) · [上游末频重拟合](https://github.com/Arcadia-1/ADCToolbox/blob/cb3636cd3b68ba3a6ec321a6b5c71e244d9a85d3/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L137-L174)

`extract_mismatch_sine(x,M,fs,fin)` 按 `x[m::M]` 拆通道，`fs/fin` 以 Hz 提供。每通道求均值和相干基波相量，`gain[m]` 是该通道幅值除以各通道幅值均值。所有通道共同放大时此比值不变，`offset[m]` 也包含输入的 DC。绝对系统增益须与已标定的差分输入比较。该函数在核对的两提交间源码一致。 [接口和单位](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/timeinterleave/extract_mismatch_sine.py#L18-L48) · [相量归一化](https://github.com/Arcadia-1/ADCToolbox/blob/cb3636cd3b68ba3a6ec321a6b5c71e244d9a85d3/python/src/adctoolbox/timeinterleave/extract_mismatch_sine.py#L65-L102)

## 寄存器字节和舍入

| 字段或码字 | 低字节 | 高字节 | Rev. B 含义 |
| --- | --- | --- | --- |
| `OFFSET` | `0x25` 存 `[7:0]` | `0x26` 的低四位存 `[11:8]` | 12 位补码，−2048 至 +2047 ADC LSB，复位 0。−64 编为 `0x0FC0`，两地址分别填 `C0`、`0F` |
| `GAIN=0x200` | `0x27` 写 `00` | `0x28` 写 `02` | 10 位自然二进制，中性码及复位值，器件旁路增益校正 |
| `GAIN=0x001` | `0x27` 写 `01` | `0x28` 写 `00` | 公布斜率 0.984406 |
| `GAIN=0x3FF` | `0x27` 写 `FF` | `0x28` 写 `03` | 公布斜率 1.015594 |

保留位写零。p.71 的 OFFSET 地址与 p.74、p.92 字段表冲突，`0x24` 是 LO 阈值高字节，OFFSET 采用 `0x25/0x26`。严格双字节访问依据 p.38 图 58/59，升序从低地址送低字节，降序从高地址送高字节，再读回整字。 [ADI p.37–38](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=37) · [p.74 字节分配](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=74) · [p.92–93 字段和端点](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=92) · [ADI no-OS 地址](https://github.com/analogdevicesinc/no-OS/blob/0e6ed1dea135ef541ad6323fc9fcad116ffce90b/drivers/adc/ad4080/ad4080.h#L47-L52) · [冻结 RTL 字节方向](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf8db9a41eebdd756378b69d2775f107dec/subsys/ad4080_dsp/rtl/ad4080_regs.sv#L115-L148)

p.71 给的名义步长是 $0.015594/512$。p.93 两端与 `0x200` 各相距 511 码，按端点线性插值得 $0.015594/511$。两种口径都约为 30.5 ppm/码。精确选号在明确的工程映射 $m_q(G)=1+(G-512)K/2^S$ 或以 `0x200` 为 1.0 的实测斜率表中找最近档。`K_GAIN=1, SHIFT=15` 是工程绑定，其 `0x001/0x3FF` 斜率约为 0.98440552、1.01559448。器件精确中间码映射、`0x000` 和内部舍入需实测。缺映射时，本包保留连续估计。 [ADI p.71](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=71) · [p.93 表 61](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=93) · [工程选号](system_cal.py) · [器件测表](characterize_registers.py)

**系数舍入与样本舍入。** 先选增益码 $G$，再用该档斜率 $m_q$ 重算零点补偿 $O=\lfloor-m_qb+0.5\rfloor$。用未量化的 $m$ 先算 OFFSET 会留下与 $(m_q-m)b$ 有关的零点误差。样本处理以冻结 RTL 和同一参数的 `sys_corr` 为逐码依据。把增量 $u=x(G-512)K/2^S$ 舍入为 $\lfloor u+0.5\rfloor$，加 $x+O$ 后限幅至 −524288…524287。对整数 $x+O$，整式用同一半步向正无穷的规则舍入也等价。例如 `K=1, S=15, x=16384, G=0x1FD, O=0`，增量 −1.5 变为 −1，结果 16383。若对 16382.5 执行 ties-to-even，结果是 16382。若把 OFFSET 放在乘法输入，连续模型中的偏移项会变成 $m_qO$。器件的有效偏移步长应在所选增益下写后复测。 [冻结 RTL](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf8db9a41eebdd756378b69d2775f107dec/subsys/ad4080_dsp/rtl/sys_error_corr.sv#L58-L114) · [冻结 `sys_corr`](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf8db9a41eebdd756378b69d2775f107dec/subsys/ad4080_dsp/model/ad4080_cal_model.py#L45-L100) · [逐拍校验](verify_rtl.py)

## 结果怎么判

- 系统校正看独立参考上的有符号码误差、零点、剩余斜率和 RMS。两点训练的组内散布与正弦拟合残差各有自己的口径。正弦拟合已吸收该记录的绝对幅值和 DC，验收使用独立已知的电压参考。 [本包统计](system_cal.py) · [拟合残差公式](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/fundamentals/fit_sine_4param.py#L99-L110)
- INL/DNL 用有充分码覆盖的独立记录估计转移特性和码宽。SNDR 用相同激励、采样率、分析带宽、窗和基波信号频带单独比较。去 DC 后，无削顶或重新量化的理想仿射校正共同缩放基波、噪声和谐波，零点及斜率变准本身不规定 SNDR 增量。 [直方图 INL/DNL](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/aout/compute_inl_from_sine.py#L11-L37) · [SNDR 源码](https://github.com/Arcadia-1/ADCToolbox/blob/8d62fb1863e38ffe5c6cc3ac48fa9c24d9df1ae9/python/src/adctoolbox/spectrum/compute_spectrum.py#L118-L156)
