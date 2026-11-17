# SMIC 180 nm 历史试验

这里保存早期重构候选及对应综合脚本。旧测试台可用于追溯候选接口。当前 RTL 路线从 [RTL 索引](../../../rtl/README.md) 进入。

| 保留项 | 对应记录 |
| --- | --- |
| [recon_d5_pipe.sv](recon_d5_pipe.sv) 与 [recon_d5_pipe2.sv](recon_d5_pipe2.sv) | 早期流水候选。对应 [pipe 测试台](../../../tb/tb_recon_pipe.sv)、[pipe2 测试台](../../../tb/tb_recon_pipe2.sv) 和 [时序记录](../../../docs/_history/TIMING_PIPE2.md)。 |
| [dc_w2.tcl](synth/w2/dc_w2.tcl) | 旧工具和工艺库的实验脚本。脚本含历史环境配置，移植前需重绑定。 [当年运行记录](../../../docs/_history/TIMING_W2.md) |
| [D5 参考与旧 RTL](../../../docs/_history/D5_PROD_VS_RTL.md) | 定点重构与倒数实现的比较。 |

历史面积、slack 和优化倍数限定在对应工艺、约束和网表。原始报告名及当年条件以链接中的历史记录为准。当前实现的证据见 [项目状态](../../../docs/STATUS.md)。
