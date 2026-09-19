# ADR 0018：按物理单元校正与论文结构控制

日期：2026-09-20。状态：已实现，验证结果见 `docs/rtl/STRUCTURAL_CALIBRATION_20260920.md`。
承接 ADR0017；用户明确授权扩展顶层端口与内部结构，并要求分模块、优先数字校准。

## 决策与实现边界

默认顶层 `sar20_digital_core.P_STRUCTURAL=1`，输入改用共享 3-bit Flash 的
7 路比较结果、两套 SAR 比较器握手以及 ADC2 比较器握手。
`P_STRUCTURAL=0` 显式保留原粗/细码接口，用于旧模型向量回归。
两种模式共用完整系数配置、物理权重校正与输出状态，不共用错误的跨样本上下文。

论文 [00] 披露的是外部求系数、片上 DAC 权重校正。此实现提供片上校正硬件，
不把未经披露的 LMS/RLS/最小二乘训练器冒充原芯片算法。模拟宏通过控制端口连接；
RTL 不是 CDAC、参考缓冲器、RA 或采样 MOS 的晶体管网表。

## 来源逐项映射

| 来源 | 可核实的结构/机制 | 本次 RTL 对应 | 仍需区分的实现选择 |
|---|---|---|---|
| [00] p1、Fig9.8.1；[00_1] slide12 | 两套 SAR、共享 3-bit Flash、独立 RDAC | `sar_trial_ctrl` ×2、`sadc_enc(P_B1=3)` | 二进制基数、逐拍握手不是原芯片 SAR 的完整时序披露 |
| [00]、专利 [09] US10516408B2 | RDAC 跟随已决位，不承受 SAR 暂态试探 | `resolved_code/resolved_valid` 到 RDAC 驱动 | 63+8 单元展开为原项目模型选择，不等于版图单元数 |
| [00]、[12] US10707889B1 | 18 中选 8 转换、8 采集、2 备用 | `slice_pool_ctrl` | PRNG 扫描起点和排列策略为工程实现，不声称均匀遍历全部组合 |
| [00] Fig9.8.2 | 共享 TP 关键沿、每 slice EN/hold-low、量化器采样支路 | `analog_phase_ctrl`、acquiring/hold-low、coarse_acquire_enable | MOS 串联开关、延迟、VT、共扩散布局必须由模拟宏落实 |
| [00] | 输入与上一笔 RA 余差同一安静窗口采样 | `cal_sample_context` 两槽上下文 | ADC2 编码分辨率、相位裕量沿用工程假设 |
| [10] US10505561B2 | 量化器注入并在余差路径补偿 | `quantizer_dither` 与反号 `remove_dither` | 原芯片注入电容、幅值、概率序列未全披露 |
| [00_1] slide31、[11] US10511316B2 | slice/horizontal/vertical DEM、桥接约 50% 活动 | slice pool、原 `dem_addr_gen`、`sid[8]` 门控桥接 | 保留原项目 DEM 单元映射；不能宣称与专利所有可选分段电路等同 |
| [13] US10541702B1 Fig2/11 | AUX 提供采样开关寄生/boost 充电 | `aux_charge_enable` | 图10/12 的附加采样电容预充电属于其他可选模拟实施例，未伪造为同一电路 |
| [00] Fig9.8.3、[14] US10826519B1 | 转换期粗参考充电、余差期准确外参考 | 独立 `ref_precharge/ref_accurate` 与死区 | 比较器电荷泵、储能电容、慢放大器等专利替代实施例须单独选型 |
| [00] p1 | 外部求权重、片上校正 | `weight_store`、`cal_weight_reduce`、`cal_residue_mac`、`div_floor` | Q30/Q32、96-bit 检查与实现流水为 ADR0014/本工程选择 |

[01]–[08] 是相关架构/产品对照，不能把各文献电路同时拼成 [00] 芯片。
论文工艺为 40nm；工程现有 28nm 库只可作独立映射试验，不能直接比较版图 PPA。

