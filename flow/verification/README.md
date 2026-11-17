# v21 与组件验证

当前集成 ADC 的数字回归由 [verify_v21.sh](../../tools/verify_v21.sh) 组织。输入、位宽、接口和回归所需的仿真环境见 [工程合同](../../docs/CONTRACT_CURRENT.md) 与 [仿真入口](../sim/README.md)。

| 路径 | 用途 |
| --- | --- |
| [src/adi_model/](../../src/adi_model/) | 通过电荷、采样、残差与量化模型研究机理并定位误差。 |
| [reference/](reference/) | 为独立组件测试提供 Python 整数参考，定义算术舍入、截断、饱和和标志。 |
| [manifests/](manifests/) | 登记机制来源、证据范围和输入身份。 |
| [flow/tests/](../tests/) · [CI 工作流](../../.github/workflows/ci.yml) | Python 测试与已登记的独立 RTL 组件检查。组件结果对应各自的输入和断言。 |

早期模块向量和独立整机展开按原 Git 证据解读。原始阶段报告可用 `git show 674b0d8:flow/verification/reports/VERIFICATION_REPORT.md` 查看。当前整机及模块级证据见 [项目状态](../../docs/STATUS.md)。
