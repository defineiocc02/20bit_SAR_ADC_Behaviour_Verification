# Python 模型架构与数据流

默认模型采用 18 片池。每个稳态周期由 8 片采集、8 片转换、2 片空闲。SADC 给出粗判决，RDAC 更新开关形成余差。共享 RA 的名义增益为 32，默认 ADC2 采用 13 位、0.75 V 窗口 `[-0.1875, +0.5625) V`，重构输出 20 位 offset-binary 码。[默认参数](../../src/adi_model/defaults.py) · [工作机理与验证图](../figures/20261117/README.md)

本页说明 `src/adi_model` 的行为模型。v21 和独立源绑定数字核各有自己的接口、位宽和验证入口。需要核对 RTL 的合成顶层、开关合同和事务先后关系时，从对应入口进入。[v21 工程合同](../CONTRACT_CURRENT.md) · [RTL 导航](../../rtl/README.md) · [独立核心 ABI2](../rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)

## 1. 分层

| 层 | 职责 |
| --- | --- |
| [contracts](../../src/adi_model/contracts/README.md) | profile、单位、观测结构、定点格式与数值规则 |
| [topology](../../src/adi_model/topology/README.md) | 电容网络、名义码本与电荷算子 |
| [control](../../src/adi_model/control/README.md) | 片选、掩码、扰动和跟随命令 |
| [behavioral](../../src/adi_model/behavioral/README.md) | 采样、参考、普通 AZ、RA、ADC2 与误差模型 |
| [simulation](../../src/adi_model/simulation/README.md) | 装配模型、推进采集与转换、返回逐笔记录 |
| [calibration](../../src/adi_model/calibration/README.md) 与 [metrics](../../src/adi_model/metrics/README.md) | 从观测拟合或应用冻结系数，计算静态与频谱指标 |

默认 CLI 装配 `DefaultModel`，由 `simulate` 进入行为流水。显式源绑定用例按自己的配置构造采集和掩码回调。两个入口的参数、观测合同与验证证据分别归档。[快速上手](QUICKSTART.md) · [源绑定验证流程](../../flow/verification/README.md)

## 2. 一笔样本的数据流

~~~text
输入电压和采样条件
    ↓
采样记录 → SADC 粗码与已决事件
    ↓
转换片组 + 实际 RDAC 命令 → 主／子底板掩码
    ↓
电荷及余差 → RA 输出 → ADC2 原码
    ↓
冻结数字观测 + 权重 → 名义定点重构 → 20 位码与 flags
~~~

模型从采样计划取得该笔的物理 ID，再按掩码与电容计算电荷。失配作用在物理池和余差路径。重构只使用数字观测及相应系数。默认解码把 RA 物理增益与权重和口径配对后对细码做换算。[流水实现](../../src/adi_model/simulation/pipeline_engine.py) · [定点约定](../../src/adi_model/contracts/README.md)

默认运行关闭源扰动与单元 DEM。显式 Fig.19 扰动用例由调用者提供六位字，存留的 Python 等效路径使用 `k=c-U` 并检查实际命令域。v21 的开关合同使用 `k=c+U`，两条绑定的命令域与行为模型的静态对照见 F05、F03。[行为控制模型](../../src/adi_model/control/README.md) · [对照图](../figures/20261117/README.md)

## 3. 状态归属与时序

| 状态 | 持有者与生命周期 |
| --- | --- |
| `Config` / `DefaultParameters` | 由调用方定义，运行期间按选定配置使用 |
| `SplitChip` | 制造电容、寄生与反馈电容属于模型实例，由制造 seed 派生 |
| 片选与噪声记录 | `run_id` 选择同一制造实例的独立计划和噪声序列 |
| 实际采集与转换片组 | 计划按样本 ID 绑定。采集切片的持有状态交接到后续转换 |
| `DigitalState` 与 `DigitalObservation` | 增益与冻结系数有各自的输入归属。原码、物理 ID、掩码与异常标志随样本记录 |
| `SimResult` | 返回逐笔结果、配置与诊断信息。异常笔保留在记录中供定位 |

事务图中的 Flash／SAR 前缀、残差读取和标签 handoff 说明事件及跨笔关系。Python 模型中的计划与动态状态由具体运行配置启用，不能据名义时间轴推断当前 RTL 的同沿优先级或物理时限。[交织和捕获机理](../figures/20261117/README.md) · [RTL 验收入口](../../rtl/README.md)

## 4. 比较与证据边界

| 对象 | 可核对的量 |
| --- | --- |
| Python 行为模型 | 选定配置下的电荷、余差、代码趋势、越窗或饱和。动态波形与名义精度解码分别查诊断可用条件。 |
| [整数参考](../../flow/verification/reference/) | 指定输入和数值格式下的宽度、舍入、限幅及标志 |
| [scoreboard](../../flow/verification/cosim/scoreboard.py) | 把对应设计与版本的实际、参考记录按接口和样本 ID 比较 |
| [CAAZ 扩展](../../subsys/extensions/caaz/README.md) | 独立的偶奇配对假设与专门用例 |
| 电路与后端 | 参考建立、采样网络、RA/ADC2 的实物非理想和工艺余量以匹配设计的证据说明为准 |

[项目状态](../STATUS.md) 维护已知覆盖、证据边界与缺口。
