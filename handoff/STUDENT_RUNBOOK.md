# 六个月接手实操：每个模块走完模型、RTL、GDS

此手册配合[任务书](NINE_MONTH_PLAN.pdf)使用。所有命令从学生分支仓库根目录执行；先固定 Git 提交、Python／依赖、Verilator、工具镜像及约束。下面的 `/tmp/...` 是示例，须为每次运行更换全新目录，不覆盖历史收据。行为效果图可以先引用原归档；源码、参数或输入变化时重跑受影响部分。

**共同验收顺序：**①说明原文机制和本工程选择；②行为模型与独立参考核对，提交对照图；③同笔整数／RTL 比较，覆盖正常、复位、stall 和非法输入；④冻结顶层、源文件、库和 SDC，运行综合、版图、提取、STA、DRC／LVS；⑤交 GDS、网表、SDC、SPEF、工具报告及哈希。每项分别标 PASS／FAIL／BLOCKED／NOT_RUN，不能用旧版 PASS 代替本版证据。各模块的 I/O 时序、时钟、PVT／RC 角与实际电源约束须先冻结；本工程 6.25 ns 是目标，不是原文规定。

## M1：模块一——切片调度与 DEM

**原理与起点。** ISSCC 论文 Fig.9.8.1／3 讲 18 片池和一元电容重排；US10511316B2 Fig.11／20／21 是电容及跨片映射依据。当前 [Fig.21 合同](../task1/BRIDGE_SPEC.md)中的 β、切轨和补偿属于工程前提；[模块一说明](../task1/README.md)与 [RTL 顶层](../task1/rtl/module1_bridge_top.sv)为已有实现，不能宣称完整原型 3D DEM。

1. **行为：**先看 [2%／3% 原始数组及效果图](../task2/evidence/eight_negative/README.md)，从归档补齐无 DEM／无 LMS、仅 DEM、仅 LMS、两者均开四组逐码 INL／DNL 和频谱；2% 与 3% 各自固定同一物理池和切片序列。LMS 映射模式分别训练，留出数据独立。只改变绘图不重训；增加制造种子时才新增实验。交角色时序、使用统计、四组对照和训练／留出图，注明共同窗 ±3.671875 V、LSB20 和有限状态平均。
2. **RTL：**运行下列检查；保存测试数、样本／事件数和日志。全部合法事件掩码、ID、补偿与独立参考零差异；非法 ID／复位／stall 按合同处理。

```sh
PYTHONPATH=.:src python -m pytest -q task1/tests/test_fig21_bridge.py
PYTHONPATH=.:src python task1/tools/run_bridge_checks.py --out /tmp/student-dem-rtl-001
```

3. **GDS：**以 `module1_bridge_top` 为顶层，按 [后端说明](../task1/physical/README.md)在 Linux amd64＋Docker 运行；输出目录必须空。已有 TT GDS 是历史版本，先查源及约束哈希，不能未经核对复用。该脚本设置 `LEC_CHECK=0`，**不会自动证明 RTL 与网表等价**；必须另做等价并提交报告。旧模块一独立 DRC／LVS 标为未运行，需补做。

```sh
bash task1/physical/sky130_run.sh /tmp/student-dem-gds-001 module1_bridge_top
```

交版图全景、最差 setup／hold 路径图及原始报告。效益数字不得因未达预期而换池、换窗口或只选最好种子。

## M2–M3：模块二——八片 dither

**原理与起点。** US10505561B2 Fig.3／5／19 与 ISSCC 讲稿第 17 页说明扰动和数字去扰；[数字合同](../task2/eight_rtl/SPEC.md)的七片到八片分配、负注入接法与 Q 格式是工程选择。[八片模型](../task2/eight/model.py)、[RTL](../task2/eight_rtl/rtl/dither8_top.sv)及 20,000 拍历史对拍已经存在。

