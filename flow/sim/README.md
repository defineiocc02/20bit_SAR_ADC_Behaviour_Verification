# RTL 仿真入口

当前集成 ADC 为 [v21](../../rtl/v21/adc_digital_ip_v21.sv)，ADC2 为 13 位、0.75 V。整链回归从仓库根调用 [verify_v21.sh](../../tools/verify_v21.sh)。仿真环境、运行目录与源文件选择分别见[环境说明](../../docs/v21/I5_ENVIRONMENT.md)、[工作目录规范](../../docs/audit/REMOTE_WORKDIR_POLICY.md)和[编译清单](../build/filelists/README.md)。

| 检查对象 | 入口与范围 |
| --- | --- |
| v21 整链 | [verify_v21.sh](../../tools/verify_v21.sh) 登记 11 个测试台，含逐位事件、事务归属、ADC2 宏与重构。 |
| 重构出码 | 本地 Verilator 运行 `tb_i4_recon` 和 `tb_recon_stream`，两者都实例化生产 `recon_v21`。前者检查冻结向量，后者按标签检查连续输入、空拍、延迟和错误传播。[运行命令与实测](../../docs/audit/todo/README.md#recon-s3) |
| 顶层连续采样 | `v21_sample_cadence` 直接驱动现有生产顶层，检查片内字源、SADC 实际低段引脚、RDAC 去扰、逐笔系数作用和连续输出间隔。[十二个场景与复现命令](../../docs/STATUS.md#持续采样率) |
| 共享 D3/D4 模块 | [verify_d3d4.sh](../../tools/verify_d3d4.sh) 核对 DEM 掩码和分路扰动向量。 |
| 源绑定、定点和 DSP 组件 | [CI 工作流](../../.github/workflows/ci.yml)声明测试台与显式输入。结论范围取决于当次组件和输入。 |

未设置 `V21_WORKDIR` 时，脚本会在执行主机新建临时目录。指定路径时要提供尚未存在的专用绝对路径，脚本会保留已有目录。用 `bash tools/verify_v21.sh --check-sources` 可只打印输入源清单。

定向 `v21_sample_cadence` 的 `samples.csv` 包含物理片号、八槽 pre/post 和 SAR 事件。默认 Python 的 CLI 与库参考从独立输入重放，五组共 1,792 笔。[逐样本对比命令](../../docs/guide/QUICKSTART.md)

## 目录定位

- [仓库根的 tb/](../../tb/) 放 v21 与共享模块测试台。[tb/](tb/) 放组件与协议测试台。
- [fixtures/](fixtures/) 放组件的合成输入和绑定。每组输入只适用于其声明的几何、位宽与协议。
- [ref/](ref/) 放独立定点参考，[smoke/](smoke/) 放仿真器通路的小例子。

旧回归按当时的运行记录追溯。[分层验证](../verification/README.md) · [项目状态](../../docs/STATUS.md)

[集成数字回归的被测通路](../../docs/algorithms/digital.md)
