# 历史 FPGA 综合、布线与时序记录

这次结果对应早期独立 ADC 数字核的 `71e7d5a` 冻结源包。工具为 Vivado 2018.3，器件为 `xc7vx690tffg1761-2`，使用 P5 和 25 ns 核心时钟。布线后的内部寄存器 setup／hold 为 +0.946／+0.052 ns。全 OOC 路径的 hold 为 −2.278 ns，外部接口时序仍未闭合。

原始过程、DCP 身份和命令见 `git show 674b0d8:flow/synth/FPGA_SYNTHESIS_AND_STA.md`。原始报告目录可通过 `git ls-tree -r --name-only ca5ae31 -- docs/evidence/20260930` 查询。当前 v21 的综合状态见 [项目状态](../../docs/STATUS.md)。
