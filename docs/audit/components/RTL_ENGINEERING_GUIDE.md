# RTL 工程与调试入口

当前唯一的集成 ADC 为 **v21，13 位 ADC2、0.75 V**。从 [RTL 导航](../../../rtl/README.md)查找顶层与运行清单，从 [工程合同](../../CONTRACT_CURRENT.md)核对码域、位宽及算术，从[项目状态](../../STATUS.md)确认验证范围与缺口。

核对一笔 v21 结果时，在同一个样本身份下查采样片组、已决事件、最后生效的开关、ADC2 返回以及重构状态。正式回归由 [verify_v21.sh](../../../tools/verify_v21.sh) 组织。每次运行保留工具版本、源文件 SHA、参数和日志。

源绑定、DSP、定点算术在 CI 中作为组件检查。[组件接口](CURRENT_INTERFACE_AND_ARITHMETIC.md)说明所用输入包和观测格式。过去独立核、有限窗及旧工艺结果的复核以[冻结证据表](../../_history/archive/digital_status_matrix.md)和相应的 Git 提交为准。
