# 八切片扰动及整体数字顶层的 SKY130HD 实现

`dither8_top` 实现八切片扰动开关控制、调度、DEM 掩码和同笔去扰元数据。`sar20_digital_top` 接入模块三 CAAZ 配对与控制，外部提供已重构码。`sar20_digital_lms_top` 再接入用片外训练系数进行的片上固定点重构，ADC2 码和模拟确认从端口输入。ADC 前级、采样开关、残差放大器、ADC2 与片外 LMS 训练各由对应模型或电路验证。[八切片合同](../eight_rtl/SPEC.md) · [整体顶层源码](../../task_integration/rtl/sar20_digital_lms_top.sv)

后端使用固定镜像的 Linux amd64 OpenROAD/Yosys/KLayout。顶层沿用模块一的 6.25 ns 单时钟 SDC、I/O 延时、5 fF 负载，以及 SKY130HD TT、25 °C、1.80 V 工艺角。前两版布局初值为 450 × 450 µm，核心框为 (20, 20) 至 (430, 430) µm。片上重构版增加系数存储和流水算术，按综合单元面积 33% 的初始核心利用率定方形底图，留 20 µm 边距。P&R 每顶层使用 4 线程；标准作业上限 40 分钟，片上 LMS 长时作业上限 150 分钟。[SDC](../../task1/physical/sky130_constraint.sdc) · [运行计划](plan.json)

已复跑的八片控制顶层，在上述 TT 角和时钟下 setup 裕量为 +0.154860 ns、hold 裕量为 +0.0631387 ns，公开 DRC 为 0。同一冻结 GDS 的独立 LVS：原版公开规则报告 **FAIL**，上游修订规则报告 **PASS**；两版原始日志及比对数据库均保留。原始运行因未产生 LVSDB 被自动标作 BLOCKED，外层复核清单已纠正。这是公开规则验证，不能替代目标工艺厂签核。[GDS、网表和原始报告](evidence/dither8_top_sky130hd_run_42265153017.zip) · [哈希与原始判定](evidence/dither8_top_manifest_42265153017.json) · [两版规则的 LVS 复核](evidence/dither8_top_lvs_replay_manifest_42270270897.json)

外接重构的整体控制顶层在同一条件下 setup 裕量为 +0.208478 ns、hold 裕量为 +0.0521769 ns，公开 DRC 为 0；同一 GDS 经独立复核，旧规则 LVS **FAIL**、上游修订规则 **PASS**。该顶层的 20 位重构码由外部提供，不包含片上 LMS 重构核。[GDS、网表和时序](evidence/sar20_digital_top_sky130hd_run_42265153017.zip) · [两版规则的 LVS 复核](evidence/sar20_digital_top_lvs_replay_manifest_42272620602.json)

含片上 LMS 的完整顶层，首次详细布线在 40 分钟运行上限时中止，最终 GDS／STA 未产生。消除 ADC2 常数除法后的新版已经通过功能对拍，映射后、未插缓冲且未提取布线的时序诊断为 setup −7.268336 ns、hold +0.034222 ns；最差是 557 个使能端点的高扇出路径。新版全局布线的估计 setup 为 −0.006 ns，但 40 分钟作业仍在详细布线中止，不能用该值冒充最终布线时序或 GDS。相同工艺与约束下的长时作业继续执行。[旧版超时记录](evidence/sar20_digital_lms_top_timeout_42262919977.json) · [新版超时记录](evidence/sar20_digital_lms_top_timeout_42270270956.json) · [映射时序诊断](evidence/sar20_digital_lms_top_mapped_manifest_42270594409.json) · [完整顶层运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42270381481)

旧版的 68 分钟后端作业曾完成首轮详细布线并报告 0 个路由标记，之后因 68 个天线违规进入修复和再次布线，在运行上限中止；该中间结果不是最终 DRC 或新版 RTL 的结论。[旧版长时诊断](evidence/sar20_digital_lms_top_timeout_42267364320.json)

在 Linux amd64 Docker 环境中，从干净的目标提交运行。输出目录需要为空。

```sh
bash task2/physical/sky130_run.sh /tmp/task2-dither8-sky130hd dither8_top
bash task2/physical/sky130_run.sh /tmp/task-integrated-sky130hd sar20_digital_top
bash task2/physical/sky130_run.sh /tmp/task-lms-sky130hd sar20_digital_lms_top
```

物理脚本先对照提交冻结 RTL、配置与 SDC，然后运行综合、预布线 STA、布局、CTS、路由、提取及 GDS。每个布局出口检查电源网实际图形、通孔和每个标准单元电源引脚。若优化插入的单元未绑定电源，沿用平台全局连接规则补接，核验后写回阶段数据库并记录前后哈希。抽取前检查填充后的数据库，最终 setup／hold 从同一份 ODB、SDC 和 SPEF 重开复核。独立 DRC／LVS 采用镜像所带的公开 deck，脚本保留退出码与报告，按实际运行结果判定。交付包记录 GDS 顶层、同一 GDS 交给 PV 的摘要、输入文件和全部产物的 SHA-256。

每个运行以 `moscow.json`、`PROVENANCE.json` 和 `release_manifest.json` 记录当前证据范围。目标 180 nm BCD、独立多角、流片方规则、EM/IR 与模拟验证分别验收。

首个八片布线基线来自 `9573d65`。布线后 setup 最差裕量为 −0.852274 ns，TNS −87.337 ns，139 个违例端点。hold 最差裕量为 +0.116048 ns。最差 setup 路径从转换样本 ID 寄存器进入扰动字择路，经命令调整到主阵列开关掩码。公开 KLayout DRC、详细路由 DRC 与天线违规均为 0。六个阶段保留实际电源引脚和几何检查记录。[GDS、网表、时序和物理证据](evidence/dither8_top_sky130hd_run_42261310056.zip) · [基线哈希](evidence/dither8_top_manifest_42261310056.json)

外接重构整体顶层的 `3af060e` 布线基线采用相同 SDC。setup 和 hold 最差裕量分别为 +0.256906 ns 和 +0.0513127 ns，TNS 与违例端点均为 0。公开 DRC、路由 DRC 与天线违规均为 0。[外接重构整体 GDS 与报告](evidence/sar20_digital_top_sky130hd_run_42262308310.zip) · [基线哈希](evidence/sar20_digital_top_manifest_42262308310.json)

基线 LVS 在合并 CDL 读取时停在非数值的 `short` 记法，状态为 `BLOCKED`。后续运行仅采用官方 SKY130HD CDL 的兼容拼写，把六处明确短接写为零欧姆，并去掉备用单元调用的七个分隔符。转换前后均核对官方 SHA-256，继续用相同 GDS 和 LVS 规则执行比较。[兼容收据脚本](prepare_lvs_cdl.py) · [ORFS 官方修订](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/commit/5eebdc0d06879cf674e86e3fb8efa2803e6e44dc)
