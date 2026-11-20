# 数字顶层的引脚与片上实现证据

当前生产源清单有 11 份 SystemVerilog，含顶层、8 种直接实例和 2 种传递依赖。顶层声明 90 个命名端口，45 个输入、45 个输出。端口数按名称计，一条多位总线计一个端口。[顶层与实例清单](../../rtl/README.md) · [端口声明](../../rtl/v21/adc_digital_ip_v21.sv)

## 论文中的功能怎样跨过宏边界

| 功能 | 数字顶层实际交接的信号 | 对应关系 |
| --- | --- | --- |
| 前级判决与物理片组 | 接收宏的 Flash 和逐位判决，以 `acq_sel_o/conv_sel_o/idle_sel_o` 选 18 片中的采集、转换和备用组。 | 论文披露量化器与 RDAC 分开及 8/8/2 调度。具体引脚与握手由工程定义。 |
| 动态单元匹配 | `dem_mask_v3` 按本笔相位、桥交换条件和已决码形成 `rdac_post_state_o`，由 `post_wr_o` 控制写入。 | 横纵打乱有论文依据。两维相位与 coin 由上游提供。[掩码和状态](../algorithms/digital.md#dem) |
| 分路扰动 | `sadc_pre_state_o` 和 `sadc_pre_wr_o` 写选中的 SADC，`rdac_pre_state_o` 和 `pre_wr_o` 给实际采样片组。生产时片内生字。 | 论文披露分路与两位范围增强，六位字递推和八槽映射由工程定义。[原文及实测](AUDIT_ALGO_VS_SOURCE.md) |
| ADC2、权重和输出 | `adc2_raw_i[12:0]` 接收细码，片上根据已加载权重出 `out_code[19:0]`、`out_tag` 和状态。 | 论文披露 20 位及片上修正。ADC2 采用工程选定的 13 位、0.75 V 窗口，顶层固定 `alpha=1`。 |
| 装载、持续采样 | 五个写入字段共 52 位，另有提交、重装、状态、代号和偏置。快时钟和理想宏按约响应时，顶层按 25 ns 间隔连续输出 192 笔。 | 宏实际建立时间与同版整核物理时序仍待验收。[装载时序](../v21/CONFIG_CONTRACT.md) · [持续采样率与条件](../STATUS.md#持续采样率) |

## 后端归档证明到了哪一步

2026-11-17 的 13 个模块逐项列在[后端原始汇总](ALL_13_MODULES_FINAL.md)。其中 7 个有 router DRC 为 0 且 Formality 成功的记录，2 个布线后 router DRC 分别为 2 和 27，4 个在相应输入与预算下未完成。两个倒数模块是重构模块的下级，独立层级的面积会重复计入。归档中的 `recon_v21` 面积来自强制 alpha 旁路的诊断配置。

那 7 项中的 `dem_phase_rr`、`dem_phase_gen`、`follower_rt` 是历史独立模块。生产顶层的已决事件由 `sar_evt_v21` 处理，DEM 由 `dem_mask_v3` 实现。已有数据能说明哪些运行通过、哪些停在哪一步；整核能否在目标工艺闭合，需要同一版网表、约束、物理实现和签核结果。[整核与模块证据](../STATUS.md) · [生产编译清单](../../flow/build/filelists/files_adc_v21.f)

从仓库根逐项核对端口数、编译入口及历史结果。

```bash
rg -c '^\s*input\s+logic\b' rtl/v21/adc_digital_ip_v21.sv
rg -c '^\s*output\s+logic\b' rtl/v21/adc_digital_ip_v21.sv
rg '^rtl/.+\.sv$' flow/build/filelists/files_adc_v21.f
rg 'SUCCEEDED|router DRC|未完成' docs/audit/ALL_13_MODULES_FINAL.md
```
