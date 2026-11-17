# 20 位／40 MS/s SAR ADC 工程复现

本仓库依据 ISSCC 2024 9.8 的公开机制，实现两级 SAR ADC 的 **v21 数字控制和定点重构**，并提供 **Python 行为模型**、参考向量及对拍工具。工程设计点为 **20 位输出、40 MS/s、±3 V 差分输入**。v21 的数字边界接收外部 Flash/SAR 事件和 **13 位 ADC2 原码**，驱动 RDAC 开关，并输出带样本标签的 20 位码。[工程合同](docs/CONTRACT_CURRENT.md) · [实现与验证状态](docs/STATUS.md)

## 一笔转换如何完成

~~~text
                    ┌─ 外部 Flash/SAR 已决事件 ─→ RDAC 逐事件命令及 post 掩码
输入 → 两组切片交织 ┤
                    └─ 本笔采样预置 pre ───────→ 电荷余差 → 外部 RA/ADC2
                                                             │ 13 位原码
物理片号 + 实际 pre/post + 权重 ─→ G、Wm、Wp ─→ 同笔快照 ────┘
                                                  ↓
                                         定点去扰和反解 → 20 位码 + tag
~~~

| 模块 | 作用 | 代码入口 |
| --- | --- | --- |
| **D1 片组调度** | 稳态下从 18 片中分配 8 片采集、8 片转换和 2 片空闲。已采样的一组在事务交接时进入转换，下一笔用独立片组。定向验证可从顶层提供外部片组。 | [sched18.sv](rtl/sched18.sv) |
| **D2 逐事件跟随** | 常规路径先锁存 Flash 判决，再消费六次 SAR 已决结果。事件和量化器身份符合本笔时，才将对应码用于 RDAC 更新。高码 guard 走专用路径。 | [sar_evt_v21.sv](rtl/v21/sar_evt_v21.sv) |
| **D3 DEM 与桥接** | 将合法 RDAC 命令转换为 8 个活动槽的 160 位 `post` 掩码。普通码按 `3+3+3` 分解，横向与纵向据本笔冻结相位选通。满足桥接条件时交换指定主半单元和子支路。 | [dem_mask_v3.sv](rtl/dem_mask_v3.sv) |
| **D4 分路扰动** | 从 6 位字产生采样预置 `pre`、整数上扰动 `U` 等。同笔转换按 `k=c+U` 形成 RDAC 命令。采样预置随实际片号保存，重构用其权重扣除扰动项。 | [dither_pre.sv](rtl/dither_pre.sv) |
| **D5 权重和与重构** | 主机装入并提交 18 片的主／子阵列权重。由实际片号和本笔 `pre/post` 算 `G、Wm、Wp`，再以 ADC2 码箱中心、权重和、偏置作定点反解。最终舍入和限幅，输出 `out_code`、`out_sat`、`out_err` 及 `out_tag`。 | [wsum_v21.sv](rtl/v21/wsum_v21.sv) · [recon_v21.sv](rtl/v21/recon_v21.sv) |
| **D6 样本归属** | 管理采集／转换及捕获握手。冻结 tag、相位、扰动字和片号，给 ADC2 原码绑定捕获时的权重和与配置 epoch。旧响应和新捕获同沿到达时，旧响应按原事务快照解释。 | [acq_conv_ctx.sv](rtl/v21/acq_conv_ctx.sv) · [adc2_capture.sv](rtl/v21/adc2_capture.sv) |

v21 的合成顶层为 [adc_digital_ip_v21.sv](rtl/v21/adc_digital_ip_v21.sv)。相位 `h/v/half/coin` 经事务端口输入并冻结。模拟端连接片组选通信号、18×4 位的采样 `pre`、18×20 位的转换 `post` 与逐片写使能。ADC2 捕获后交回 `valid/raw/range_err`，范围标志随 ADC2 响应独立报告。ADC2 合同窗口为 `[-0.1875,+0.5625) V`。`adc2_node_macro.sv` 在测试台侧按数字快照生成行为响应。[机理图 M00–M03](docs/figures/20261117/README.md) · [权重配置和重载](docs/v21/CONFIG_CONTRACT.md)

权重配置有效后，`prepare_valid/ready` 受理样本。D6 等待采样和归属条件满足再把该笔从采集提升到转换。最后一次有效 `post` 和已保存的 `pre` 进入权重求和，ADC2 返回时按原 `tag` 反解。模拟侧 `hold_ack` 用于判定片组能否释放，原码交回另有握手。停止接收后可以继续处理已接收的事务。

