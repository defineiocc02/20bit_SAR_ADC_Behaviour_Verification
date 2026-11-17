# RTL 导航

先按目标选顶层和源清单。接口和位宽以[工程合同](../docs/CONTRACT_CURRENT.md)为准，回归与后端进度见[复现状态](../docs/STATUS.md)。

## 顶层与入口

| 路线 | 顶层与用途 | 入口 |
| --- | --- | --- |
| v21 数字集成 | [`adc_digital_ip_v21`](v21/adc_digital_ip_v21.sv) 接入片组调度、逐事件跟随、DEM、分路 dither、重构与 ADC2 事务。 | [v21 验收脚本](../tools/verify_v21.sh) · [综合清单](../flow/build/filelists/files_adc_v21.f) · [仿真清单](../flow/build/filelists/files_adc_v21_sim.f) |
| D3/D4 单独交付记录 | [`d3d4_ip`](_legacy/tops/d3d4_ip.sv) 保留相位发生、跟随、片组选择与两个叶子模块的独立组合。 | `make -f flow/Makefile verify-d3d4` 验叶子，`make -f flow/Makefile verify-d3d4-ip` 验旧 IP 组合。见 [Makefile](../flow/Makefile) · [接口说明](../docs/D3D4_IP_INTERFACE.md) · [独立 IP 清单](../flow/build/filelists/files_d3d4_ip.f)。 |
| 早期数字链 | [`digital_top_w`](_legacy/tops/digital_top_w.sv) 保留旧权重重构路线，[`digital_top`](_legacy/tops/digital_top.sv) 是更早的组合骨架。 | [旧权重链脚本](../tools/run_topw.sh) · [旧权重链清单](../flow/build/filelists/files_topw.f) · [早期混合清单](../flow/build/filelists/files.f) |
| 独立 RTL core | [`sar20_digital_core`](top/sar20_digital_core.sv) 与 [`sadc_enc`](top/sadc_enc.sv) 使用 [`core/`](core/) 的结构化模块，有单独的输入绑定和仿真路径。 | [开放仿真脚本](../tools/run_open_rtl.py) · [验证层说明](../flow/verification/README.md) · [AD4080 DSP 子系统](../subsys/ad4080_dsp/README.md) |

详细编译范围见[文件清单导航](../flow/build/filelists/README.md)。[`rtl_sources.f`](rtl_sources.f) 列出 `rtl/` 全树的 SystemVerilog 源，供[源码清单测试](../flow/tests/unit/test_review_trace.py)核对。编译按上表选用对应清单或驱动脚本。

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

v21 由事务入口接收 DEM 相位，由 `sar_evt_v21` 实现事件跟随。[`dem_phase_rr`](dem_phase_rr.sv)、[`dem_phase_gen`](dem_phase_gen.sv) 与 [`follower_rt`](follower_rt.sv) 由旧 IP 使用，也保留各自的模块验证记录。编译清单中的文件与顶层的实例化关系分别以清单和顶层源码为准。

## 目录里另外几类文件

- 根级 `sched18.sv`、`dither_pre.sv`、`dem_mask_v3.sv` 和 `recip_q60.sv` 是 v21 与专项流程复用的叶子。
- `cdac_decode.sv`、`dem_shuffle.sv`、`dither_split.sv`、`follower.sv`、`slice_pool.sv` 和 `recon_d5*.sv` 服务早期顶层或单项测试，定位调用时应同时看 [`tb/`](../tb/) 和[文件清单](../flow/build/filelists/README.md)。
- [`params/rtl_error_codes.vh`](params/rtl_error_codes.vh) 是独立 core 的错误码头文件。`calib_regs`、`sar_structural_ctrl` 和 AD4080 DSP 的系统测试台均有引用。

RTL 问题与验证进展汇总在[复现状态](../docs/STATUS.md)。带环境和基线的结果从[审计与后端证据](../docs/audit/README.md)查阅。
