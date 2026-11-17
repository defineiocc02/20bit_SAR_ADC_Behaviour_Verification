# 项目状态与证据边界

工程合同采用 **v2 + v2.1**。ADC2 采用 **13 位、0.75 V** 窗口 `[-0.1875, +0.5625) V`。v21 顶层和默认 Python 模型使用相同 ADC2 码制，模型的开关网络及 Fig.19 扰动绑定有各自的假设。[工程合同](CONTRACT_CURRENT.md) · [模型图与配置](figures/20261117/README.md)

## 源码和回归

| 部分 | 证据与适用范围 |
| --- | --- |
| D1 调度 | `sched18` 的历史回归覆盖 4,096 拍逐位对比及 20,000 拍片选统计。机理图的 Python 镜像也与 4,096 行冻结向量逐行一致。 |
| D2 已决事件 | v21 用 `sar_evt_v21`。历史 I2 和 I1/I2 有逐事件验收。`follower_rt` 属于历史独立链。 |
| D3/D4 掩码与分路扰动 | 历史叶子 TB 对拍覆盖 133,376 个 DEM 组合与 512 个扰动组合。v21 实例化掩码和预置叶子，顶层相位由外部提供。历史独立 IP 的相位发生器及事件链另走自己的入口。 |
| D5 定点与权重 | 历史回归有重构、倒数、连续事务和任意权重场景。Python 冻结向量及整数 oracle 用于数值对照。仍有下文的 S3 缺陷。 |
| D6 归属与配置 | 历史 I3/I4B、配置生命周期和接口反例覆盖了捕获、释放、延迟返回和配置切换等选定场景。 |

`tools/verify_v21.sh` 登记 9 个 RTL 用例，以及整数证明与从 RTL 抽取的算术检查。运行结果以本次工具输出为准。VC Formal 的属性证明另行记录。[RTL 入口](../rtl/README.md) · [证明台账](v21/PROOF_LEDGER.md) · [D3/D4 旧 IP 验收](ACCEPTANCE_STATUS.md)

默认 Python 的 ADC2 量化步长按名义 `G=32` 折算到输入为 **0.50 LSB20**。13 位接口对齐是码制核对，不能替代模型网络、事务或模拟宏验证。当前静态模型的扰动绑定工况仍有 ADC2 越窗，图与清单保留计数。[F03/F05](figures/20261117/README.md)

## 综合、布局与形式等价

- **v21 整核。** 历史记录曾有旧 RTL 的整核网表。现有资料没有与当前 RTL 配套的整核网表、门级等价或签核结果。旧 Formality 记录的 20 个失败点对应旧网表。单模块在 25 ns 约束下的结果不足以证明整核 40 MS/s 吞吐。[综合阻塞记录](v21/SYNTH_STALL.md) · [旧 Formality 记录](v21/FORMAL_STATUS.md)
- **部分模块。** 归档后端汇总有 7 个独立模块记录 router DRC 为 0、Formality 为 `SUCCEEDED`，合计 2,189 个通过点。`dem_phase_rr`、`dem_phase_gen`、`follower_rt` 的结果属于模块，三者均未接入 v21 顶层。另有 2 个模块 router DRC 未归零、4 个在冻结输入和给定预算内未完成。DC 的 13 行包含 `recon_v21` 强制 alpha 旁路的诊断配置。[后端总表](audit/ALL_13_MODULES_FINAL.md) · [报告和存证](audit/README.md)
- **物理边界。** 模块记录使用推导技术 LEF，保留记录中的 DEF 无 PG 路由几何。尚无 foundry DRC、LVS、多角或 IR/EM 签核。早期面积算术和 160,951 µm² 与 D5 面积占比 95.8% 已撤回，原因是 `recon_v21 → recip_rnd_v21 → recip_q60` 层级重复计数。“四模块物理不可行”也已撤回。[复核更正](audit/ALL_13_MODULES_FINAL.md)

## 已知问题

`recon_v21.sv` 的 S3 用 `err3_q <= err2_q | (|x3_q[95:31])` 判断溢出。源级复核指出它会把低 32 位合法的负数误判为溢出，并把旧 `x3_q` 的判据与本笔 tag 配对。应增加相邻事务的定向验证。详细反例和待办状态见 [独立复核待办](audit/todo/README.md)。

## 设计依据

[A] ISSCC 2024 9.8 论文 p.182 和讲稿 p.29–35 给出 18 片池、8 片采集与 8 片转换、独立量化器、RDAC follower、DEM 与残差相位的依据。[论文与讲稿](../references/README.md)

[P] US10516408B2 对照独立量化器与 follower，US10511316B2 Fig.19–21 对照横纵选择和保留列，US10505561B2 Fig.19 对照分路扰动。[机制专题](mechanisms/README.md)

[E] 本工程定义 `3+3+3`、guard、桥接权重、ADC2 窗口与事务接口。现行 [U] 项与设计约束按[工程合同](CONTRACT_CURRENT.md)及[待办](audit/todo/README.md)追踪。历史结论可按原版追溯。[旧状态快照](_history/v21_status/STATUS_d5a1576.md)
