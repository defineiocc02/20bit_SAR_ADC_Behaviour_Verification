# 2026-09-30 RTL 与工具流程证据索引

提交 `39e9c27` 的本地全量回归以 [final_rtl/result.json](final_rtl/result.json) 和 [冻结源码清单](final_rtl/source_manifest.json) 为准：完整运行官方 runner 当前注册的 **21 个 testbench，21/21 PASS**，进程退出码为 0；同时完成三个严格生产 lint 入口。这里的“完整”指该注册集合全部执行，不表示穷举整个芯片的状态空间。编译日志和大尺寸原始数据保留在外部工件，本目录只收录可核对的精简证据。

后继修复增加了权重最高恒零位的显式掩码，并将归约 miter 的输入驱动改为明确的事务边沿，同时加入独立 scalar oracle。原 `final_rtl` 原始日志保留：其 84 个冻结文件中，当前仅 `rtl/core/weight_store.sv` 与 `sim/tb/cal_weight_reduce_ppa_tb.sv` 已改变，不能再宣称该旧集合与当前工作树完全相同。新增证据见 [weight_msb](weight_msb/README.md) 与 [reducer_scheduler](reducer_scheduler/README.md)，其范围、负控、工具版本和精确计数分别记录。完整新提交的 CI 状态另行确认。

## 当前证据入口与适用范围

| 目录 | 已观察的证据 | 不能替代的验证 |
|---|---|---|
| [final_rtl](final_rtl/result.json) | 21 个真实 RTL 回归的原始 run.log、完成标记及源码冻结；含校准、协议、完整 20 位码域 oracle、树映射 RTL 夹具和除法穷举 | RTL 无门延迟；不是全芯片形式证明、映射后整机等价、模拟晶体管验证或 STA |
| [final_profiles](final_profiles/structural_adc_ppa_profiles.final.summary.json) | 最终 RTL 的 `P_RECON_STAGES=5/6/7`；每项 438 个正确输出及逐笔延迟检查、7680 拍，三项共 1314 个输出；覆盖三种 dither 模式和全部 18 个物理 slice | 周期预算不等于 640 MHz 实际时序闭合；不能由参数展开程度直接推断面积或频率改善 |
| [divider](divider/README.md) | 独立任意精度 oracle：40,010 MAC、40,000 ADC2、10,048 除法与 8 组 flags 序列；六种小位宽组合共 67,592 项穷举；两个真实错误变异均被检测 | 小位宽穷举不是完整 63/64 位形式证明；算术功能结果不证明映射延迟 |
| [tree_mapping](tree_mapping/README.md) | 两种修复公式的真实 Vivado/XSim 小型映射网表各 12,904 PASS，完整 CSV 一致；969/969 输入有端点；旧连接版的 Flash 与归约负控失败；两种修复 reducer 的原公式 miter 各 1,430,848 PASS | 这是 3-slice 小型组合见证，不能替代生产尺寸全芯片网表等价、STA 或 ASIC PPA；旧版 2 LUT 是错误映射结果 |
| [clockless_mock](clockless_mock/manifest.json) | 9 项本地 Tcl mock，验证查询失败、有限覆盖、常量链分类等流程行为 | 没有实际读取 DCP；不证明真实设计全部无时钟路径已解释或全部端点有约束 |
| [vivado_impl_mock](vivado_impl_mock/manifest.json) | 32 项本地 Tcl mock，验证实现脚本状态、异常处理、结果完整性与失败判定 | 没有运行 Vivado 实现；不证明布线完成、setup/hold 通过或 FPGA/ASIC 时序签核 |

最终参数回归的实际接受至提交延迟为 **15/13/11 拍**（参数 5/6/7），距下一次 16 拍请求分别剩 **1/3/5 拍**。独立连续输入夹具每项 32 笔，实测 busy 在第 **14/12/10 拍**释放，日志见 [recon_ppa_latency](final_profiles/recon_ppa_latency_tb.run.final.log)。这些是实际周期计数，不是纳秒延迟测量。

