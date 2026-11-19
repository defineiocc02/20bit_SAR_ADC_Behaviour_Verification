# 工作树与文件归属

从[根 README](../../README.md)找功能与根目录，从[文档索引](../INDEX.md)找专题。工程合同记录接口，状态页记录各项验证所用的条件。[工程合同](../CONTRACT_CURRENT.md) · [项目状态](../STATUS.md)

## 找实现，找对应的测试

| 对象 | 实现、输入与证据 |
| --- | --- |
| 生产数字 IP | [唯一顶层](../../rtl/v21/adc_digital_ip_v21.sv)由[生产清单](../../flow/build/filelists/files_adc_v21.f)展开。测试侧模拟宏由[仿真清单](../../flow/build/filelists/files_adc_v21_sim.f)引入。 |
| 时序、开关与出码 | [算法卡片](../algorithms/digital.md)链接到相应 RTL 与测试台。顶层测试驱动在 [sample_cadence.cpp](../../tb/v21/sample_cadence.cpp)，运行入口、夹具与向量说明在[仿真导航](../../flow/sim/README.md)。 |
| 默认 Python 数字模型 | [defaults.py](../../src/adi_model/defaults.py)运行同一生产事务，[工具](../../tools/check_default_rtl.py)对真实顶层事件和出码逐笔核验；[快速上手](../guide/QUICKSTART.md)给出输入、输出与诊断命令；预期行为由 [flow/tests/](../../flow/tests/)核对。 |
| 可复用组件及研究扩展 | [组件接口](../audit/components/CURRENT_INTERFACE_AND_ARITHMETIC.md)定义组件测试口径。[subsys/](../../subsys/)存放 DSP、CAAZ 与对照实验。生产顶层的接口以工程合同为准。 |
| 运行和发布 | [tools/](../../tools/)存放命令和参考计算，[flow/](../../flow/)存放清单、测试、综合与形式验证流程。根层打包配置定义 Python 交付，[.github/](../../.github/)存放 CI 和协作模板。 |

## docs 的九个目录

| 目录 | 保留的内容 |
| --- | --- |
| [algorithms/](../algorithms/README.md) | 数字 IP 的算法、六张手工矢量图、其余流程与状态机，以及核对图源和数值的清单。原生图直接编辑 SVG，自动排版图编辑算法页中的 Mermaid。 |
| [figures/](../figures/) | [behavioral/](../figures/behavioral/README.md)保存行为模型图及其出图清单；[static_linearity/](../figures/static_linearity/)保存全码 DNL/INL 图、参数与摘要。 |
| [v21/](../v21/) | 当前数字 IP 的物理接口、开关规则、权重配置、算术证明和整核实现记录。首查[合同总表](../CONTRACT_CURRENT.md)。 |
| [guide/](../guide/QUICKSTART.md) | 模型上手、架构、术语、排查以及[历次设计决策](../guide/decisions/README.md)。 |
| [mechanisms/](../mechanisms/README.md) | 按论文和专利原图解释各机制，图号与原始出处配对。[原件清单](../../references/README.md) |
| [audit/](../audit/README.md) | 独立审查与报告，`reviews/` 收专题复核，`components/` 收独立组件说明，`artifacts/` 收已登记的网表和工具证据。 |
| [caaz/](../caaz/README.md) | 斩波辅助自归零研究。与随机化的组合实验在 [randomization/](../caaz/randomization/README.md)。 |
| [meta/](./) | 仓库归属、维护约定、[变更记录](CHANGELOG.md)、[许可](LICENSE)、[声明](NOTICE)与[引用信息](CITATION.cff)。 |
| [_history/](../_history/) | 按当时输入复查的验收、接口草案与输出。老 D3/D4 交接在 [d3d4/](../_history/d3d4/)，原模型报告及绘图在 [model_results/](../_history/model_results/README.md)。 |

## 哪些文件属于验证输入

`.f` 决定待编译的源。`tb/` 的冻结向量和 `flow/sim/fixtures/` 的 `.sv/.hex/.json` 均有读入它们的测试台；`flow/tests/fixtures/` 的 `.rpt` 可用于诊断测试。审计目录内的 `.svf/.v` 随原工具报告解释。[编译清单说明](../../flow/build/filelists/README.md) · [后端存证说明](../audit/artifacts/README.md)

两份早期试验台已由现行用例取代。ADC2 捕获与返回见 [tb_i3_full.sv](../../tb/v21/tb_i3_full.sv)，SAR 与物理写门联调见 [tb_i12_full.sv](../../tb/v21/tb_i12_full.sv)。需要核查旧用例时可读取整理前的 Git 对象。

~~~bash
git show 9e4f1fd:tb/v21/tb_i3_adc2.sv
git show 9e4f1fd:tb/v21/tb_i12_integration.sv
~~~

日常运行把输出写入新建的临时目录。发布图及冻结证据保留输入身份，不把旧图的哈希改写成新实现的实测。下列命令从仓库根统计实际存在的 Git 可见文件并核对入口，无需 EDA。

~~~bash
python3 -B tools/check_doc_counts.py
python3 -B tools/check_engineering.py
node tools/v21_algorithm_atlas.mjs --check
~~~
