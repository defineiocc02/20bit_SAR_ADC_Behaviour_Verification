# 模块二｜Dither 生成、注入与效果

当前统一为 `k=c−U`、`conv_sub+sample_sub`。新数据、效果图与逐笔 RTL 记录见
[负注入交付](evidence/eight_negative/README.md)；旧 `eight_native` 的图和 GDS 只作历史记录。
负注入以显式受控电荷源建模，并假定与转换共享子权重，真实参考开关尚未绑定。

![负注入模型的 20 位线性度](evidence/eight_negative/eight_native_linearity.png)

![负注入模型的频谱](evidence/eight_negative/eight_native_spectra.png)

同池 DEM／LMS 条件下，dither 对不同指标有改善也有退化，完整数字见新结果页。
共同静态输入窗为 ±3.671875 V；ADC2 理想 12 位、输入折算 ±100 mV。
全幅前端平均拒绝宽度为 0.0723805 V，不能把共同窗的结果当作全幅无缺码证明。

与 CAAZ 联合时，本工程将扰动注入定义在**输入斩波之后**的采样坐标中，每笔先按自己的开关位图去扰、重构，再解调配对。若模拟电路将扰动接在斩波之前，奇数笔的注入极性会改变，必须重新定义开关与数字补偿；实际接线须由模拟组确认。

## 原文说了什么？

| 来源 | 原始短句 | 作用 |
| :--- | :--- | :--- |
| ISSCC 讲稿第 17–18 页 | “Extra dither range”；“Improved linearity” | 两路扰动扩大 RDAC 可用范围，辅助线性化。 |
| ISSCC 论文第 182 页 | “Dither is used to complement the DEM linearization” | dither 补充 DEM 的 DAC 线性化；原文所说范围扩展 2 bit 不等于 ADC 精度提高 2 bit。 |
| US10505561B2 | “pseudo-random number generator”；“reduces the effect of DNL” | 用数字伪随机字产生扰动，使码边界误差在统计上更平滑。 |

[原始句子、页码和完整说明](ORIGINAL_SOURCES.md) · [讲稿原件](../references/ISSCC2024_SLIDES.pdf#page=17) · [专利正文](https://patents.google.com/patent/US10505561B2/en#description)

## 字怎么生成、怎样注入？

![PRBS31 六位字生成、样本绑定与词频](figures/dither_prbs31.png)

这是本项目的工程选择，不是论文指定的发生器。PRBS31 的 31 位状态在使能且接受一笔采样事务时推进 6 次，产生 `D6…D1` 并登记样本 ID；禁用时受理输出零字、保持状态。零种子或与采样同沿的种子写入被拒绝。数字登记沿**必须早于实际模拟采样孔径**，留出电容开关建立时间。连续 131,072 个有效字的实测频数和逐拍对拍见 [发生器收据](evidence/dither_rng/result.json)。

![ISSCC 讲稿中的双路扰动路径](figures/dither_isscc_ppt.png)

讲稿中 lower 同时进入 SADC 与 RDAC，upper 进入 RDAC，两路信息也参与 DEM 前的数字合成；图上加号不独自确定物理电荷极性。专利七片例明确的位分配如下：

![七片实例的六位分配及数字去除](figures/dither_fig19.png)

以一个前级 LSB 为单位，`m=4D3+2D2+D1`，低路为 `L=2D5+m/7`，高路为 `u=4D6+D4`，总扰动为 `L+u`。专利正文的码域命令是 `k=c−u`；实际采样电荷和最终去扰必须绑定同一笔字。**不能直接把七片的 `/7` 改成目标八片的 `/8` 并称作原文结论。**

## 七片示例：开关效果与代价

![七片工程示例：dither 前后的 20 位 DNL 和 INL](evidence/fig19_seven/fig19_seven_linearity.png)

这组隔离实验使用七片等值电容、理想 12 位 ADC2、无电容失配和热噪声；静态比较仅覆盖 ±3.671875 V，所有 64 字等权。`m=7` 全片升档是模型约定，边界档位尚须验证。它用于验证 Fig.19 的扰动路径，**不是目标八片 ADC 的实测结果**。

| 同一条件 | dither 关闭 | dither 开启 |
| :--- | ---: | ---: |
| 评估窗口内 DNL 均方根 | 2.030 LSB20 | 0.429 LSB20 |
| 评估窗口内零宽码 | 619,642 | 0 |
| 相干正弦 SFDR／SNDR | 123.30／104.57 dB | 128.47／104.36 dB |

静态码密度和 SFDR 改善，单次量化误差及 SNDR 没有因此改善。只保留低路中的分数部分 `m/7`，本例的输出逐笔相同：所示改善来自亚 LSB 相位变化；高路对非理想 RDAC 的收益仍待验证。高路扩大采样与 RDAC 码域范围；本例两组 ADC2 残差均为 0～19.53 mV，实际电容失配下仍需核对残差量程。本例的满幅拒绝统计和前缀边界见[频谱、残差、数据与复现](evidence/fig19_seven/README.md)。

另一组在**理想 ADC2 输入**加减性 dither 的实验使用 2%／3% 固定失配、Fig.21 DEM 和冻结 LMS：[20 位开关对照](../task1/evidence/adc2_dither/full20/README.md)。它不等同于七片真实采样支路，也不能说明目标八片的物理注入已实现。

## 本阶段交付与下一步

- **已有：**`rtl/dither_word_gen.sv`、[八片联合 RTL](eight_rtl/rtl/dither8_top.sv)、[两节点行为模型](eight/model.py)、训练／频谱／静态图和独立电荷检查；[七片示例](model/fig19_seven.py)保留作原文机制对照。
- **待模拟验收：**实际辅助 DAC、逐片子电容开关、参考极性与建立、ADC2／RA、电荷寄生和 PVT。八片位图与原生步长是基于现有 Fig.11 结构的工程推导，不能宣称已复现原芯片。
- **物理交付：**包含模块二和片上重构的整体数字顶层已有 SKY130HD GDS、TT 角 STA 及公开 DRC／LVS 证据，见[物理记录](physical/README.md)；未来 180 nm BCD、多角和模拟宏后仿仍需单独验收。

```sh
python -m pip install -r task1/requirements-calibration.txt
PYTHONPATH=.:src python -m pytest -q task2/tests
PYTHONPATH=.:src python task2/tools/run_dither_word_checks.py --out /tmp/task2-dither-run-001
PYTHONPATH=.:src python -m task2.experiments.fig19_seven_demo --output /tmp/task2-fig19-run-001
```
