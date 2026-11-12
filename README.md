# 20位／40 MS/s SAR ADC：行为模型、静态校准与数字实现

[English](README_EN.md) · [当前状态](docs/STATUS.md) · [数字状态表](docs/digital_status_matrix.md) · [原文对照](docs/current_digital_source_map.md) · [论文与讲稿](references/README.md)

> 当前实现采用显式来源绑定和ABI2。下面分别给出可重跑的Python静态校准、实际RTL静态闭环和Fig.19逐事件反例；旧模型结果单独归档。

> **合并分支 `merge/adi-repro`（W8 候选，未推送）**
> main 包为骨；CAAZ 已隔离到 `extensions/caaz/`；普通逐笔 AZ 在 `behavioral/ra_az.py`。
> W1–W3 已落地（合同/profile、内核/控制迁移 119 项测试）；W4–W7 为合同/编译级，主链未闭合。
> 详见 [W8 状态](docs/status_w8.md)、[架构](docs/architecture.md)、[迁移](docs/migration.md)。
>
> 三条命令：
> `PYTHONPATH=src python -m adi_model simulate --samples 512 --output /tmp/adi-demo`
> `PYTHONPATH=src pytest -q`
> `PYTHONPATH=.:src pytest -q extensions/caaz/tests`

## 项目目的与范围

本工程研究两级SAR ADC中的电容失配、采样电荷、残差重构和数字校准。交付分为三部分：**ADC行为模型、静态失配校准流程、数字控制与重构RTL**。输出DSP和斩波协助自归零（CAAZ）是独立扩展；动态元件匹配（DEM）、dither与切片选择分别说明实现范围和验证条件。

**20位是最终输出码宽，40 MS/s是默认raw采样率参数。** 这些数值不代表20位ENOB或目标工艺已达到40 MS/s。原文架构锚点、工程推断、实现状态与证据等级分别登记，不能通过换模块名称或对齐一条公式宣称复原原芯片。

## 已经完成的工作与当前状态

| 工作项 | 实际内容 | 当前结论 | 尚需完成 | 入口 |
| :--- | :--- | :--- | :--- | :--- |
| **ADC行为模型** | 显式物理切片、电容／桥接网络、独立SADC、共享RA与ADC2；默认层集中提供可覆盖的工程推断值 | 默认命令已实跑4096笔，保留名义20位码、各节点异常和假设清单；指定静态路径有独立留出 | 动态建立、真实开关／参考、噪声协方差和模拟宏 | [默认层](src/adi_model/defaults.py)、[信号链](src/adi_model/pipeline_engine.py) |
| **静态失配校准流程** | 以数字观测和独立已知输入拟合有效C/Cf，检查秩，固定系数后作整数重构 | Python无dither及RTL Fig.19受限静态两套闭环均有真实留出；RTL系数在Python训练、回装DUT | 实际参考与读出、含噪动态、漂移、ADC2非线性 | [Python重跑](tools/run_static_calibration.py)、[RTL重跑](tools/run_fig19_rtl_calibration.py) |
| **数字控制与重构RTL** | 20个清单模块实现9位SAR、18/8资源池、16相位、系数装载、上下文和重构；显式双source绑定 | 8文件包、两采集端口、calobs v2、实际RDAC事件与整数结果在指定有限窗内完成同池对拍 | 真正停采／排空／park、观察输送、模拟建立与目标工艺验证 | [RTL入口](rtl/README.md)、[ABI2合同](docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md) |
| 输出DSP扩展 | 15个模块实现并行校正、SINC、捕获、SPI／寄存器与GPIO；缺失来源参数显式拒绝 | handoff003核15源，14指定数字场景与5严格profile通过；已报告controller fault有实际锁停／恢复 | 整链滤波选择数值路由、逐输出元数据、未报告ID缺口、真实停采park后保记录软件复位、数据PHY／CNV、逆向两点系数学习 | [DSP说明](ad4080_dsp/README.md) |
| CAAZ扩展 | 显式输入等效AZ记录、数字控制及完整重构后的配对 | 默认关闭；现行8包组件核到4个数字配对，独立配对单元有325字段核对 | CBIAS保持、H/K、模拟斩波与最终组合验证 | [状态表](docs/digital_status_matrix.md) |

默认输出使用**名义系数**，没有进行训练。`adi-simulate`正常结束只说明所选推断模型实际生成了记录；训练改善、原文机制复现和目标精度分别需要自己的证据。

## 主要实验结果

### Python静态校准：独立留出RMS 85.83 → 2.076 µV

