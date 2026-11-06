# 模块二 · 分路 dither

**计划交付。** 本模块按 US10505561B2 Fig.19 建立前级 ADC 和采样 RDAC 的实际扰动路径。模块一的理想细级减性 dither 已给出码密度对照，可用于分离量化器效应。两项实验各自保留明确的注入节点和数字去除条件。 [Fig.19](https://patents.google.com/patent/US10505561B2/en#description) · [现有对照](../task1/evidence/adc2_dither/README.md)

- 冻结六位扰动字、前级与 RDAC 的分配、真实电荷极性、粗码修正和 ADC2 输入量程。将七片专利示例迁移到八片活动组时，按实际电容网络重算两路电荷。验证同一笔样本的扰动只消除一次。
- 交付 Python 电荷参考、可综合 RTL 和同笔对拍。覆盖 64 种扰动字、前缀合法性、边缘输入、复位与过载。保存 LMS 独立训练和留出数据，比较 dither 开关时固定失配下的 20 位 INL／DNL、频谱和余量。 [校准对照口径](../task1/EXPERIMENT_STANDARD.md)
- 接入模块一后交付 SKY130HD GDS、抽取后 STA 报告、源码身份和复现命令。目标工艺与真实模拟余量的联合验证遵循项目规格。 [交付规格](../task1/SPEC.md)

原工程的静态等效模型及夹具可用于接口对照。 [旧 Fig.19 模型](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/bc9a16a596dd37d1172d29cbda23979c222bf63b/src/adi_model/dither.py) · [旧绑定夹具](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/bc9a16a596dd37d1172d29cbda23979c222bf63b/sim/fixtures/fig19_ideal_ports)
