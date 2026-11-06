# 模块三 · CAAZ

**计划交付。** 冻结实际斩波极性、CBIAS 更新／保持及捕获确认。两笔有效且归属一致的连续样本进入一组非重叠配对，10 MS/s 原始样本对应 5 MS/s 配对输出。模拟建立和真实噪声效果需要独立验证。 [北大 VLSI 2025](../references/PKU_VLSI2025_C32_1_PAPER.pdf) · [作者讲稿](../references/PKU_VLSI2025_C32_1_SLIDES.pdf)

- 交付 Python 样本域参考、可综合周期控制及数字解调，冻结有符号码值、配对次序和位宽舍入约定。用连续样本号、斩波标签和 AZ 代号识别同一对，覆盖缺样、非法捕获、错配和复位。
- 对拍模块一和模块二的真实输出。交付斩波时序、正常与异常配对波形，以及同输出速率和相同分析带宽下的噪声与失调对照。固定失配结果同时报告 LMS 独立训练和留出验证。 [校准对照口径](../task1/EXPERIMENT_STANDARD.md)
- 完成 SKY130HD 综合、布局布线、GDS、抽取后 STA 和复现命令。目标工艺、真实模拟宏和后仿按项目规格另行验收。 [交付规格](../task1/SPEC.md)

原工程已存的控制与配对原型仅作接口参考。 [周期控制原型](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/bc9a16a596dd37d1172d29cbda23979c222bf63b/rtl/control/caaz_cycle_ctrl.sv) · [配对原型](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/bc9a16a596dd37d1172d29cbda23979c222bf63b/rtl/calibration/caaz_pair_processor.sv)
