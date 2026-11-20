# 项目状态与证据边界

当前生产数字 IP 的顶层为 [adc_digital_ip_v21.sv](../rtl/v21/adc_digital_ip_v21.sv)。ADC2 为 **13 位、0.75 V**，窗口为 `[-0.1875, +0.5625) V`。默认 Python 与本顶层使用同一套符号、字源、物理片号和定点出码规则。开关位序、码域和定点表示由同一份数字接口合同索引。[工程合同](CONTRACT_CURRENT.md) · [模型图与配置](figures/behavioral/README.md)

复现范围按分支和 Git 提交定位。软件包与硬件模块分别命名，后端参考端按本次检出的生产 filelist 读取。当前覆盖和历史结果的归属见[工作树实测](audit/TREE_AUDIT_REPORT.md#current-tree-review)。

## 源码和回归

| 部分 | 证据与适用范围 |
| --- | --- |
| D1 调度 | `sched18` 的历史回归覆盖 4,096 拍逐位对比及 20,000 拍片选统计。机理图的 Python 镜像也与 4,096 行冻结向量逐行一致。 |
| D2 已决事件 | v21 用 `sar_evt_v21`。历史 I2 和 I1/I2 有逐事件验收。历史独立链的 `follower_rt` 已退役。 |
| D3/D4 掩码与分路扰动 | 本地 Verilator 检查 133,376 种 DEM 掩码与 512 种 SADC/RDAC 预置。生产字源连续 1,024 笔覆盖全部 64 个字，实际 SADC 和 RDAC 开关产生的理想宏响应全部还原正确。[生成与去扰](audit/TREE_AUDIT_REPORT.md#dither-word-audit) |
| D5 定点与权重 | S3 对本笔的 X 做 32 位有符号截断检查，溢出时返回本笔的错误标志。本地 Verilator 运行了 8,209 笔冻结输入和 21 笔带标签事务，出码与标志全部符合期望。另有 6 个内部边界与错误传播探针通过。[重构回归记录](audit/todo/README.md#recon-s3) |
| D6 归属与配置 | 顶层接口测试的 124 项检查通过。A 释放、B 晋升、C 受理时，沿前和沿后采集与转换片选的交集都为零。另覆盖反压、空闲时提前预置、停收排空及恢复等待。[交接与重构的原报告复核](audit/ISSUE_10.md) |

`tools/verify_v21.sh` 登记 11 个 RTL 用例，以及整数证明与从 RTL 抽取的算术检查。运行结果以本次工具输出为准。VC Formal 的属性证明另行记录。[RTL 入口](../rtl/README.md) · [证明台账](v21/PROOF_LEDGER.md) · [D3/D4 旧 IP 验收](_history/d3d4/ACCEPTANCE_STATUS.md)

默认 Python 与生产 RTL 对五组共 1,792 笔逐标签比较，36 个记录字段全部匹配。该对拍由 `d97afa1` 的 CI 执行并通过。[该提交的运行记录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/44500594977) 固定输入组另核对实际 CLI 写出的完整 NPZ。每个 ADC2 步进折合名义输入 0.50 LSB20。Fig.11 研究模型的旧饱和记录保留在原工况。[可复现命令与原文差别](audit/AUDIT_ALGO_VS_SOURCE.md)

工作树的 CI 配置包含顶层展开、权重配置、连续采样与两项重构回归。21 笔逐标签对拍与 8,209 笔冻结向量已在本地 Verilator 运行。GitHub 历史运行按其提交解释。[CI 配置](../.github/workflows/ci.yml) · [本地复现入口](../flow/sim/README.md)

## 综合、布局与形式等价

- **v21 整核。** 历史记录曾有旧 RTL 的整核网表。现有资料没有与当前 RTL 配套的整核网表、门级等价或签核结果。旧 Formality 记录的 20 个失败点对应旧网表。单模块在 25 ns 约束下的结果不足以证明整核 40 MS/s 吞吐。[综合阻塞记录](v21/SYNTH_STALL.md) · [旧 Formality 记录](v21/FORMAL_STATUS.md)
- **部分模块。** 归档后端汇总有 7 个独立模块记录 router DRC 为 0、Formality 为 `SUCCEEDED`，合计 2,189 个通过点。`dem_phase_rr`、`dem_phase_gen`、`follower_rt` 的结果属于模块，三者均未接入 v21 顶层，原件随历史提交保留。另有 2 个模块 router DRC 未归零、4 个在冻结输入和给定预算内未完成。DC 的 13 行包含 `recon_v21` 强制 alpha 旁路的诊断配置。[后端总表](audit/ALL_13_MODULES_FINAL.md) · [报告和存证](audit/README.md)
- **物理边界。** 模块记录使用推导技术 LEF，保留记录中的 DEF 无 PG 路由几何。尚无 foundry DRC、LVS、多角或 IR/EM 签核。早期面积算术和 160,951 µm² 与 D5 面积占比 95.8% 已撤回，原因是 `recon_v21 → recip_rnd_v21 → recip_q60` 层级重复计数。“四模块物理不可行”也已撤回。[复核更正](audit/ALL_13_MODULES_FINAL.md)

## 持续采样率

**同一份生产顶层在理想宏握手下，已连续按 25 ns 间隔收样和出码。** 本地 Verilator 定向测试用真实调度器和完整的 360 字名义权重表。192 笔输入分为普通转换、保护门限拒绝后继续转换、保护门限接受三类，每类 64 笔。两组快时钟试验均得到 192 个有序标签和 192 个不同的正常 20 位码，所有相邻的 191 个采样、捕获和输出间隔均为 25 ns。[生产清单](../flow/build/filelists/files_adc_v21.f) · [端口测试](../tb/v21/sample_cadence.cpp)

| 内部数字时钟 | 要求的公共采样间隔 | 顶层实测 | 从采样到本笔最后一次数字开关写入，最慢值 |
| --- | --- | --- | --- |
| 25 ns（40 MHz，当前约束） | 25 ns | 第一笔余差到捕获时仍未获授权 | — |
| 1.5625 ns（640 MHz，理想时钟） | 25 ns | 192/192 笔，码与标志全部正确 | 17.1875 ns |
| 0.625 ns（1.6 GHz，理想时钟） | 25 ns | 192/192 笔，码与标志全部正确 | 6.875 ns |

**片内扰动闭环。** 正常端口 `dither_force_i=0`，实际受理字依次为 45、7、17、40。1,024 笔片内序列覆盖所有 64 个字，固定输入全部返回 410304。强制全字 192 笔及生产/强制混合 192 笔均逐标签正确。混合场景的受扰输入还跨越 Flash 分区和 512 门限。只断开理想 SADC 的低段，96/192 笔 ADC2 越窗。单一已知物理子权重的纠正使同批细码原有的 24 笔 ±32 码误差归零。顶层仿真使用真实数字输出引脚驱动理想 SADC 与 ADC2，宏的模拟建立和芯片实测仍需另行检验。[顶层试验](../tb/v21/sample_cadence.cpp)

**宏假设。** 每个逐位判决在指定数字沿前有效，捕获沿的 hold 确认同时到达，ADC2 在下一次公共捕获沿返回上一笔的 13 位原码。刻意撤去一次应到的原码后，上一笔仍占有返回槽，第 4 笔错过捕获。在 25 ns 主时钟下把公共周期放宽为 13 拍（325 ns）时，三类样本混合的 192 笔全部通过；缩为 12 拍时，第 2 笔的保护门限回退路径错过捕获。

**实现预算。** 普通事务至少占一个启动沿和七个提交沿，单计事件级在 25 ns 时钟下最多 5 MS/s。最后开关写入后，还要登记权重和、捕获余差、等待两个安静周期并交接片组。论文描述前级转换约 7 ns。表中的 6.875 ns 只量到 RTL 数字写门，模拟宏接收命令和实际开关建立还需时间。[事件与安静窗](../rtl/v21/acq_conv_ctx.sv) · [捕获门控](../rtl/v21/adc_digital_ip_v21.sv) · [论文原文，第 182 页](../references/ISSCC2024_PAPER.pdf)

**物理实现待验收。** 当前顶层已提供两套 SADC 的低段预置与写使能。下一步要确定比较判决最迟到达、RDAC 写入及建立、ADC2 返回和 hold 的时限，再核对本版从判决到物理开关寄存器的路径。旧网表曾报告事件序号寄存器至开关寄存器的单段到达时间为 2.839 ns。640 MHz 和 1.6 GHz 是本次零延迟仿真的激励，现有 25 ns 时钟约束和归档时序均不能签署这些频率。[时钟约束](../flow/synth/v21/dc_synth_v21.tcl) · [旧网表路径 ②](_history/v21_legacy/TIMING_BUDGET_FINAL_superseded.md)

使用本机 Verilator 和 clang++，可用 `VERILATOR` 指定工具路径。在仓库根运行可重建十二个场景，输出目录内的 `summary.csv` 列出成功与错过的捕获，`samples.csv` 保存每笔的实际时钟沿、标签、原码和出码。输入源码及清单随同保存。

~~~bash
rate_dir="$(mktemp -d)"
bash tools/pyenv.sh --run tools/run_open_rtl.py --simulator verilator \
  --tops v21_sample_cadence --out "$rate_dir/results"
cat "$rate_dir/results/v21_sample_cadence/summary.csv"
~~~

## 设计依据

[A] ISSCC 2024 9.8 论文 p.182 和讲稿 p.29–35 给出 18 片池、8 片采集与 8 片转换、独立量化器、RDAC follower、DEM 与残差相位的依据。[论文与讲稿](../references/README.md)

[P] US10516408B2 对照独立量化器与 follower，US10511316B2 Fig.19–21 对照横纵选择和保留列，US10505561B2 Fig.19 对照分路扰动。[机制专题](mechanisms/README.md)

[E] 本工程定义 `3+3+3`、guard、桥接权重、ADC2 窗口与事务接口。现行 [U] 项与设计约束按[工程合同](CONTRACT_CURRENT.md)及[待办](audit/todo/README.md)追踪。历史结论可按原版追溯。[旧状态快照](_history/v21_status/STATUS_d5a1576.md)