**Python 模型**从电压、片组、电容和开关连接算出采样电荷、余差、RA 及 ADC2 输出，再执行名义解码或施加冻结校准。它支持参考建立、失配、噪声、DEM、扰动和普通 AZ 的受控对照，可查看逐笔记录、频谱与静态残差。模型与 v21 共用 13 位 ADC2 码制，Fig.19 等效扰动实验的减法绑定及几何另有说明。默认模型中的物理增益 `g0=32.0`，码本权重和约为 `G_fixed=31.028`，细码解码通过 `physical_gain` 处理两者的比例。[Python 架构](docs/guide/ARCHITECTURE.md) · [定量图及读法 F01–F12](docs/figures/20261117/README.md)

## 文件结构

下图列出主要目录和定位源码、输入及证据所需的子目录。同名文件先按所属设计路线确认，再结合选用的编译清单或测试入口阅读。

~~~text
.
├── .github/                             CI、提交模板、维护说明与检查配置
├── docs/
│   ├── CONTRACT_CURRENT.md              v21 工程合同入口
│   ├── STATUS.md                        验证状态、证据边界与待解决问题
│   ├── INDEX.md                         按用途检索文档
│   ├── v21/                             配置接口、算术证明和整核实验记录
│   ├── guide/                           上手、架构与排查
│   ├── mechanisms/                      论文机制和工程实现对照
│   ├── adr/                             架构决策的依据与取舍
│   ├── figures/20261117/                机理图、定量图、SVG 与运行清单
│   ├── audit/                           审计、后端报告与原始佐证产物
│   ├── acceptance/                      固定基线的验收记录
│   ├── reviews/                         专题独立复核及其输入或结果
│   ├── verification/                    已归档的事件与接口追踪记录
│   ├── rtl/                             独立核 ABI 及 RTL 专项说明
│   ├── meta/                            版本、许可、引用和工作树导航
│   └── _history/                        按原基线解释的历史档案
├── rtl/
│   ├── v21/                             主数字顶层与 v21 专用子模块
│   ├── sched18.sv                        v21 复用的片组调度叶子
│   ├── dem_mask_v3.sv                    v21 复用的 DEM 掩码叶子
│   ├── dither_pre.sv                     v21 复用的扰动预置叶子
│   ├── recip_q60.sv                      重构倒数链的底层实现
│   ├── top/                             独立源绑定核等入口
│   ├── core/                            独立核的结构模块
│   ├── params/                          接口错误码等头文件
│   └── _legacy/tops/                    历史独立 D3/D4 IP、TopW 与早期顶层
├── tb/
│   ├── v21/                             v21 事件、捕获、配置和重构测试
│   └── tb_*.sv、vec_*.txt                 叶子及旧链用例和冻结向量
├── flow/
│   ├── Makefile                         D3/D4 验收、片组统计和参考向量入口
│   ├── build/filelists/                 按顶层选择的编译源清单
│   ├── sim/                             仿真入口、绑定夹具、测试台
│   ├── formal/                          VC Formal 脚本和属性
│   ├── synth/                           DC、FC、Formality 脚本及约束
│   ├── tests/                           Python 检查、集成测试与固定输入
│   ├── verification/                    算术参考、向量、scoreboard 和历史输出
│   │   └── manifests/                   来源和机制项登记
│   └── scripts/                         维护工具
├── src/
│   └── adi_model/                       Python 包
│       ├── cli.py                       命令行入口
│       ├── defaults.py                  默认参数与模型装配
│       ├── contracts/                   参数 profile、观测和定点格式
│       ├── topology/                    电容网络、码本和电荷算子
│       ├── control/                     片选、DEM、扰动和跟随
│       ├── behavioral/                  采样、参考、RA、ADC2 和误差模型
│       ├── simulation/                  逐样本推进、片池、记录和导出
│       ├── calibration/                 参数拟合、冻结与应用
│       └── metrics/                     静态和频谱评价
├── subsys/
│   ├── ad4080_dsp/                      AD4080 风格数字滤波与读出
│   ├── extensions/caaz/                 斩波辅助自归零的独立研究
│   ├── experiments/                     消融矩阵和制图配置
│   └── archive/180nm/                   旧 180 nm 重构路线
├── tools/
│   ├── requirements.txt                 Python 工具环境依赖
│   ├── verify_v21.sh                    v21 回归驱动
│   ├── run_open_rtl.py                  独立组件的仿真驱动
│   ├── recon_oracle.py                  v21 定点算术参考
│   ├── run_review_figures.py            机理与验证图重生成
│   ├── _legacy/                         已退役工具原件
│   └── results/                         历史结果快照
└── references/                          论文、讲稿及出处与哈希清单
~~~