负对照属于检测能力证据，不能当作正常工作结果。`tree_mapping` 保留旧网表实际 `Z` 对参考 `0`、实际归约 `0` 对参考 `912` 的失败行；`divider` 保留高分母保护和负数 floor 修正被移除后的失败。早期 [rtl/协议负对照](rtl/structural_protocol.negative_controls.json) 保留原组合使能/cancel 的显式相位扰动失败；它证明该断言能检测注入的沿间变化，不是物理毛刺测量。

## 哈希核对与历史边界

2026-09-30 交付检查逐字节核对：`final_rtl/sha256.json` 的 27 项、`final_profiles/sha256.json` 的 15 项、`divider/sha256.json` 的 10 项、`tree_mapping/evidence_index.json` 的 31 项均匹配；除索引文件自身外，没有遗漏归档文件。两个 mock 的 run.log 哈希和合计 5 项来源文件也与当前文件匹配。`final_rtl` 的 84 项冻结来源在归档时全部匹配当时工作树；21 份日志均与 result 的哈希、runner 完成标记一致。后续两项源码变化已在上文单独列出。

当前关键 RTL 的 SHA-256 前缀如下；完整哈希以来源清单为准：

| 文件 | 当前来源哈希前缀 |
|---|---|
| `rtl/core/cal_weight_reduce.sv` | `c5fc7f6ef58b8e74` |
| `rtl/core/div_floor.sv` | `cc3b0bd9c5e6c849` |
| `rtl/top/sadc_enc.sv` | `c366ddd1af00bfbf` |
| `tools/run_open_rtl.py` | `8f3cfa9dbc6fd39c` |

`final_profiles` 对应旧冻结实现的 30 项 RTL、头文件和相关 TB；后继 `weight_store` 修改另有独立证据，新参数回归不会覆盖这些旧日志。该参数实验冻结的 runner 是 `b543a8063e79d279…`，相对当前版本只缺后来增加的 `tree_mapping_tb` 注册及对应 wrapper 输入；已读取原冻结文件核对差异。不能将两份 runner 说成字节相同；21 项最终完整回归使用当前 `8f3cfa9d…` runner。

[rtl](rtl/evidence_index.json)、[arithmetic](arithmetic/sha256.json)、[reduction](reduction/sha256.json) 是较早历史快照，原始记录保留。它们引用的 reducer `30ed5958…`、divider `f798b5d5…`、encoder `2471ca33…`（依目录所含模块而定）与当前修复版不同，不能作为最新 RTL 的通过证据。历史文件名中的 `final` 只表示当时实验的最终快照，不代表本次交付版本。最新功能结果分别由 `final_rtl`、`final_profiles`、`divider` 与 `tree_mapping` 补充。

`tree_mapping` 内的旧版与 `baseline_fixed` 来源差异是故意保留的实验变量：后者采用旧四树公式加连接修复，当前生产版采用列共享公式加相同连接修复。完整 CSV、网表、早期启动/传输失败记录的外部路径及 SHA 见其 [evidence_index.json](tree_mapping/evidence_index.json)；不把旧失败 manifest 改写成通过。

## 回归入口与 CI 边界

当前 [CI](../../../.github/workflows/ci.yml) 无 `--tops` 筛选调用 `python tools/run_open_rtl.py`，因此执行全部 21 项。该 runner 的注册表与 `final_rtl/result.json` 的 21 项一致；`tree_mapping_tb` 单独加入 `tree_mapping_dut.sv`。RTL job 上限 45 分钟；大型 `cal_weight_reduce_ppa_tb` 编译上限 **900 秒**，其他编译 300 秒；该 miter 的运行上限仍为 300 秒，严格 lint 为 120 秒。900 秒修复只放宽大型编译预算，没有删除测试或放松完成标记检查。独立算术 oracle 和六组综合/实现/映射流程测试也已接入 CI，当前流程测试实测共 230 项通过；它们使用 mocks，不能替代实际 EDA。

这里核对的是本地配置和已有实跑证据；不据此声称新的远端 CI 已通过。真实综合、实现和约束完整性必须以对应冻结来源的厂商原始报告另行判断。
