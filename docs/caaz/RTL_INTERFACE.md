# CAAZ 数字接口与40MS/s预算

本实现是可选、默认关闭的**数字样本域机制**。它不构成模拟CAAZ闭环或640MHz时序签核。来源为`PKU_VLSI2025_C32_1_PAPER`（PDF/印刷第2页Fig.5/6）及`PKU_VLSI2025_C32_1_SLIDES`（PDF28–35页，页脚Slide27–34）。源图把chopper放在整个ADC输入之前，even更新CBIAS的AZ存储，odd保持同一状态；完整码合并/位权校准之后再作偶减奇。源文的6MS/s与raw/pair分母未充分披露，本工程选raw40/pair20，不声称复刻其物理ODR。

## 启用范围

直接例化`sar20_digital_core(P_STRUCTURAL=1,P_CAAZ_ENABLE=1)`启用。参数默认0；关闭时原code/valid/ID/flags/clip/analog_bad保持原位与原时序，pair处理器为零拍组合旁路。该参数不是0x1018的新增运行时位；不支持在pair中途软件切换。

当前`ad4080_dsp/rtl/sar20_system_top.sv`只显式绑定默认关闭的可选端口，**未透传CAAZ参数或宏ack/control**。不能把core的启用能力写成DSP组合系统已可配置完整CAAZ。

## 模拟宏控制与样本归属

| 端口 | 归属与语义 |
|---|---|
| `caaz_input_chop_neg` | 正在acquire、下一次晋升current的输入极性；0直通正相，1交换差分输入。必须作用于整个ADC输入、包括coarse路径，不能仅翻转RA residue或ADC2码。 |
| `caaz_az_capture_en` | 为current RA生成的even-only CBIAS AZ采样更新使能。当前选phase1..8窗口，并在phase8沿接受资格；这是工程接口选点。 |
| `caaz_az_hold` | current odd周期保持CBIAS状态，独立于普通放大器/储能电容复位。 |
| `caaz_az_capture_ok` | 同步输入，模拟宏对even存储截止的资格确认；失败会同时使其even和下一odd的AZ标签无效。测试台置1不是模拟建立证明。 |
| legacy `ra_az` | 每cycle保持原有reset/preparation时序。不得把odd跳过AZ采样解释成整个RA、CP-FIA reservoir或普通reset停止。实际宏须把这些开关分开。 |

`caaz_cycle_ctrl`使用pool实际`acquisition_primed`。首个quiet在prime前不翻转；首有效ID2取正相，ID3取负相。quiet同沿先保存旧input chop为current，再为下一acquiring准备新极性。AZcapture属于当前将放大的residue；fine/reconstruction使用上一residue冻结标签，不能读当前bank或全局当下奇偶。

新34位tag依次经过`cal_sample_context`的residue_capture→quiet转交→重构接受时的pending寄存器。即使某个coarse/fine valid缺失，物理chop和AZ代照原采样节拍推进；后续记录不能因少一个输出而重新按输出计数配对。

输入chop在quiet事务后的切换还要求模拟宏正确保持旧采样组。本实现没有证明直接`TP AND EN`的物理开关不会重开；本地锁存/inhibit、chopper非交叠、电荷注入和CBIAS隔离/保持必须单独验证。源文AZ发生在其采样阶段，未给本工程16tick定量窗口；不能将当前phase1..8选点写成原文逐相位复刻。

## 码域与输出

现有`CodeStream.voltage`定义为码箱中心：

$$
v(c)=-V_{FS}+(c+0.5)\,LSB,\qquad M=2^{20},\quad LSB=2V_{FS}/M.
$$

对于实际even正相与odd反相记录：

$$
d=c_e-c_o,\quad v_{pair}=(v(c_e)-v(c_o))/2=LSB\,d/2.
$$

`d`用21位signed保存，范围`[-(M-1),M-1]`。20位输出按既有uniform floor合同：

$$
c_{pair}=\left\lfloor\frac{M+d}{2}\right\rfloor.
$$

`M+d`先用22位有符号量保存再移位，合法输入结果自然落在`0..M-1`。不能直接对20位signed最小数取负，也不能省略bin-center的0.5后声称平均无偏。`d=0`的精确pair电压为0，重新量化的20位码中心仍有半LSB误差；21位差同时保留用于精确后级处理。