根级保留 `README.md`、`pyproject.toml`、`.gitignore`、`.gitattributes`。快速验收在 [flow/Makefile](flow/Makefile)，Python 工具环境清单在 [tools/requirements.txt](tools/requirements.txt)。许可、第三方声明与引用信息归入 `docs/meta/`。[文件归属及迁移说明](docs/meta/REPOSITORY_LAYOUT.md) · [版本记录](docs/meta/CHANGELOG.md) · [许可](docs/meta/LICENSE) · [第三方声明](docs/meta/NOTICE) · [引用信息](docs/meta/CITATION.cff)

### 如何区分几条 RTL 路线

| 目标 | 顶层与入口 |
| --- | --- |
| **v21 当前数字集成** | `rtl/v21/adc_digital_ip_v21.sv` 配 `files_adc_v21.f`。仿真清单再加入行为 ADC2 宏及所需 testbench。按目标选择清单。 [RTL 导航](rtl/README.md) · [文件清单](flow/build/filelists/README.md) |
| **独立源绑定核** | `rtl/top/sar20_digital_core.sv` 配 `rtl/core/` 和显式 `binding.json`／源绑定模块。AD4080 风格 DSP 有自己接入和测试路径。 [验证流程](flow/verification/README.md) · [AD4080 DSP](subsys/ad4080_dsp/README.md) |
| **历史 D3/D4 IP 与 TopW** | 位于 `rtl/_legacy/tops/`。旧独立 D3/D4 仍有自己的相位发生和 follower，TopW 是早期权重重构路线。按原清单与对应 TB 阅读。 [旧 D3/D4 接口](docs/D3D4_IP_INTERFACE.md) |

`.mem/.txt` 可能是 TB 读取的向量，`flow/sim/fixtures/` 的 `.hex/.json/.sv` 是配套仿真输入，`.rpt/.out/.svf/.v` 也可能是测试或审计所用的原始证据。本机临时编译、波形和缓存应放生成目录，图与实验数据通过清单核对。[产物及存证归属](docs/meta/REPOSITORY_LAYOUT.md)

## 运行入口

**Python 示例。** 在仓库根目录运行，`--output` 取新目录。

~~~bash
run_dir="$(mktemp -d /tmp/sar-adc-demo.XXXXXX)"
bash tools/pyenv.sh --run -m adi_model simulate --samples 64 --output "$run_dir/sim"
~~~

输出包含 `codes.npz`、`metadata.json` 和 `assumptions.json`。查看模型参数、样本和结果身份后，再选择静态、频谱或校准实验。[逐步说明与重绘命令](docs/guide/QUICKSTART.md)

| 数字检查 | 入口与适用范围 |
| --- | --- |
| v21 回归 | `bash tools/verify_v21.sh`，需 VCS。使用已配置的远程主机时设置 `D3D4_RUNNER`。脚本会清理并重建 `D3D4_WORKDIR`，为每次运行指定独立目录。 [环境记录](docs/v21/I5_ENVIRONMENT.md) |
| D3/D4 叶子与旧 IP | `make -f flow/Makefile verify-d3d4` 核对名义掩码、桥接和分路扰动。`make -f flow/Makefile verify-d3d4-ip` 验历史独立 IP，门级部分须提供该设计配套网表和单元模型。 [独立 IP 的验收范围](docs/ACCEPTANCE_STATUS.md) |
| 源绑定及子系统 | `python tools/run_open_rtl.py --help`，在支持的仿真器、设计输入及新输出目录下按目标运行。 [仿真入口](flow/sim/README.md) |

工程合同的目标规格、Python 行为实验、RTL 回归和后端工具结果各有自己的输入及基线。当前整核没有同版网表和签核证据，已知的重构 S3 缺陷记录在状态表。[复现状态](docs/STATUS.md) · [全部文档](docs/INDEX.md) · [论文与讲稿](references/README.md) · [维护指南](.github/CONTRIBUTING.md) · [English overview](docs/meta/README_EN.md)
