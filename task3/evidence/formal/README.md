# CAAZ 有界形式验证

GitHub Ubuntu 24.04 工作流在 `f44e654e11bbc0f1a8f5c07228ce99ead6385e9d` 上执行 `yosys -s task3/tb/formal.ys`，Yosys 版本为 0.33。归档前核对了工作流产物 ZIP 的 GitHub SHA-256，并从提交对象重算 RTL、断言、脚本和工作流的 SHA-256。[运行详情](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42277211979) · [机器可读收据](receipt.json)

| 项目 | 结果 | 证据范围 |
| :--- | :--- | :--- |
| 安全性 | **通过** | 从第 1 步同步复位开始，19 条断言在 9 步内无反例。覆盖复位和故障门控、控制标签、配对元数据及精确差和向下取整。 |
| 配对可达性 | **通过** | 第 3、4 步出现 raw ID 0、1，分别正相、反相，均回报 AZ 代 0。第 5 步 `out_pair_o=out_valid_o=1`。脚本以 `-falsify` 求证配对可达，日志中第二次求解的 `model found: FAIL!` 是预期的存在性见证。 |
| 同工作流逐拍对拍 | **通过** | Verilator 5.020 共核对 2,896 拍、675 对配对、256 个旁路结果、10 次故障及 4 个 ID 边界。远端向量 SHA-256 与本机对拍一致。 |

G3 以该深度的安全性、配对可达性及逐拍对拍为验收依据。任意运行长度的归纳证明和真实模拟宏时序另行验收。G4 跟随整体物理流程。

[原始 Yosys 日志](caaz_formal.log) · [同工作流逐拍收据](rtl_replay_result.json) · [本机逐拍收据](../rtl/result.json)
