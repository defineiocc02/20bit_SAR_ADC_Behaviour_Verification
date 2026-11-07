# Dither：原文、生成和注入

本页将**原文披露**与**本项目工程选择**分开。完整原件见 [ISSCC 论文](../references/ISSCC2024_PAPER.pdf)、[ISSCC 讲稿](../references/ISSCC2024_SLIDES.pdf)和 [US10505561B2](https://patents.google.com/patent/US10505561B2/en#description)。

## 原文如何说？

| 来源与位置 | 原始短句 | 含义 |
| :--- | :--- | :--- |
| ISSCC 讲稿 PDF 第 17–18 页 | “Extra dither range”；“Improved linearity” | 将扩大扰动范围和改善线性度列为 RDAC 架构的效果；这不是单独开关实验的量化结论。 |
| ISSCC 论文 PDF 第 1 页、印刷第 182 页 | “Dither is used to complement the DEM linearization”；“the dither range is enhanced by 2b” | dither 用来补充 DEM 线性化；量化器到 RDAC 的扰动范围扩展 2 bit，**不是** ADC 有效精度提高 2 bit。 |
| 专利 PDF 第 26 页、印刷第 5 栏及第 33 页、印刷第 19–20 栏 | “The dither signal is formed in the digital domain by a pseudo-random number generator 40.”；“reduces the effect of DNL” | 说明数字伪随机字和减轻 DNL 的目的；正文另规定数字减法。没有给出唯一的 LFSR 多项式、种子或八片目标电路的开关表。 |

讲稿第 17 页的蓝色 lower 同时连到 SADC 采样、RDAC 采样以及 SAR 后／DEM 前的数字求和点；红色 upper 连到 RDAC 采样和同一数字求和点。同页写明“RDAC set directly to final state”，因此七片实验采用最终码设定，旧逐前缀规则作为单独诊断。图中的加号是框图符号，不能单凭它决定每个物理节点的电荷极性。讲稿第 31 页的 `P=50%` 属于另一处二进制到一元桥接示意，不是 Fig.19 生成器参数。讲稿第 32 页的 INL／DNL 是**整机测量**，没有给出相同条件下单独 dither 开关的消融。

## 一笔扰动如何生成和传递？

1. **生成并冻结字。** 专利允许数字伪随机数发生器产生字。本项目采用独立 PRBS 状态和非零种子，在采样事务登记沿生成六位字并连同 `sample_id` 保存；stall 不应使该笔的扰动字改变。字在登记沿之后才有效，因此数字登记必须早于实际采样孔径，并留出开关建立时间。具体发生器是工程选择，不冒充原文电路。
2. **七片实例拆分。** 以专利 Fig.19 中一个前级 LSB 为单位，令 `F=4D3+2D2+D1`、`u=4D6+D4`：前级 SADC 使用 `L=2D5+F/7`，RDAC 的总扰动为 `T=L+u`。六位全字进入 RDAC 路径；SADC 使用 `D5/D3/D2/D1`。
3. **粗码与 DEM。** 专利第 33 页正文规定：将 `u` 从 ADC 粗字中扣除，再用调整后的字设置 RDAC，即按该示例码约定 `k=c−u`。七片中的 `I=2,F=5` 可用两片取 2、五片取 3 来形成 `2+5/7`。示例没有自动证明所有边界开关状态可物理实现。
4. **后级与去扰。** 残差携带其余扰动并进入后级，数字结果按同一采样字及实际注入路径处理；实际开关位权重构和名义补偿不得重复消除。模拟输入符号、增益和节点连接须由模拟组签认。

**七片不能直接当八片。** `/7` 权重来自专利举例。当前目标的八个活动 slice、Fig.11 的非均匀码本与 Fig.21 跨段映射须重新解电荷，不能直接把 `/7` 改为 `/8` 并声称与原文等效。`I=7,F>0` 若需第 8 档，其开关可达性也待确认。

## 能证明什么？

- 原文指出扰动可减轻码边界造成的系统性误差，并补充 DEM 的 DAC 线性化；讲稿／论文未给该芯片独立的 dither 开关收益数字。
- [理想 ADC2 减性 dither 对照](../task1/evidence/adc2_dither/full20/README.md) 固定 DEM、LMS，仅改变理想细级扰动；它验证统计码密度机制，**不是** Fig.19 的实际采样电容注入。
- 目标八片的真实物理路径、2%／3% 固定失配＋LMS、逐前缀及 ADC2 过载、同笔补偿、后仿和芯片性能尚须单独验证。dither 不能恢复已过载的样本。

## 逐条台账在哪

本页只讲 dither 一条线。**原文每一句 → 代码哪行 → 哪个测试**的完整对应在 [`trace/source_matrix.csv`](../trace/source_matrix.csv)：专利 8 条（`PAT-01`…`PAT-08`）、论文 14 条（`PAPER-01`…`PAPER-14`）、讲稿 21 条（`SLIDE-01`…`SLIDE-21`）。专利原文（OCR 转写 + FIG.19 裁图，PDF 不入库）在 [`references/patent/`](../references/patent/)。

本页与台账的两处衔接，读的时候别错位：

- 上面写「令 `F=4D3+2D2+D1`、`u=4D6+D4`」，台账里 `F` 记作 `count = word & 7`（`PAT-02`）、`u` 记作 `upper = 4*(word>>5) + ((word>>3)&1)`（`PAT-05`），两者 `PASS`。
- 上面写「专利第 33 页正文规定 `k=c−u`」，台账 `PAT-05` 落的实现是 `command = coarse - dither.upper`。**注意 FIG.19 的 224 画的是 `u−c`**，与正文相反但物理等价（只差 RDAC 码加减约定），台账 `PAT-05` 的 `note` 与 [`references/patent/README.md`](../references/patent/README.md) 末节都记了这条张力，图的 ± 不作极性依据。

