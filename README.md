# 两级 SAR ADC 的数字控制、重构和仿真

两级 SAR ADC 先粗略判断输入电压，让电容阵列产生接近输入的电平。两者之间剩下的小差值叫**残差**，由第二级 ADC（ADC2）再测一次。这个仓库实现数字控制和出码逻辑。它根据前级已判出的位控制哪些电容参与转换，然后把第二级返回的残差码和这一笔的开关记录、校准权重合起来，算出 **20 位结果**。

仓库里还有一套 **Python 行为模型**，可以给电路模型输入电压，观察采样、残差、输出码和误差。配套实验脚本可以对比扰动、失配平均和校准开启前后的结果。工程参考 ISSCC 2024 9.8，设计点为 40 MS/s、±3 V 差分输入。实际验证覆盖和已知缺口见[项目状态](docs/STATUS.md)。

## 先选你要看的东西

| 想做什么 | 从哪儿开始 |
| --- | --- |
| 看当前硬件主线怎样接线 | [数字顶层 adc_digital_ip_v21.sv](rtl/v21/adc_digital_ip_v21.sv)。它把下文的片组管理、开关控制和数字重构接在一起。 |
| 在电脑上跑一笔或一段数据 | 模型会模拟前后两级的信号，输出逐笔数据。[Python 示例](#跑起来) |
| 看图理解开关是怎么动的 | [机理图和验证图](docs/figures/20261117/README.md)从采集与转换交替开始，跟到实际选通的电容、残差和最终出码。 |
| 改接口、查位宽或确认合法输入 | [工程合同](docs/CONTRACT_CURRENT.md)给出硬件遵守的规则，[RTL 导航](rtl/README.md)给出对应实现和测试入口。 |

### 为什么有几个编号和顶层

- **`v21` 是当前数字主线的代码名称**。主顶层在 `rtl/v21/`。要看各个数字模块怎样连在一起，从这里进入。
- **`v2` 和 `v2.1` 是设计文档的编号**。v2 是规格正文，v2.1 是它的补充。两份合起来定义上面这一个主线的码域、开关方式和接口。同一条规格有出入时以补充为准。[合同入口](docs/CONTRACT_CURRENT.md)
- **旧顶层各有用途**。`rtl/_legacy/tops/` 里保留了只测开关控制的独立小模块组合和早期重构链。要读这些历史结果，用它们原来的测试台和编译清单。`rtl/top/sar20_digital_core.sv` 是另一条独立验证路线，配套显式输入和 DSP 测试。[各路线与测试入口](rtl/README.md)
- **`9.0.0` 是 Python 安装包的版本号**，定义在 `pyproject.toml`，用于安装和发布。Python 模型和数字主线使用相同的 13 位 ADC2 码制，模型里的电容结构和开关规则另有记录。[Python 模型说明](docs/guide/ARCHITECTURE.md)

## 跟一笔样本走完数字主线

1. **分配本拍采样和转换的片组（D1，D6）。** 稳态下，电容阵列分为 18 个切片，每个切片是一组电容。其中 8 片采集新输入、8 片转换上一笔，另外 2 片空闲。控制器接收样本时记下片号、样本编号（`tag`）和开关参数。片组由采样转入转换时，这套记录也随样本带过去。[片组调度](rtl/sched18.sv) · [样本交接](rtl/v21/acq_conv_ctx.sv)
2. **采样前先设置扰动（D4）。** 采样前主动加一个已知的小偏移，目的是打散误差随输入重复的模式。6 位扰动字同时决定采样预置 `pre` 和转换时所需的码偏移 `U`。如果这笔的前级最终判为 200，且预先选定的 `U=4`，电容阵列就按命令 204 设置开关。采样时真正采用的预置会保留到重构，供扣回扰动。[扰动预置](rtl/dither_pre.sv)
3. **前级每确定一位，电容开关就跟进一步（D2，D3）。** 普通转换先有一次快速判决，再接六次逐位判决。数字逻辑只使用已经确定的结果更新电容开关。动态单元匹配（DEM）轮换承担同样名义权重的电容，以分散固定失配的影响。最后一次有效开关状态记为 `post`，用于后续重构。[逐位事件](rtl/v21/sar_evt_v21.sv) · [开关掩码](rtl/dem_mask_v3.sv)
4. **把残差码和同一笔的开关记录合起来（D6，D5）。** 模拟侧放大残差，ADC2 返回 13 位原码。数字端给这份原码配回捕获时保存的样本编号与权重和。重构器读实际 `pre/post` 对应的校准权重，扣回扰动，最终输出 20 位码、样本编号和数字状态标志。ADC2 的越界标志随它自己的响应给出。[残差捕获](rtl/v21/adc2_capture.sv) · [权重求和](rtl/v21/wsum_v21.sv) · [定点重构](rtl/v21/recon_v21.sv)

开关接口对每片提供 4 位采样预置和 20 位转换控制。ADC2 的规定输入窗口为 `[-0.1875, +0.5625) V`。测试台可使用行为宏模拟 ADC2 的返回，实际模拟电路仍由外部接口连接。[规格与位序](docs/CONTRACT_CURRENT.md) · [权重怎样装入及提交](docs/v21/CONFIG_CONTRACT.md)

## 文件放在哪里

以下为仓库的 9 个根层目录，常用子目录和文件列在各自所在处。

~~~text
.
├── rtl/                             SystemVerilog 硬件
│   ├── v21/                         当前主顶层与该顶层专用模块
│   ├── sched18.sv                   主线用的 18 片调度
│   ├── dither_pre.sv                主线用的采样预置
│   ├── dem_mask_v3.sv               主线用的电容开关译码
│   ├── recip_q60.sv                 重构用的整数倒数底层
│   ├── top/                         独立核顶层
│   ├── core/                        独立核的内部模块
│   ├── params/                      错误码等 RTL 头文件
│   └── _legacy/tops/                历史用例保留的顶层
├── tb/                              SystemVerilog 测试台和参考向量
│   └── v21/                         主线的采样、事件、捕获和重构用例
├── src/adi_model/                   Python 行为模型
│   ├── cli.py                       命令行参数和输出文件
│   ├── defaults.py                  默认参数和模型装配
│   ├── topology/                    电容连接和输入、码、开关的映射
│   ├── control/                     片组选取、开关控制和扰动算法
│   ├── behavioral/                  采样、参考、放大和量化的模型
│   ├── simulation/                  按样本推进并保存过程记录
│   ├── contracts/                   Python 参数、数据类型和整数规则
│   ├── calibration/                 拟合、保存和应用校准系数
│   └── metrics/                     频谱、线性度等计算
├── flow/                            仿真、验证和后端流程
│   ├── Makefile                     列出常用的测试和向量生成入口
│   ├── build/filelists/             记录每条硬件路线编译哪些源文件
│   ├── sim/fixtures/                仿真输入、绑定和权重数据
│   ├── tests/                       单项和集成检查
│   ├── verification/                参考计算、结果比对和验证记录
│   │   └── manifests/               来源清单和机制登记
│   ├── formal/                      静态属性证明的脚本和属性
│   ├── synth/                       综合、布局布线及网表等价脚本
│   └── scripts/                     文件与运行目录维护
├── tools/                           可直接调用的脚本
│   ├── verify_v21.sh                运行数字主线测试
│   ├── run_open_rtl.py              运行独立组件测试
│   ├── recon_oracle.py              生成定点重构的参考结果
│   ├── run_review_figures.py        重画机理图和定量图
│   ├── requirements.txt            Python 工具环境所需依赖
│   ├── results/                     冻结的历史结果
│   └── _legacy/                     退役脚本
├── subsys/                          额外研究和子系统
│   ├── ad4080_dsp/                  数字滤波和读出
│   ├── extensions/caaz/             斩波辅助自归零模型
│   ├── experiments/                 对照实验与制图配置
│   └── archive/180nm/               旧工艺下的重构实现
├── docs/                            规格、说明和证据
│   ├── CONTRACT_CURRENT.md          硬件接口和合法行为
│   ├── STATUS.md                    已验证的范围和待解决问题
│   ├── v21/                         权重配置、算术与整核验证专题
│   ├── guide/                       上手、架构和排查
│   ├── mechanisms/                  论文机制和本工程对应关系
│   ├── figures/20261117/            图、图注与逐图运行清单
│   ├── audit/                       审计、后端报告和原始证据
│   ├── meta/                        变更记录、许可和仓库维护说明
│   └── _history/                    按当时版本保留的记录
├── references/                      论文及讲稿原件、出处与清单
└── .github/                         CI、提交模板和维护配置
~~~

找一块硬件时，从 `rtl/` 找实现，从 `tb/` 找对应激励，再到 `flow/build/filelists/` 看它和哪些文件一起编译。仿真会读配套的输入文件，比如 `.mem`、`.hex` 和配套 `.json`。需要核对一个已报告的数字时，从状态表找到对应的验收或审计原件，记录里的源、配置、工具和输入决定这个数字的范围。[文件归属说明](docs/meta/REPOSITORY_LAYOUT.md) · [文档索引](docs/INDEX.md)

## 跑起来

**先跑 Python 的 64 笔例子。** 在仓库根执行。

~~~bash
run_dir="$(mktemp -d /tmp/sar-adc-demo.XXXXXX)"
bash tools/pyenv.sh --run -m adi_model simulate --samples 64 --output "$run_dir/sim"
~~~

`codes.npz` 保存每笔的输出码和过程记录，`metadata.json` 说明这次如何运行，`assumptions.json` 保存模型假设。默认模型的物理增益 `g0` 为 **32.0**，码本的定点权重和约为 **31.028**。细码解码按两者的比例换算，查模型输出误差时应使用同一增益口径。[Python 快速上手](docs/guide/QUICKSTART.md) · [定点约定](src/adi_model/contracts/README.md)

**跑硬件测试时按路线选命令。** 从仓库根执行。

| 想检查的范围 | 入口 |
| --- | --- |
| 主线的事件、片组交接、ADC2 返回及定点重构 | `bash tools/verify_v21.sh`。这是 VCS 测试入口。先用 `D3D4_WORKDIR` 指定本次专用的目录。[仿真环境](docs/v21/I5_ENVIRONMENT.md) · [远端工作目录规范](docs/audit/REMOTE_WORKDIR_POLICY.md) |
| 只核对 DEM 开关表和扰动预置 | `make -f flow/Makefile verify-d3d4`。历史独立模块组合用 `verify-d3d4-ip` 目标。 [验收范围](docs/ACCEPTANCE_STATUS.md) |
| 运行独立核或 DSP 测试 | `bash tools/pyenv.sh --run tools/run_open_rtl.py --help`。每个测试台配套自己的输入和仿真器。 [开放仿真入口](flow/sim/README.md) |

[已验证的范围和待解决问题](docs/STATUS.md) · [变更记录](docs/meta/CHANGELOG.md) · [论文和讲稿](references/README.md) · [English overview](docs/meta/README_EN.md)