1. **行为：**先引用 [无 LMS 与冻结 LMS 的开关图和数据](../task2/evidence/eight_negative/README.md)，同池、同输入、同 DEM 和同窗口，分别看 2%／3% 的 INL／DNL、频谱及零宽码。第 2 个月另交 lower／upper 和同笔时序图；第 3 个月新增 ±5 V 的拒绝率／ADC2 超窗图。现有共同下限 −4.855239 V，不能声称全幅已通过。64 字及八槽逐项检查，不以最终码限幅掩盖内部过载。
2. **RTL：**按以下命令运行，检查同笔 ID／掩码、`c<U`、复位、种子及 stall；逐事件与参考一致。冻结的历史 [负注入收据](../task2/eight_rtl/independent_review/negative/result.json)可作基线。

```sh
PYTHONPATH=.:src python task2/eight_rtl/tools/run_checks.py --out /tmp/student-dither-rtl-001
```

3. **GDS：**[现有流程](../task2/physical/README.md)支持 `dither8_top`；输出目录必须空。此顶层已包含模块一调度／DEM，其面积不能与模块一宏简单相加。旧版 GDS、TT 时序及 DRC／LVS 的记录不能覆盖负注入当前版；另检查等价、多角、PG／IR／EM。

```sh
bash task2/physical/sky130_run.sh /tmp/student-dither-gds-001 dither8_top
```

交版图与 setup／hold 图。数字宏验证与 ±5 V 系统余量分别打状态：内部越界则全幅验收 FAIL。

## M4：模块三——CAAZ

**原理与起点。** 北大 VLSI 2025 论文 Fig.5／6 说明偶笔 AZ、奇笔保持和换相；当前[数字合同](../task3/SPEC.md)是逐笔先重构去扰、再偶减奇，工程选用非重叠 10→5 MS/s。原论文的输出速率不能根据除二算式推断。现有 [CAAZ RTL](../task3/rtl/caaz20_pair.sv)与 [3% 联合噪声图和数据](../task3/evidence/negative/joint_noise/summary.json)可复用。

1. **行为：**引用已有四组普通配对／斩波独立 AZ／共享 AZ／含保持误差曲线；都用 5 MS/s、相同输入、带宽、噪声设置和失配池。比较斩波独立 AZ 与理想共享 AZ 以隔离共享收益。现有数据只含一个 3% 制造样本、固定保持参数；2% 联合和保持／增益扫描要新增，现有脚本没有扫描开关。交时序、四组谱／RMS 和新增保持扫描图。
2. **RTL：**运行以下检查，验证偶奇结果的整数差与参考逐笔相等，覆盖反相／代号错配、丢笔、坏码、复位和 stall；错误必须进入合同定义的 fault 路径。

```sh
PYTHONPATH=.:src python task3/tools/run_caaz_checks.py --out /tmp/student-caaz-rtl-001
```

3. **GDS：**以 `caaz20_pair` 为顶层新建／适配 SKY130HD 后端配置、源文件清单、时钟与 I/O 约束；[task2 现有脚本](../task2/physical/sky130_run.sh)不接受此顶层。先证明新配置综合的确是该顶层，再做等价、布局布线、提取、STA、DRC／LVS 及适用电源检查，交 GDS、版图和时序图。不能拿整体顶层 GDS 作为独立宏已交付。

## M5–M6：整体集成

将八片控制、同笔 Q28 重构与 CAAZ 按 [整体负注入证据](../task_integration/evidence/negative/README.md)绑定；先核对旧收据与当前 RTL、参数、向量的摘要，变化处定向重跑。整体顶层 `sar20_digital_lms_top` 是另一个交付对象，且包含片外训练权重的片上重构，不包含片内 LMS 训练器。按 [整体后端入口](../task2/physical/README.md)完成新版本整体 GDS，并交三技术联合时序／频谱、版图、setup／hold 及 DRC／LVS／电源摘要；宏结果不自动等于整体签核。

六个月是紧凑目标，依赖现有实现、环境与接口及时可用；若未满足某检查，按实际记录 FAIL／BLOCKED／NOT_RUN，不通过删减原始报告或挪用历史证据宣布完成。性能门槛、模拟绑定或签核输入未冻结时单列待解决项，不伪造 dB 或工艺结论。
