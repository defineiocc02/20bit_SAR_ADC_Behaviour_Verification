# SAR工程的并行DSP扩展

> 15个现行模块的限定数字证据为handoff003：14指定场景、5严格lint profile。旧`cal_two_point`、`lvds_framer`和`settled`仅在af8／9bdf历史快照中解释。

本扩展实现系统增益／失调应用、输出SINC、捕获FIFO、配置SPI／寄存器和GPIO。它与SAR行为／数字校准工程连接，但不实现模拟ADC、片内位权学习、结果数据PHY、工艺SRAM或目标ASIC时序。[追溯表](docs/ad4080_traceability.md)将手册功能与工程选择分开。

## 与SAR连接

[sar20_system_top](rtl/sar20_system_top.sv)连接真实SAR数字核和[sar20_ad4080_top](rtl/sar20_ad4080_top.sv)。核心仍需完整显式输入包、两只source绑定与真实模拟宏返回；DSP组合不会补齐这些设计输入。

SAR端和适配层并行输出为20位offset-binary，内部DSP使用补码，入口／出口各翻转一次最高位。各结果、控制和清除按同一clk合同；SPI／GPIO同步器不代表整个系统CDC/RDC已签核。[SAR20接口](docs/SAR20_INTERFACE.md)定义接受、拒绝、异常和复位边界。

任一SAR结果flag、模拟sticky或已报告的`conversion_fault`会live门控输入／输出并锁存stream_fault；该故障包括controller错误及CAAZ pair fault，独立于合并status的总线优先级。无raw有效时不伪造rejected ID；后续真实good raw即使flags0仍被拒收。输入侧accepted／rejected携带实际raw ID，滤波、FIFO与最终输出仍没有完整逐结果ID／flags／配置代次；未报告的任意ID缺口不由此检测或精确归因。

## 实际模块与支持域

| 组 | 当前模块与功能 | 需要保留的边界 |
| :--- | :--- | :--- |
| 系统校正 | [sys_error_corr](rtl/sys_error_corr.sv)应用增益／失调并报告clip；[ad4080_dsp_chain](rtl/ad4080_dsp_chain.sv)组合处理 | 非unity增益律必须显式绑定；没有逆向两点系数求解或片内C/Cf学习器 |
| 输出滤波 | [sinc1_decim](rtl/sinc1_decim.sv)、[sinc5_decim](rtl/sinc5_decim.sv)、[sinc5_comp](rtl/sinc5_comp.sv) | valid只是数值输出；没有基于真实CNV的settled／ready承诺。补偿需明确有效系数，缺项拒绝，不放单位占位 |
| 捕获与队列 | [result_fifo](rtl/result_fifo.sv)、[fifo_capture](rtl/fifo_capture.sv) | 一条捕获链；完成／未完成记录、早事件和再arm分别检查。存储宏与物理时序另验 |
| 配置与GPIO | [spi_slave](rtl/spi_slave.sv)、[ad4080_regs](rtl/ad4080_regs.sv)、[event_detect](rtl/event_detect.sv)、[gpio_ctrl](rtl/gpio_ctrl.sv) | 实体访问、CRC／状态、SDO使能、事件路由与拒绝原因有实现；器件全协议和电气仍需验证 |
| 顶层与代理 | [ad4080_top](rtl/ad4080_top.sv)、两个SAR封装和[cnv_ctrl](rtl/cnv_ctrl.sv) | 并行DSP加配置接口；cnv_ctrl是独立数字代理，不是差分接收器或SAR转换启动 |

无法从手册唯一确证的增益映射、字节顺序、地址／ID、短指令范围、写相SDO行为及补偿系数，由明确参数绑定或拒绝受影响的功能。参数存在／可保存不表示相应物理接口完成。

已删除的正向两点拟合不能恢复成默认校准器。真正的系统逆校准需以测得raw码与独立已知输入求系数，再验证定点应用；这与本工程上游有效C/Cf训练也不是同一任务。旧SDR串行候选删除后，结果数据PHY仍是未实现项。

## 复位与外部资格

硬复位、dsp_restart和sar_epoch_clear按适配层合同撤销状态；epoch clear同时使SAR系数失效并要求重载。配置SPI的软件复位是另一协议：仅在外部adc_idle真实为真且没有未完成捕获／读写活动时允许，重置配置并保留已完成捕获记录的读出会话。不可把adc_idle或interface_ready常绑1来绕过资格。

实际系统已验证三类controller来源：必要source边界缺失(error13)、coarse valid缺失(error7)、fine valid缺失(error8)；每类故障后4个真实good raw被拒，dsp_restart无法越过持续的上游fault，真实epoch clear＋完整系数重载恢复。核心仍继续转换／观察，fault不是模拟idle。正常停采→排空→park→独立idle ack后的非空捕获记录跨软件复位仍OPEN；native DSP的保记录正例不替代系统quiet条件。CAAZ＋DSP尚未启用验证。

## 怎样验证

```bash
# 从仓库根目录显式选择独立组件与库外新目录
python tools/run_open_rtl.py --tops result_fifo_tb --out ../sar-output/fifo-check-001
# 需要完整SAR绑定的系统检查另外提供 --inputs-dir 等明确输入
```

handoff003的15源SHA与当前模块一致，14个指定场景实际通过（FIFO含同bench的两个深度，不是14个独立顶层）；5个严格-Wall profile无warning／waiver。真实系统有185次完整20位比较、214次source握手、15448项检查，三个epoch恢复与三个restart阻断均有非空事件。原收据SHA256为`5daa8995a78ac9a34d51a09a4802099d3d6f6c37a0cc2f6575c7c84062a9b5f3`，复现所用输入与数学参数见[tooling](docs/tooling.md)。

整链数值对拍覆盖unity／OFFSET0与bypass。SINC1／SINC5和补偿FIR有各自独立数值oracle，但filter_sel1/2/3经过完整DSP链的数值路由尚未对拍；kernel正确不能替代选择器与链路组合验证。GPIO／事件检测的证据限native top中实际输入回读、释放、阈值路由与ALERT，不声称所有选择功能或CDC通过。[逐模块证据](../docs/digital_status_matrix.md#15个dsp扩展模块)分开登记。

`ad4080_dsp/sim/run_all.sh`只是同一显式组件runner的转发；不再代表默认7台或历史5/5集合。源、工具、参数、计数和断言范围以每次manifest为准。静态／文本工具用法见[tooling](docs/tooling.md)，注释与代码约定见[coding_standard](docs/coding_standard.md)。

[历史仿真](docs/verification_sim.md)、[旧BMC](docs/formal.md)、[ECP5记录](docs/synthesis_ecp5.md)和[完成度百分比](docs/status_completion.md)保留旧身份，不作为当前源通过。当前全局状态以[B表](../docs/digital_status_matrix.md)及最终新收据为准。