CAAZ打开时主`dout`每两个raw结果产生一个pair；odd接受沿提交，较odd raw结果事件多一个时钟。`dout_sample_id`为odd末ID，`caaz_pair_first_sample_id`保留even首ID，`caaz_pair_id`保留AZ存储代。`caaz_pair_difference_valid`与pair结果有效同拍。两笔输入任一flags非零、analog_bad或AZ无效都会拒配；合法轨端码本身不等于错误。

## 观察格式

旧748位`cal_obs_data`布局不变，`cal_obs_valid`仍是原重构接受条件。新增`cal_obs_caaz_data[33:0]`使用同一接受沿：`[33] chop_neg`、`[32:1] az_pair_id`、`[0] az_valid`。

新增`caaz_raw_valid`标记尚未成对的完整校准raw结果；`caaz_raw_data[91:0]`为：

| 位 | 字段 |
|---|---|
| 91:60 | raw sample ID |
| 59:40 | 20bit offset-binary code |
| 39 | actual chop_neg |
| 38:7 | AZ pair generation |
| 6 | AZ valid |
| 5:1 | `{clip_high,clip_low,adc2_ovf,gain_err,acc_ovf}` |
| 0 | 与raw结果对齐的analog_bad |

CAAZ关闭时raw观察valid为0，34/92位数据为0；pair元数据只有在pair_difference_valid有效时才有pair语义。上述观察口没有传输背压，也没有全局epoch字段；捕获系统必须记录`cfg_clear_valid`/外部epoch号，不能将不同epoch的重复ID2/3直接拼接。

## 故障与清除

Pair处理要求even→odd、连续sample ID、pair内AZ代相同、下一pair代递增；32位ID/代按模回绕。输入时钟空洞允许，丢记录在后续ID或phase检查时检出，不提供独立wallclock超时。捕获结束时只剩even不会产生虚假pair，clear会取消pending。

`caaz_pair_fault`保持到reset/epoch clear；原因1=raw flags、2=analog_bad、3=AZ无效、4=ID缺口、5=极性顺序、6=AZ代错配。fault后停止成对，raw观察继续供定位，不自动拿后续valid修补。状态错误新增`ERR_CAAZ_PAIR=12`，旧错误ABI不重编号。clear同沿odd被取消，系数仍按原完整重载合同重新生效。

## 40MS/s预算与限制

固定16tick的raw40MS/s需要640MHz、每tick1.5625ns：legacy AZ高8tick=12.5ns，RA amplify/accurate高7tick=10.9375ns，phase9死区1tick=1.5625ns，fine acquire高3tick=4.6875ns。高电平长度不是可无条件使用的建立时间；若真实ADC2在TP下降沿关采样，放大至该沿只有6tick=9.375ns。必须由模拟宏冻结真实采样关闭沿和非交叠/建立截止。

非重叠pair使输出率为20MS/s，时间戳位于两笔真实采样时刻的中点。两点平均有`|cos(pi*f/fraw)|`幅频响应；raw40时10MHz已衰减3.01dB，且两tap不能自动完成抽2所需完整抗混叠。若要求最终40MS/s完整pair，维持本16tick架构需raw80MS/s/1.28GHz或另行架构，当前没有该验收。

全速滑动平均不是本实现。跨AZ代窗口残留`(e[k]-e[k-1])/2`，不能把所有40M滑动结果都宣称AZ噪声精确消除。即使正确pair，保持误差、两相噪声传递增益差、fresh放大器/ADC2噪声仍须模拟和模型验证，不移用源文4.3dB收益作本工程结果。

## 验证入口

- `caaz_pair_processor_tb`：整数边界、独立bin-center参考、默认off旁路及fault/clear/回绕。
- `caaz_python_vectors_tb`：正式Python完整raw模型与独立状态oracle的共享fixture，468周期/206输出。
- `caaz_context_contract_tb`：真实结构core、ID2/3和missingvalid、AZ ack失败、legacy reset每cycle及tag经真实context/recon传播。

测试需要实际运行日志，不能只凭本文件或参数名宣称PASS。标准runner已注册这3项及CAAZ on/off观察profile。模拟宏、目标PDK综合/STA、CDC/RDC及完整产品PHY仍独立验收；当前DSP系统wrapper未启用CAAZ。
