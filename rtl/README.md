# RTL 导航

单芯片数字顶层是 [ad4080_chip_top](top/ad4080_chip_top.sv)。它连接 [ADC 转换核](sar_core/adc_digital_ip_v21.sv)、[DSP](ad4080_dsp/README.md) 与[外围](ad4080_periph/README.md)，共用一份四银行结果 SRAM。转换核的 ADC2 输入为 **13 位**，窗口为 **0.75 V**。接口和位宽见[工程合同](../docs/CONTRACT_CURRENT.md)，回归与后端状态见[项目状态](../docs/STATUS.md)。

整核综合、物理实验和 Formality 参考端共用 [files_adc_v21.f](filelists/files_adc_v21.f)。仿真入口见 [verify_v21.sh](../tools/verify_v21.sh) 和 [files_adc_v21_sim.f](filelists/files_adc_v21_sim.f)。[rtl_sources.f](rtl_sources.f) 用于核对全树源码覆盖，整链编译按 v21 专用清单选源。

## 转换核的模块边界

| 模块 | 源文件 | 顶层中承担的职责 |
| --- | --- | --- |
| D1 | [`sched18`](sar_core/sched18.sv) | 分配采集、转换与空闲片组。定向测试可走外部片组输入。 |
| D2 | [`sar_evt_v21`](sar_core/sar_evt_v21.sv) | 承接 Flash/SAR 事件，形成逐事件 RDAC 命令。 |
| D3 | [`dem_mask_v3`](sar_core/dem_mask_v3.sv) | 按本笔冻结的相位和 bridge 条件形成实际 `post` 掩码。 |
| D4 | [生产顶层](sar_core/adc_digital_ip_v21.sv) · [开关译码](sar_core/dither_pre.sv) | 每次受理从独立 xorshift32 取一个六位字。SADC 低段按 qid 写入七位寄存器，RDAC 八槽采样预置由同一字形成。 |
| D5 | [`wsum_v21`](sar_core/wsum_v21.sv) · [`recon_v21`](sar_core/recon_v21.sv) | 读取配置权重，计算权重和并定点重构。生命周期和错误口径见[配置合同](../docs/v21/CONFIG_CONTRACT.md)。 |
| D6 | [`acq_conv_ctx`](sar_core/acq_conv_ctx.sv) · [`adc2_capture`](sar_core/adc2_capture.sv) | 冻结采集与转换描述符，保持 ADC2 响应与样本标签配对。 |
| 仿真侧 | [`adc2_node_macro`](sar_core/adc2_node_macro.sv) | 用 DUT 快照生成行为级 ADC2 响应。生产综合清单排除此宏。 |

`recon_v21` 经 [`recip_rnd_v21`](sar_core/recip_rnd_v21.sv) 调用 [`recip_q60`](sar_core/recip_q60.sv)。后端报告按各自的综合顶层取面积，汇报整核面积时需要考虑这条包含关系。

v21 的 DEM 横纵相位、桥与半单元选择仍从事务入口提供。扰动字源位于生产顶层，`dither_force_i=1` 才使用外部定向字。SADC0 取 `sadc_pre_state_o[6:0]`，SADC1 取 `[13:7]`，各自使用 `sadc_pre_wr_o[qid]`。后到的真实 Flash 可由原有 `ev_flash_valid_i/code_i` 和 `ev_qid_i` 在首提交前配回本笔。[数字接口合同](../docs/CONTRACT_CURRENT.md)

## 组件与历史证据

其他可复用 RTL 服务于组件测试，如显式源绑定、定点算术和 DSP。早期顶层与重构链的验收结果依 Git 中的原提交解释。

RTL 问题与验证进展汇总在[复现状态](../docs/STATUS.md)。带环境和基线的结果从[审计与后端证据](../docs/audit/README.md)查阅。

从单笔样本核对各级寄存器和同沿优先级，见 [v21 算法流程与状态机](../docs/algorithms/digital.md)。
