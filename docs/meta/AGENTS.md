# 仓库维护约定

[根 README](../../README.md) 说明一笔转换、文件归属和运行入口。[工程合同](../CONTRACT_CURRENT.md) 给出接口和合法行为，[项目状态](../STATUS.md) 记录实测范围与待解决问题。涉及设计参数时同步核对运行源、测试和这些入口。

- 证据用 [A] 论文或讲稿、[P] 专利实施例、[E] 工程定义、[U] 未确定。报告记录实际输入、提交和可支持的验证范围。
- 改动 3+3+3 开关映射、桥接、扰动符号、电容模型或 ADC2 窗口时，提交原文依据和影响评估。
- 当前唯一数字整核为 [v21](../../rtl/README.md)。仿真、DC、FC 和 Formality 参考端从同一份生产清单加载，独立组件使用自己的输入和判据。
- 仿真前使用专用工作目录。规则及自查见 [EDA 工作目录规范](../audit/REMOTE_WORKDIR_POLICY.md)。

先运行所改模块的测试。Python 和静态检查入口在 [快速上手](../guide/QUICKSTART.md) 与 [CI 工作流](../../.github/workflows/ci.yml)。需要 VCS 的整核回归入口在 [仿真说明](../../flow/sim/README.md)。
