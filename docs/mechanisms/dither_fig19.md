# 分路扰动怎样注入，又怎样扣回

ISSCC 2024 第 182 页说明，扰动用于配合 DEM 线性化 DAC，从量化器到 RDAC 的扰动范围增强了 2 bit。作者讲稿第 17 页画出两路输入以及量化结果到 RDAC 的调整。论文没有给出每个字对应的实际硅片电压、发生器递推或 ADC2 窗口。[论文第 182 页](../../references/ISSCC2024_PAPER.pdf#page=1) · [作者讲稿第 17 页](../../references/ISSCC2024_SLIDES.pdf#page=17)

## 专利图 19 在哪里加减

US10505561B2 图 19 的字源是 400。低段 `D5,D3,D2,D1` 经 300 加到量化器 200，全字经 222 加到采样 DAC 220，高段 `D6,D4` 经 224 从量化器结果中减去。正文的分数部分以七片为例，三个低位按 4、2、1 片分配。这些端点和极性应连在一起读。[原图](figures/US10505561B2_FIG19_dither_injection.jpg) · [专利正文及图 19](https://patents.google.com/patent/US10505561B2/en)

## 生产数字 IP 怎样完成同一笔转换

| 时刻 | 实际行为 |
| --- | --- |
| 采样请求受理 | 片内 xorshift32 从种子 `0xC001D00D` 推进一次，取新状态低六位。`dither_force_i=1` 时可指定测试字。字、片号和量化器号同时冻结。[字源和顶层接线](../../rtl/v21/adc_digital_ip_v21.sv) |
| 预置开关 | 定义 `f=4D3+2D2+D1`、`L=2D5+f/8`、`U=4D6+D4`。所选 SADC 接收 `L`，八片 RDAC 的完整预置平均为 `U+L`。八槽分配和正向采样极性是工程选择。[开关译码](../../rtl/dither_pre.sv) |
| 逐位转换 | 前级产生已决前缀，合法写入使用前缀加 `U`，最终命令为 `k=c+U`。DEM 选择实际开关，记录最后一次有效的 `post`。[事件状态图](../algorithms/digital.md#events) |
| 数字去扰 | 用同一笔实际片号、采样 `pre` 和标定表求 `Wp`；用 `post` 求 `Wm`。重构分子含 `+6Wm−6Wp`。外部可追到这笔字实际控制的电容以及相应的扣除量。[权重与重构](../algorithms/digital.md#reconstruct) |

强制字 53、差分输入 −0.6522216796875 V 时，`L=2.625`、`U=4`，SADC 粗码为 202，RDAC 命令为 206，ADC2 原码为 6016，最终得到 410304。该字漏扣一次采样权重，结果多 13,568 个输出码。专利七片实例与工程八槽的符号、量纲和适用条件，连同 64 个字的逐笔对照，见[原文与实测](../audit/AUDIT_ALGO_VS_SOURCE.md)及[开关测试](../audit/TREE_AUDIT_REPORT.md#dither-word-audit)。

此前的电荷口、码移实验和相应曲线可按原提交追溯。[历史实验全文](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/d97afa160a7c9c0ff2f1427656e5899b360771e2/docs/mechanisms/dither_fig19.md)