同一固定失配工程模型，使用4096笔均匀随机参考覆盖[−3,+3) V训练，固定schema2系数；再用未参与训练的512笔留出，让名义／固定系数消费同一raw。中幅[−2.8,+2.8) V的同样4096笔只有rank1224/1279而被拒绝；全跨度训练达到1279/1279，条件数2883.7。

![当前同源Python静态校准及中幅不可辨识反例](docs/readme/fig/source_bound_static_calibration.png)

*图1｜最终源码的公共producer正例`fullspan_positive`和中幅反例`midrange_rank_negative`，绑定producer及36个实际加载源码哈希。seed42，单元／桥接失配各0.001，dither／单元DEM关闭，采样／RA随机噪声关闭；独立留出仅覆盖[−2.6,+2.7] V。RMS 85.829→2.07577 µV，标准差85.314→2.07544 µV；模拟异常／clip均0，512笔最终码与独立整数oracle一致。[逐点数据与来源](docs/readme/data/source_bound_static_calibration.json)、[重新训练命令](docs/REPRODUCING.md#3-静态校准重新训练固定与留出)。这证明指定Python工程静态条件下的校准效果，不证明RTL、动态／含噪工况、端点、全码INL/DNL或芯片性能。*

### RTL静态闭环：同一留出RMS 97.04 → 1.993 µV

实际DUT在同一固定非均匀ADC模型上产生4096笔word0全跨度训练观测，片外Python用独立参考拟合有效权重（rank1279/1279），将固定系数回装DUT。独立留出是峰值2 V的指定正弦，64个扰动字各出现8次；两种系数使用相同物理池、有序slice计划、开关记录与512笔raw。训练和留出参考不相交。

![实际RTL静态闭环的512笔校准前后及放大残差](docs/readme/fig/source_bound_rtl_calibration.png)

*图2｜实际DUT `full_typechecked_001`及四次同版生产Python重放；指定静态等效、随机转换噪声及单元DEM关闭。RMS 97.04090→1.99287 µV，标准差96.28313→1.99087 µV。正常留出515条程序、514笔物理采样、512笔目标退休；三条程序尾不退休。status／sticky／fault与结果flags均为0，512码与生产Python及独立整数oracle一致，3598个RDAC事件含两笔已采样尾记录。[逐点数据与收据身份](docs/readme/data/source_bound_rtl_calibration.json)、[重跑与独立重放](docs/REPRODUCING.md#5-显式导出与rtl组件执行)。该证据限静态工程模型与有限观察窗，不能推及真实停采、端点、动态或芯片精度。*

图1与图2使用不同激励和资源计划，分别验证校准闭环。两图之间的误差差值不用于评价dither的增量收益。

### Fig.19扰动：分开采集，逐事件检查

同一六位字派生lower、total和upper：lower进入SADC采样，total进入RDAC保存，upper调整每个已决RDAC候选码。按实际mask和固定权重重构时，仅去除一次已知扰动。完整分组、Q32单位与端点见[ABI2合同](docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md#2-两采集端口与同笔归属)。

![Fig.19两采集端口与早事件反例，以及DEM应插入的位置](docs/readme/fig/source_bound_fig19_mechanism.png)

*图3｜原创机制示意及实际DUT反例。−2.9 V／word32的SADC与RDAC采样分别为−2.9000 V、−2.946875 V；最终k=4合法，前三个候选为−4，非法mask不加载。DUT六个目标只退休四笔，拒绝ID3／6并报告预期控制故障。[原事件与验证身份](docs/readme/data/source_bound_fig19_evidence.json)。±2 V只指指定静态点与留出激励；事件检查针对整数码域，参考／模拟建立和原文3D DEM未获证明。本图不复制原文。*

<details>
<summary>展开af8／9bdf历史校准、交织、旧dither／DEM结果</summary>

以下三张图保留原字节，属于**af8功能合同、9bdf文档版本保存的历史结果**。实际受测源码以相邻JSON的源SHA和原配置为准；9bdf本身不是这些实验的新运行身份。旧SID／零和bridge、`range`和固定4倍扰动方案已退出当前运行入口，图中的数值不转作本轮新模型的性能。

![历史静态校准：误差标准差55.75降到2.08微伏](docs/readme/fig/current_calibration.png)

*图4｜历史静态失配夹具：seed42、DEM＋sampling D=2，4096训练／512独立留出、关闭外加随机噪声，标准差55.75→2.08 µV。该结果限原开关／观察合同。[原数据与源SHA](docs/readme/data/structural_calibration_summary.json)；第二颗模型及含噪条件继续保留。当前公共producer的独立新运行见图1，两者不合并为同一性能记录。*

![历史交织比较保留SFDR与SNDR的取舍](docs/readme/fig/current_interleave.png)

*图5｜历史三策略Python比较：相同10 ps标称时偏池、40 MS/s、N=16384。按旧RTL规则运行的Python策略相对固定A/B，SFDR提高14.93 dB，SNDR下降11.05 dB；它不是SV输出频谱。[原配置、数组与指标](docs/readme/data/interleave_40m.json)。当前默认层没有据此获得逐切片时偏／带宽补偿能力。*

![历史工程扰动模型在2.99伏附近的内部越界](docs/readme/fig/current_dither_headroom.png)

*图6｜已退役工程扰动方案的历史余量诊断：2.99 V处SADC越界18次、RA饱和4次、ADC2越界15次，最终码clip仍为0。保留失败点与原端点约定；这些计数不是新Fig.19等效模型的当前缺陷或测试结果。[原数据](docs/readme/data/dither_headroom.json)。*

历史DEM图统计的是旧映射的正轨使用次数与分母；名义守恒不证明均衡或精度收益。该映射已退役，[9bdf专题](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/docs/randomization_caaz/README.md)仅作历史解释。当前指定静态闭环见图1／2；动态与交织补偿仍需单独验证。

</details>

## 系统怎样工作与默认值

![工程推断的默认等效结构与待验证边界](docs/readme/fig/source_bound_default_architecture_v1.png)

*图7｜原创默认等效架构图：独立SADC、18片资源池、63+8推断阵列、共享RA／Cf、ADC2和两种系数入口。蓝色是原文架构锚点，绿色是工程推断，橙色标出已核的受限静态路径及待验证的物理网络。完整物理网络仍未实现。本图不复制原文图像。*

默认层采用每次8片转换、8片采集、2片空闲的因果计划；已制造的失配在同一模型对象中保持。基本译码以整数粗码决定主／子阵列计数，资源组随机选择与单元DEM分开。名义重构只读名义网络；拟合器只读数字观测和独立参考，不读取实际电容、真实增益或持有电压。

| 参数或结构 | 当前集中默认值 | 来源与边界 |
| :--- | :--- | :--- |
| 采样、输出与资源 | 40 MS/s；前级9位；输出20位；18片中每次8片转换 | 架构锚点；输出字宽与采样率设置不等于ENOB或工艺时序通过 |
| 名义输入与增益 | 差分等效峰值±3 V；名义增益32 | 实际电荷增益由所选电容／反馈电容决定 |
| SADC与基本译码 | 513个均匀边界、有效窗[−3,+3) V；默认k=c；主计数k//8、子计数k%8 | 均匀门限、canonical mask与整数计数为工程实现，不是原文3D DEM或完整binary-to-unary矩阵 |
| 每片分段网络 | 主63、子8；β=1/8；Cu=40.0390625 fF、Cb≈45.7589286 fF | 以20.5 pF映射到名义Cnorm推导的工程网络，非公开版图单元清册 |
| 活动电容与共享反馈 | Cnorm=20.5 pF；共享Cf=0.640625 pF；独立SADC电容2.5625 pF | 节点含义与Cs匹配是推断；反馈电容由活动转换组共享 |
| 后级与摆幅 | ADC2 12位；窗口[−0.0375,+0.4125) V；RA限幅±0.5 V | RA输出节点的工程预算；`half_open`端点，异常保留 |
| 失配与噪声 | 单元／桥接相对标准差各0.001，反馈默认0；300 K、χ=2；RA输出1 mV RMS | 制造压力与噪声假设；默认采样／RA噪声开启，不由目标DR反推 |
| 定点与重构 | Q30权重／Q32归一化电压V/Vfs，48／64位接口、96位受检累加、20位码 | 工程算术选择；默认名义系数未经训练 |
| 采样与开关 | 默认全部Signal，参考坐标±Vfs，主／子初始等效坐标0，寄生默认0 | 静态等效假设；不建立真实开关噪声协方差或参考动态 |
| 默认关闭与零值 | dither、单元DEM、CAAZ、动态关闭；slice时偏／失调0，driver噪声关闭 | 开关由运行元数据记录；显式开启未支持项会拒绝，不暗中降级 |

`DefaultParameters`可以显式覆盖，运行元数据逐项记录推断来源。改变电容不会暗中重调另一电容来维持原增益。低层`Config`保留缺项检查，高层`default_model()`负责提供上述可运行默认；两者职责不同。

**dither与DEM的当前边界。** 默认不启用源dither或单元DEM。可选Fig.19路径接收显式六位字：upper为`U=4D6+D4`，lower与total满足`T=L+U·Δ`；本工程选择SADC采样`x−L`、RDAC保存`x−T`及命令`k=c−U`，均匀名义码表可写`VD(k)+r+T`；实际失配重构按逐unit的mask与冻结权重归约，不再扣一次U。负极性、低位4:2:1/7的Q32取整和18/8目标绑定是工程假设；目前仅作**经指定Python／RTL对拍的受限工程静态等效**。[逐项原文依据](docs/current_digital_source_map.md)单列其与完整物理网络的差距。原文3D DEM及完整binary-to-unary开关矩阵尚未实现。

| 组合 | 当前证据与边界 |
| :--- | :--- |
| Fig.19静态等效＋18/8因果slice计划＋固定非均匀电容池＋冻结校准 | 64字各8次的实际RTL留出通过同池、同有序ID／计划重放；Python独立整数结果一致。资源组随机选择与单元DEM分别核验 |
| Fig.19＋原文3D单元DEM | 原文3D网络未实现；静态v1拒绝单元DEM组合，不从资源调度推出已组合 |
| Fig.19＋CAAZ | 带真实共享AZ的组合尚未验证；v1拒绝额外非零RA扰动。零AZ或只有数字pair不证明CBIAS保持、H/K或噪声抵消 |
| Fig.19＋动态／转换随机噪声 | 当前受限路径显式拒绝，必须显式关噪声；专用dither电容／开关的失配及噪声协方差未建模 |

## 尚未完成的工作

| 类别 | 需要完成什么 | 可接受的完成证据 |
| :--- | :--- | :--- |
| 实际读出与校准扩域 | 建立独立参考、可读数字观察与明确配置代次；在含噪、漂移与ADC2非线性下训练／留出 | 同一真实参考与实际捕获，报告满秩、固定系数、整数结果和全部异常 |
| 余量、随机化与交织 | 近负端早事件越界已定位并拒绝；还需来源支持的有效域／物理实现、源3D单元DEM、逐slice孔径／带宽观测与补偿 | 合法事件及端点覆盖；固定同一失配池、输入和带宽对照，报告SFDR、SNDR及使用统计 |
| 停采与物理验证 | 定义停采、排空、park与独立模拟idle确认；补连续采集、真实参考阈值回路、RA／ADC2建立及PVT／MC | 无损或明确取消的样本记录、实际宏时序、目标库综合／STA、CDC/RDC与PEX |
| 可选扩展 | CAAZ保持与H/K、完整组合噪声；DSP滤波选择、输出元数据和数据PHY | 对应输入、元数据、整链路由及宏／协议的独立证据 |

[13项待证记录的分组](docs/STATUS.md#按依赖排序的未闭合项)列出主链尚缺的真实采集、参考、温漂及观察输送证据。

## 怎样复现、从哪里读代码

从默认名义运行、重新训练、实际RTL记录和只读重绘四个入口选择。安装与环境见[CONTRIBUTING](CONTRIBUTING.md)，完整输入与输出说明见[复现入口](docs/REPRODUCING.md)。

```bash
python -m pip install -e .
python -m adi_model simulate --samples 256 --output ../sar-output/nominal-demo-001
```

输出目录必须尚不存在。结果为`codes.npz`、`assumptions.json`及最后写入的`metadata.json`；`UNCALIBRATED_NOMINAL_20BIT`表示名义码，不是训练结果。`adi-validate-config --config FILE.json`只检查配置，不运行模型。

| 想理解什么 | 入口 |
| :--- | :--- |
| 默认结构、假设和真实信号链 | [defaults.py](src/adi_model/defaults.py)、[pipeline_engine.py](src/adi_model/pipeline_engine.py) |
| Fig.19、实际mask与资源选择 | [dither.py](src/adi_model/dither.py)、[dem.py](src/adi_model/dem.py)、[物理池](src/adi_model/slice_pool.py) |
| 训练、固定和整数应用 | [weight_calibration.py](src/adi_model/weight_calibration.py)、[fixed_point.py](src/adi_model/fixed_point.py) |
| 当前接口、模块状态与来源 | [现行ABI2合同](docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)、[A表](docs/current_digital_source_map.md)、[B表](docs/digital_status_matrix.md) |
| 历史重绘与旧可执行档案 | [复现说明](docs/REPRODUCING.md)、[历史档案说明](docs/history/EXECUTABLE_ARCHIVES.md) |
| 新图、逐点数据与重绘脚本 | [可重绘资产索引](docs/readme/data/source_bound_assets_manifest.json)、[数据说明](docs/readme/data/README.md) |

代码采用[BSD-3-Clause](LICENSE)。[22项来源](NOTICE)和[6份选定原件](references/README.md)继续保留原版权与内嵌声明；本工程独立于原器件作者／厂商，不授予专利许可。
