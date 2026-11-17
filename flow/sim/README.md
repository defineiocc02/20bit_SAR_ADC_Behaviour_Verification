# RTL 仿真入口

当前集成 ADC 为 [v21](../../rtl/v21/adc_digital_ip_v21.sv)，ADC2 为 13 位、0.75 V。整链回归从仓库根调用 [verify_v21.sh](../../tools/verify_v21.sh)。仿真环境、运行目录与源文件选择分别见[环境说明](../../docs/v21/I5_ENVIRONMENT.md)、[工作目录规范](../../docs/audit/REMOTE_WORKDIR_POLICY.md)和[编译清单](../build/filelists/README.md)。

| 检查对象 | 入口与范围 |
| --- | --- |
| v21 整链 | [verify_v21.sh](../../tools/verify_v21.sh) 组织数字事务、捕获、ADC2 节点宏与整数回归。 |
| 共享 D3/D4 模块 | [verify_d3d4.sh](../../tools/verify_d3d4.sh) 核对 DEM 掩码和分路扰动向量。 |
| 源绑定、定点和 DSP 组件 | [CI 工作流](../../.github/workflows/ci.yml)声明测试台与显式输入。结论范围取决于当次组件和输入。 |

未设置 `V21_WORKDIR` 时，脚本会在执行主机新建临时目录。指定路径时要提供尚未存在的专用绝对路径，脚本会保留已有目录。用 `bash tools/verify_v21.sh --check-sources` 可只打印输入源清单。

## 目录定位

- [仓库根的 tb/](../../tb/) 放 v21 与共享模块测试台。[tb/](tb/) 放组件与协议测试台。
- [fixtures/](fixtures/) 放组件的合成输入和绑定。每组输入只适用于其声明的几何、位宽与协议。
- [ref/](ref/) 放独立定点参考，[smoke/](smoke/) 放仿真器通路的小例子。

旧回归按当时的运行记录追溯。[分层验证](../verification/README.md) · [项目状态](../../docs/STATUS.md)
