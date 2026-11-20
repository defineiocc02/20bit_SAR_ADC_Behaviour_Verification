# 动态单元匹配怎样选择物理电容

同一个名义码可以由多组等权电容实现。让不同物理单元轮流承担它，可以改变固定失配与输入码的相关性。ISSCC 图 9.8.3 给出横向、纵向选择与片组轮换。单元使用次数可以逐位核对；对 DNL、INL 和频谱的影响还取决于真实失配、扰动和采样条件。[论文图 9.8.3](../../references/ISSCC2024_PAPER.pdf#page=2)

| 对象 | 可据此核对的内容 |
| --- | --- |
| 生产数字 IP | 每笔从 18 片中选 8 片转换。每片的转换掩码有 16 个主位和 4 个子位。`dem_mask_v3` 按冻结相位和桥交换控制产生 `post`，Python 用 `post_mask` 和 `bridge_swap` 生成相同掩码。[RTL](../../rtl/dem_mask_v3.sv) · [Python 计算](../../src/adi_model/digital_conversion.py) |
| US10511316B2 图 11 的电荷实验 | 研究夹具按每片 17 个主位置、5 个子位置解释专利几何，显式调用 `fig11_masks`。用于逐码核算该几何里的电荷与单元选择。[研究入口](../../subsys/experiments/fig11.py) · [专利实施例](https://patents.google.com/patent/US10511316B2/en) |
| 同一专利的图 19、20 | 图 19 的 362 按正文解释为信号门。图 20 的保留输入、350/352 和 MUX 有单独的计数与映射。实施例中的每个元件是否存在于论文样片，须由该芯片披露确认。[图 19](figures/US10511316B2_FIG19_two_stage_shuffler.jpg) · [图 20 判读](fig20_cross_slice_mux.md) |

在生产掩码对照中，给定命令 283、桥交换关闭，每笔都选 35 个位置。重复同一个相位 64 次，有 29 个位置从未工作；遍历 64 对相位后，64 个位置各工作 35 次。这里测的是物理覆盖率。全码斜坡实验另用带失配的电荷模型评估 DNL/INL。[开关对照图与输入](../algorithms/README.md) · [全码结果及条件](../audit/TREE_AUDIT_REPORT.md#全码静态线性度)

早期行为池的覆盖率、误差和噪声对照保存在其原始记录。[历史实验全文](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/d97afa160a7c9c0ff2f1427656e5899b360771e2/docs/mechanisms/dem_fig11.md)
