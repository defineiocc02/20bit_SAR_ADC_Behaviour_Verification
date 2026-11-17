# 当前工程合同

**v21 是当前唯一支持的集成 ADC，ADC2 为 13 位、0.75 V。** v2 正文和 v2.1 补充共同定义其工程合同。相同条款有冲突时以 v2.1 为准。[v2 正文](ISSCC2024_ONCHIP_CONTRACT_v2.md) · [v2.1 补充](ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md)

| 主题 | 工程口径 |
| --- | --- |
| 码域 | SADC 粗码 `c=0…519`，RDAC 命令 `k=c+U`，合法域 `0…520`。普通区用 `k=64H+8V+L`，其余按 v2.1 的 guard 掩码。 |
| DEM 和桥接 | H、V 各在 0…7，保留列 `(7+h_phase)%8`。仅 `coin=1` 时，按 `v_phase` 和 `half` 交换选定主半单元与八片对应子支路。 |
| 定点权重 | 按同一笔 `pre` 和最终 `post` 计算 `Wp`、`Wm` 和实际 `G`。`Wp` 对置位的子支路加权求和，权重已经包含 `β` 与 `1/Cf`。 |
| 名义电容与增益 | 8 片主阵列共 `512 Cu`，`Cf=16 Cu`，名义 `G=32`。该值用于名义算例，逐笔重构使用实际权重。 |
| ADC2 | `13 bit`，差分窗口 `[-0.1875, +0.5625) V`，跨度 `0.75 V`。Q24 码箱中心为 `Y=-3145728+1536·raw+768`。 |
| 事务 | 常规粗量化事件为 Flash + 6 次 SAR，最高区间走单独 guard 路径。事务冻结相位、扰动字和样本标识。相位输入由 v21 外部提供，实际槽位和握手见顶层。 |

完整掩码、位序、数据格式及合法域以合同正文为准。v21 配置寄存器与生命周期见[配置合同](v21/CONFIG_CONTRACT.md)，实现和验证范围见 [RTL 导航](../rtl/README.md)和[状态表](STATUS.md)。

整链回归入口为 [verify_v21.sh](../tools/verify_v21.sh)。共享 D3/D4 模块的验证结果按各自的码域、掩码和输入版本解读。[历史 D3/D4 验收记录](ACCEPTANCE_STATUS.md)

早期 RTL 结果按 Git 中的原始版本解释。Python 的 Fig.19 减法等效实验按自身配置解释，适用范围见 [F05](figures/20261117/README.md)。[历史评审](_history/CONTRACT_V2_REVIEW.md) · [历史物理草案](_history/PHYSICAL_CONTRACT_DRAFT.md)