主要公开专利原文：
[stage](https://patents.google.com/patent/US10516408B2/en)、
[dither](https://patents.google.com/patent/US10505561B2/en)、
[DEM](https://patents.google.com/patent/US10511316B2/en)、
[interleaving](https://patents.google.com/patent/US10707889B1/en)、
[AUX](https://patents.google.com/patent/US10541702B1/en)、
[reference](https://patents.google.com/patent/US10826519B1/en)。
本仓库只保存来源定位与自行实现的 RTL，不重新分发用户提供的全文/图像。

## 校正公式与数据归属

每个物理单元的系数 `W[s][u]` 为正 Q30。定义：

- `T = ΣW`：本次参与 RDAC 的所有物理单元权重。
- `G = Σ(W*a)`：实际采集输入的单元权重；采样 dither 专用单元 `a=0`。
- `R = ΣW - 2Σ(W*on) + Σ(W*r)`：实际开关轨对应的带符号权重。
- `F/O/I`：后级去增益后的余差、偏置、独立已知输入注入，均为 Q32。

```text
N = (F - O)*2^30 - R*2^32 - T*I
C = clamp(floor((N + G*2^32)*2^20 / (G*2^33)), 0, 2^20-1)
```

与 ADR0014 的整数公式一致。除法对负数向负无穷取整，不能换成截零。
受检操作数超出 96-bit 符号域或 `G<=0` 时保留最后合法码，同时完成事务并输出异常位。
权重输入直接使用 `weight_store` 已提交的整个物理表。数据通路不访问真实输入、
浮点模型或训练结果以外的隐藏模拟状态。

`cal_weight_reduce` 把窄开关掩码路由至静态物理行，每个系数直接接本单元求和叶子，
避免八套 48-bit 宽、18:1 的权重选择网络。重复或越界 slice ID 产生结构错误。
`cal_residue_mac` 实现乘加与溢出检查；`div_floor` 为可配置展开级数的迭代除法；
`cal_output_stage` 将码、sample_id、单笔 flags 一起提交。

`result_flags[4:0] = {clip_high, clip_low, adc2_ovf, gain_err, acc_ovf}`。
它属于当前结果，区别于粘滞状态。`dout_valid=1` 并不自动表示本次码合法：
遇到 gain/acc 异常时下游必须依据 flags 丢弃保持的旧码。
模拟错误另由当前余差上下文进入顶层粘滞 `analog_ovf`，没有冒充数字溢出。

系数以 epoch 为一致性边界：所有物理权重与标量写齐才能 validate；运行中禁止改写。
clear 撤销配置并清空在途转换，保留数值用于回读但必须重新完整加载。
这保证同一结果不会混用前后两版系数。当前版本不支持不停机更新系数。

## 时序与模拟接口契约

所有比较器 valid/data 均须满足 `clk` 的建立保持要求。没有添加两级同步器来
掩盖模拟比较器握手的时序问题。原设计异步 SAR 需要专用握手/CDC 方案，不能直接套用。

| 默认 phase | 行为 |
|---|---|
| 0 | TP 为低的安静窗口；锁存上一笔 RA 上下文至 ADC2，上周期 acquisition 的 dither/injection 成为当前样本 |
| 1 | 曾经 acquisition 的 8 slice 晋升 conversion；从其补集选择下一组 acquisition；采集 Flash 种子并启动当前 SAR/上一笔 ADC2；更新非关键 EN |
| 2–7 | 6 次粗 SAR 已决位更新；RDAC 跟随已决码；另一个量化器继续采集 |
| 8 | 粗结果截止；没有完成则当前余差失效，取消残留比较；RDAC 装入最后已决状态 |
| 9 | 捕获当前 RDAC 物理上下文；参考切换死区 |
| 10–15 | 准确外参考及 RA 放大阶段；下一采样窗口之前需要满足实际模拟建立 |
| 14 | ADC2 结果截止；未完成则丢弃上一笔上下文的输出，允许后续帧恢复 |
| 15 | 对上一笔余差发起数字重构；忙冲突报告错误，不偷偷串接其他样本 |

TP/ref/AZ/amplify 是寄存器输出，不能改为二进制相位计数器的裸组合译码后直接驱动模拟开关。
quiet_sample 表示数字观察窗口；真实电荷采样由 TP 下降沿和本地 EN 决定。
模拟宏必须把此次采样相关读回量保持到窗口末端的锁存沿。
reset/clear 按 `clk` 同步生效；clear 时在途数据无效。

粗 SAR 试探码不直接代表每只 MOS 控制码；应接独立量化器 DAC 宏。
compare_enable 是进行比较的使能电平，**不是**动态比较器的reset/evaluate脉冲。
模拟宏必须在每个 clk 周期内完成试探建立、比较器复位/求值，并在下个接受沿前提供valid/data；
这些门极级时钟没有被未经依据的组合逻辑伪造。
Flash 使用 7 路有效温度计输出，宏负责判决/亚稳与 bubble 处理。
`coarse_acquire_enable` 区分两个量化器的 acquisition；`fine_acquire_enable`
和 `flash_acquire_enable` 分别控制共享后级与 Flash 返回采集。
`acquiring_mask` 与 conversion masks 指定 RDAC 的物理归属；`hold_low_enable`
是本地 hold-low 支路使能，实际门极波形仍由模拟宏生成。

量化器 dither：模拟宏应把 `quantizer_dither[b]` 以约定 RDAC 单位加到对应量化器。
RDAC 译码减去同一笔 dither；独立 `inj_q` **不再包含该量化器 dither**，否则双重扣除。
采样 dither：模拟宏使用 acquisition_mask/`acquisition_dither_rails` 采集该组轨，
转换与校正保留同一组轨；两个模式互斥。

## 调节与时序限制

顶层旋钮：`P_RECON_STAGES` 默认7、`P_REF_ON` 默认10、`P_RESIDUE_CAPTURE`
默认9、`P_SHUFFLE_SEED` 非零。`P_RECON_STAGES` 在固定16拍吞吐下允许5–63；
独立 `recon_core` 可扫更小展开值但不能把结果当成顶层同吞吐实现。
从接受 start 的沿到 dout_valid 的沿为 `ceil(63/P_RECON_STAGES)+2` 个完整时钟周期，
默认11；若把接受沿算第1沿，则是第12沿，二者不能混写。

16×40MHz=640MHz 只是工程频率预算。本实现的粗 SAR 为多拍同步比较，
不能因此声称达到论文约7ns粗转换、<1ns Flash返回、20-bit建立或0.6ps采样失配。
ADC2 12-bit、71单位阵列和数字 radix 都是当前模型配置；未披露部分保留为模拟设计输入。
20-bit 输出编码不等于20-bit ENOB，静态校正不能修复带宽/采样时刻失配。
