# RTL 导航

当前唯一的集成 ADC 顶层是 [adc_digital_ip_v21.sv](v21/adc_digital_ip_v21.sv)。ADC2 输入为 **13 位**，窗口为 **0.75 V**。接口和位宽见[工程合同](../docs/CONTRACT_CURRENT.md)，回归与后端状态见[项目状态](../docs/STATUS.md)。

综合使用 [files_adc_v21.f](../flow/build/filelists/files_adc_v21.f)。仿真入口见 [verify_v21.sh](../tools/verify_v21.sh) 和 [files_adc_v21_sim.f](../flow/build/filelists/files_adc_v21_sim.f)。[rtl_sources.f](rtl_sources.f) 用于核对全树源码覆盖，整链编译按 v21 专用清单选源。

## v21 的模块边界

| 模块 | 源文件 | 顶层中承担的职责 |
| --- | --- | --- |
| D1 | [`sched18`](sched18.sv) | 分配采集、转换与空闲片组。定向测试可走外部片组输入。 |
| D2 | [`sar_evt_v21`](v21/sar_evt_v21.sv) | 承接 Flash/SAR 事件，形成逐事件 RDAC 命令。 |
| D3 | [`dem_mask_v3`](dem_mask_v3.sv) | 按本笔冻结的相位和 bridge 条件形成实际 `post` 掩码。 |
| D4 | [`dither_pre`](dither_pre.sv) | 从新事务的 dither 字得到采样预置与上扰动字段。 |
| D5 | [`wsum_v21`](v21/wsum_v21.sv) · [`recon_v21`](v21/recon_v21.sv) | 读取配置权重，计算权重和并定点重构。生命周期和错误口径见[配置合同](../docs/v21/CONFIG_CONTRACT.md)。 |
| D6 | [`acq_conv_ctx`](v21/acq_conv_ctx.sv) · [`adc2_capture`](v21/adc2_capture.sv) | 冻结采集与转换描述符，保持 ADC2 响应与样本标签配对。 |
| 仿真侧 | [`adc2_node_macro`](v21/adc2_node_macro.sv) | 用 DUT 快照生成行为级 ADC2 响应。生产综合清单排除此宏。 |

`recon_v21` 经 [`recip_rnd_v21`](v21/recip_rnd_v21.sv) 调用 [`recip_q60`](recip_q60.sv)。后端报告按各自的综合顶层取面积，汇报整核面积时需要考虑这条包含关系。

v21 由事务入口接收 DEM 相位，由 `sar_evt_v21` 实现事件跟随。编译清单和顶层源码分别定义输入依赖与实例关系。

## 组件与历史证据

其他可复用 RTL 服务于组件测试，如显式源绑定、定点算术和 DSP。早期顶层与重构链的验收结果依 Git 中的原提交解释。

RTL 问题与验证进展汇总在[复现状态](../docs/STATUS.md)。带环境和基线的结果从[审计与后端证据](../docs/audit/README.md)查阅。
