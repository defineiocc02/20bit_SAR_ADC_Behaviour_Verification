
## RTL 时序与协议证据

本节归档 2026-09-30 最终源码快照的功能仿真证据。原始编译命令、运行目录、退出码与完成标记见各参数的 manifest；源码及参数头文件哈希见 [final.sources.json](rtl/structural_adc_ppa_profiles.final.sources.json)。所有复制文件的 SHA-256 和字节数列于 [evidence_index.json](rtl/evidence_index.json)。不归档体积较大的编译日志。

### 完整物理配置与参数比较

使用同一份最终 RTL 快照，分别例化 `P_RECON_STAGES=5/6/7`，保持 16 拍节拍；每种配置都执行关闭 dither、采样 dither、量化器 dither 三种模式，并覆盖全部 18 个物理 slice。码值 oracle 使用物理 RDAC 开关和独立系数夹具，延迟检查从真实接受沿计数至该 sample ID 的输出提交沿。

| 参数 | 正确输出及逐笔延迟检查 | 检查时钟拍数 | 实测输出延迟 | 距下次 16 拍请求的余量 | 日志 |
|---:|---:|---:|---:|---:|---|
| 5 | 438 / 438 | 7680 | 15 拍 | 1 拍 | [run](rtl/structural_adc_tb.final.stages5.run.log) / [manifest](rtl/structural_adc_tb.final.stages5.manifest.json) |
| 6 | 438 / 438 | 7680 | 13 拍 | 3 拍 | [run](rtl/structural_adc_tb.final.stages6.run.log) / [manifest](rtl/structural_adc_tb.final.stages6.manifest.json) |
| 7 | 438 / 438 | 7680 | 11 拍 | 5 拍 | [run](rtl/structural_adc_tb.final.stages7.run.log) / [manifest](rtl/structural_adc_tb.final.stages7.manifest.json) |

三配置累计核对 1314 个输出及其延迟；以上 7680 是每个参数运行中的检查时钟拍数，不是独立测试用例数。编译和运行退出码均为 0，汇总见 [final.summary.json](rtl/structural_adc_ppa_profiles.final.summary.json)。最终 reducer 的 SHA-256 为 `30ed59581d584031a2ab36813e6caadad3752586c14c0a7a531edf8cf225b6e8`。归档前逐文件核对，该 final snapshot 与当时工作树一致。

独立 [三参数连续输入回归](rtl/recon_ppa_latency_tb.run.final.log) 对每个参数输入 32 笔、间隔严格为 16 拍，逐拍核对 busy、valid、结果码、sample ID 与 flags：busy 分别在接受后的第 14 / 12 / 10 拍释放，结果分别在第 15 / 13 / 11 拍提交，全部通过。该夹具使用小物理阵列，但保留真实重构的 63 位分子、64 位分母；完整 18-slice 数值归属由上表的顶层回归补充证明。

### 协议、负对照与 lint

[最终结构协议回归](rtl/structural_protocol_tb.run.final.log) 覆盖 120 帧、1920 拍：57 次合法启动、63 次预期拒绝，包含每个粗/细比较位置的单拍 valid 缺失、Flash 缺失、截止后迟到活动、后续帧恢复、ID/injection/fine code 归属，以及 reset/disable。两次相位总线扰动下，寄存后的模拟使能保持稳定。

[负对照 manifest](rtl/structural_protocol.negative_controls.json) 保留被测试源码与 TB 哈希。恢复原组合译码的 [baseline](rtl/structural_protocol.baseline.negative.log) 在 234 ns 触发时钟沿之间使能变化断言；只恢复组合 cancel 的 [变异](rtl/structural_protocol.cancel_mutant.negative.log) 在 242 ns 触发同一断言，两次退出码均为 1，属于预期失败。日志时间戳以 1 ps 为单位，manifest 以 ns 记录。这些是显式相位总线故障注入的结构证据，不是门延迟或硅片毛刺测量。

官方 runner 的严格生产 lint 检查了默认结构顶层、兼容顶层以及独立 `sadc_enc`，见 [structural](rtl/sar20_digital_core.structural.lint.final.log)、[compatibility](rtl/sar20_digital_core.compatibility.lint.final.log)、[encoder](rtl/sadc_enc.standalone.lint.final.log)。空日志表示未产生诊断输出；runner 在任一 lint 非零退出时终止，随后两个支持回归的完成输出见 [supporting runner](rtl/structural_ppa.final.supporting_runner.log)。这三项 lint 的命名表示设计层级配置，不等同于三种除法展开参数。

### 复跑与结论边界

基础回归入口为 `python tools/run_open_rtl.py --tops structural_protocol_tb recon_ppa_latency_tb structural_adc_tb`。三参数完整顶层的实际 Verilator 参数和临时构建目录保存在上表三个 manifest 中；关键参数为 `--top-module structural_adc_tb -GP_RECON_STAGES=5/6/7 --timing --assert --unroll-count 8192`。工具版本见 [version](rtl/version.final.log)。

以上证据证明相应数字功能、样本归属、异常恢复和 16 拍周期预算成立。仿真没有门延迟，不证明 640 MHz 收敛、面积/功耗降低、晶体管级建立精度或专利模拟电路已完整实现。报告图 `31_divider_profiles.png` 使用本节 final summary、三份顶层原始日志与连续输入日志绘制，完整数据及脚本哈希保存在报告的 `audit_figures/31_source_hashes.json`。
