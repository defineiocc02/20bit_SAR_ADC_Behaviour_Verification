# Python 与 RTL 的分层验证

本目录保存早期 `sar20_digital_core` 的独立整数参考、V1–V4 验证脚本和阶段档案。现行 v21（`adc_digital_ip_v21`）由 [verify_v21.sh](../../tools/verify_v21.sh) 组织回归。独立 core 的源绑定、定点和 DSP 组件在 [CI](../../.github/workflows/ci.yml) 通过 [run_open_rtl.py](../../tools/run_open_rtl.py) 和专项测试选取输入。 [仿真导航](../sim/README.md) · [工程合同入口](../../docs/CONTRACT_CURRENT.md)

## 输入与比较器

| 位置 | 作用 |
| --- | --- |
| [src/adi_model/](../../src/adi_model/) | 行为模型使用电荷、采样及量化假设，供机理研究和误差诊断。 |
| [contracts/](contracts/) · [reference/](reference/) | 冻结 V1 数值合同及独立整数实现，定义舍入、截断、饱和与逐拍状态。 |
| [vectors/](vectors/) · [rtl/tb/](rtl/tb/) | 生成显式位宽的定向／随机激励，采集 DUT 输出及标志。读取 `.mem` 时按元数据位宽扩展符号。 |
| [cosim/scoreboard.py](cosim/scoreboard.py) · [reports/](reports/) | 比较固定点字段，定位首处差异。报告与测试须绑定该次使用的输入、源码和工具。 |

[旧验证报告](reports/VERIFICATION_REPORT.md) 记载 2026-11-12 运行。归档的 [V1 矩阵](reports/module_matrix.md) 列出 7 个模块、4,931 条向量，属于当轮输入。随后 `unit_therm` 撤回不能由 6 位端口表达的越界输入，当前 [向量元数据](vectors/generated/unit_therm/meta.json) 为 84 条，旧矩阵为 96 条。[results_module.json](reports/results_module.json) 记录另一次 `NOT_RUN`，无需将两次记录合为一个结果。

| 层 | 记录 | 边界 |
| --- | --- | --- |
| V1 模块 | [旧覆盖矩阵](reports/module_matrix.md) · [另一次运行](reports/results_module.json) | 数值字段、标志与模块延迟。 |
| V2 逐拍 | [阶段报告](reports/VERIFICATION_REPORT.md) | 部分状态与时间关系已对拍，完整跨模块统一轨迹仍待补齐。 |
| V3 顶层 | [ELABORATED_ONLY](reports/results_rtl_top.json) | 只记录展开与复位 idle，配置装载、样本流及反压未在该轮覆盖。 |
| V4 闭环 | [NOT_RUN](reports/results_closed_loop.json) | 需要将 RTL 的逐事件开关命令与 Python plant 的 ADC2 返回绑定。 |

## 复核旧工具

在仓库根目录运行 `PYTHONPATH=src:flow python3 -B -m verification.run_verification --help` 查看选项。若需静态交叉核对旧参数头与 YAML，可用 `--level contracts --out DIR`，其中 `DIR` 为新建的仓库外目录，解析 YAML 需 PyYAML。归档的 [contracts 结果](reports/results_contracts.json) 只对应旧合同参数。

旧 VCS staging 仍以 `flow/` 为源码搜索根，当前 RTL 实际位于仓库根的 `rtl/`。因此先验证输入路径、主机与工具，再使用 `--level module` 或 `rtl_top` 重放。脚本允许 `NOT_RUN` 退出码为 0，判定应读取本次 JSON 的 `status` 和各模块记录。向量生成器会写入现有的 `vectors/generated/`，如需复算旧向量，先在隔离副本对照所选基线。 [VCS staging](cosim/vcs_runner.py) · [统一入口](run_verification.py)
