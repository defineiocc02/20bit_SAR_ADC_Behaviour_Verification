# 2026-09-30 RTL 与工具流程证据索引

## 最终共享读选修复

最新源码为 `71e7d5a`，26 文件 RTL 内容 SHA `89b9f8153fea…`。[最终 CI](ci_shared_71e7d5a/README.md) 9/9 项成功，完整 22 bench、三类 lint、独立算术与52条验收通过。四组直接证据分别为 [RTL](shared_old_trial_rtl/README.md)、[综合](shared_old_trial_synth/README.md)、[实际布线](shared_old_trial_route_25ns/README.md)、[完整网表 XSim](shared_old_trial_full_mapped/README.md)。

同条件 P5/25ns 核心 setup +0.946ns、hold +0.052ns，0路由/DRC错误；综合 LUT108842，比前代增加2.483%。40MHz核心对应2.5MS/s，OOC外部hold仍负，整板与ASIC640MHz未签核。下列各代历史记录按原日期、源码和状态解释。

## 历史记录

历史提交 `39e9c27` 的本地全量回归以 [final_rtl/result.json](final_rtl/result.json) 和 [冻结源码清单](final_rtl/source_manifest.json) 为准：完整运行官方 runner 当时注册的 **21 个 testbench，21/21 PASS**，进程退出码为 0；同时完成三个严格生产 lint 入口。这里的“完整”指该注册集合全部执行，不表示穷举整个芯片的状态空间。编译日志和大尺寸原始数据保留在外部工件，本目录只收录可核对的精简证据。

后继修复增加了权重最高恒零位的显式掩码，并将归约 miter 的输入驱动改为明确的事务边沿，同时加入独立 scalar oracle。原 `final_rtl` 原始日志保留：其 84 个冻结文件中，当前仅 `rtl/core/weight_store.sv` 与 `sim/tb/cal_weight_reduce_ppa_tb.sv` 已改变，不能再宣称该旧集合与当前工作树完全相同。新增证据见 [weight_msb](weight_msb/README.md) 与 [reducer_scheduler](reducer_scheduler/README.md)，其范围、负控、工具版本和精确计数分别记录。最新生产 RTL 的三参数复验见 [compact_profiles](compact_profiles/README.md)，不能将定向复验说成旧 21 项在新源码上已全部重跑。

## 早期发布与当时 CI 状态

发布提交为 [0c4a7726597c49044d25117a701ba780137ddf04](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/commit/0c4a7726597c49044d25117a701ba780137ddf04)。截至 2026-09-29 20:32 UTC，[CI 36626662809](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36626662809) 对应同一 head，状态仍为 `in_progress`。部分 job 已成功不能写成全绿，也不能引用较早提交的成功替代它。

`39e9c27` 的已知失败已修：RTL 在 Verilator 5.020 的归约测试第 13 次检查失败，源于该测试结构下的输入调度/重算问题；此前还存在新旧两份实现同时错误为零而 miter 接受的情况。修复增加明确事务边沿和独立 scalar oracle，双工具完整复验与共同错误负控见 `reducer_scheduler`。同次 Python CI 的镜像漂移修复同步了生产除法器的 `W_R=WA`、宽分母分类和借位减法。更早 `50b5733` 的大型编译 300 s 超时是另一件事，不能用它解释运行期断言失败。

## 当前证据入口与适用范围

| 目录 | 已观察的证据 | 不能替代的验证 |
|---|---|---|
| [final_rtl](final_rtl/result.json) | 旧 `169f…` 修复基线的 21 个真实 RTL 回归的原始 run.log、完成标记及源码冻结；含校准、协议、完整 20 位码域 oracle、树映射 RTL 夹具和除法穷举 | RTL 无门延迟；不是全芯片形式证明、映射后整机等价、模拟晶体管验证或 STA |
| [final_profiles](final_profiles/structural_adc_ppa_profiles.final.summary.json) | 旧 `169f…` 修复基线的 `P_RECON_STAGES=5/6/7`；每项 438 个正确输出及逐笔延迟检查、7680 拍，三项共 1314 个输出；覆盖三种 dither 模式和全部 18 个物理 slice | 周期预算不等于 640 MHz 实际时序闭合；不能由参数展开程度直接推断面积或频率改善 |
| [compact_profiles](compact_profiles/README.md) | 当前 `fd885…` 26 文件 RTL 集合，P5/P6/P7 各 438 输出、438 延迟检查、7,680 协议节拍，覆盖三模式和 18 slice，延迟 15/13/11 拍 | 不是全部 21 项重跑，也没有重跑旧 32 笔连续启动夹具；不是 Vivado/STA |
| [reducer_scheduler](reducer_scheduler/README.md) | 修复后的同一 TB 在 5.020 与 5.49 各通过 1,430,848 次独立 scalar 检查及新旧比较、7 几何和 43,758 个生产分配；双方共错负控在第 9 次失败 | 只修测试台、未改生产 reducer；两个工具的样本不能相加为新覆盖；无综合或时序结论 |
| [weight_msb](weight_msb/README.md) | 复位后 bit47 恒零不变量；旧/新写入语义对独立状态模型 3,023 步、2,411,234 次逐字访问通过；3 类 lint 与 3 项既有 bench 通过 | 48-bit 接口和合法范围不变；不证明新网表已少 1,278 FF 或已经提速 |
| [vivado_structure](vivado_structure/README.md) | 修复连接后的有效旧基线 P7：171,246 物理 LUT、66,817 FF、20 DSP、0 BRAM；保留 DCP 摘要与层级结构审计 | 非当前 compact 综合结果；EDIF raw LUT primitive 数不等于物理 LUT；未提供 route 或完整顶层功能等价 |
| [divider](divider/README.md) | 独立任意精度 oracle：40,010 MAC、40,000 ADC2、10,048 除法与 8 组 flags 序列；六种小位宽组合共 67,592 项穷举；两个真实错误变异均被检测 | 小位宽穷举不是完整 63/64 位形式证明；算术功能结果不证明映射延迟 |
| [tree_mapping](tree_mapping/README.md) | 两种修复公式的真实 Vivado/XSim 小型映射网表各 12,904 PASS，完整 CSV 一致；969/969 输入有端点；旧连接版的 Flash 与归约负控失败；两种修复 reducer 的原公式 miter 各 1,430,848 PASS | 这是 3-slice 小型组合见证，不能替代生产尺寸全芯片网表等价、STA 或 ASIC PPA；旧版 2 LUT 是错误映射结果 |
| [clockless_mock](clockless_mock/manifest.json) | 9 项本地 Tcl mock，验证查询失败、有限覆盖、常量链分类等流程行为 | 没有实际读取 DCP；不证明真实设计全部无时钟路径已解释或全部端点有约束 |
| [vivado_impl_mock](vivado_impl_mock/manifest.json) | 32 项本地 Tcl mock，验证实现脚本状态、异常处理、结果完整性与失败判定 | 没有运行 Vivado 实现；不证明布线完成、setup/hold 通过或 FPGA/ASIC 时序签核 |

最新 compact 参数回归的实际接受至提交延迟为 **15/13/11 拍**（参数 5/6/7），距 16 拍设计预算分别剩 **1/3/5 拍**。旧版独立连续输入夹具每项 32 笔，实测 busy 在第 **14/12/10 拍**释放，日志见 [recon_ppa_latency](final_profiles/recon_ppa_latency_tb.run.final.log)；该 streaming 夹具未在 compact 这轮重跑。两代记录按原来源保留，不合并成一次新实测，也不是纳秒延迟。

Python [位级镜像](../../../sim/ref/recon_rtl_mirror.py) 的 [六组小几何穷举](../../../tests/unit/test_recon_mirror.py) 共 55,432 对输入，包含 308 个零分母、31,744 个高分母分类用例，覆盖最负数及非整除展开 padding；25 项镜像测试通过。它不同于 `divider` 中 67,592 项 RTL 穷举。另一次本地非 slow Python 回归记录 918 passed、3 deselected，其后新增的完整映射流程测试独立验证。来源在交付工作区 `outputs/sar_adc_vivado_20260930/python_regression/manifest.json` 与 `full_mapped_flow_checks/mirror_independent_review.json`；这些外部文件尚未复制成本索引下的独立归档，不能把测试数量混作 RTL 向量数。

负对照属于检测能力证据，不能当作正常工作结果。`tree_mapping` 保留旧网表实际 `Z` 对参考 `0`、实际归约 `0` 对参考 `912` 的失败行；`divider` 保留高分母保护和负数 floor 修正被移除后的失败。早期 [rtl/协议负对照](rtl/structural_protocol.negative_controls.json) 保留原组合使能/cancel 的显式相位扰动失败；它证明该断言能检测注入的沿间变化，不是物理毛刺测量。

## 哈希核对与历史边界

2026-09-30 交付检查逐字节核对：`final_rtl/sha256.json` 的 27 项、`final_profiles/sha256.json` 的 15 项、`divider/sha256.json` 的 10 项、`tree_mapping/evidence_index.json` 的 31 项均匹配；除索引文件自身外，没有遗漏归档文件。两个 mock 的 run.log 哈希和合计 5 项来源文件也与当前文件匹配。`final_rtl` 的 84 项冻结来源在归档时全部匹配当时工作树；21 份日志均与 result 的哈希、runner 完成标记一致。后续两项源码变化已在上文单独列出。

当前关键 RTL、测试台和镜像的 SHA-256 前缀如下；完整哈希以来源清单为准：

| 文件 | 当前来源哈希前缀 |
|---|---|
| `rtl/core/cal_weight_reduce.sv` | `c5fc7f6ef58b8e74` |
| `rtl/core/div_floor.sv` | `cc3b0bd9c5e6c849` |
| `rtl/top/sadc_enc.sv` | `c366ddd1af00bfbf` |
| `rtl/core/weight_store.sv` | `d4e8e6d6b36ce5f5` |
| `sim/tb/cal_weight_reduce_ppa_tb.sv` | `4e7d97c5d3fb7339` |
| `sim/ref/recon_rtl_mirror.py` | `0e48e8ae94307ec4` |
| `tools/run_open_rtl.py` | `8f3cfa9dbc6fd39c` |

`final_profiles` 对应旧冻结实现的 30 项 RTL、头文件和相关 TB；后继 `weight_store` 修改另有独立证据，新参数回归不会覆盖这些旧日志。该参数实验冻结的 runner 是 `b543a8063e79d279…`，相对当前版本只缺后来增加的 `tree_mapping_tb` 注册及对应 wrapper 输入；已读取原冻结文件核对差异。不能将两份 runner 说成字节相同；历史 21 项完整回归所用 runner 为 `8f3cfa9d…`；即使 runner 字节未变，其测试台和 RTL 仍须分别绑定。

[rtl](rtl/evidence_index.json)、[arithmetic](arithmetic/sha256.json)、[reduction](reduction/sha256.json) 是较早历史快照，原始记录保留。它们引用的 reducer `30ed5958…`、divider `f798b5d5…`、encoder `2471ca33…`（依目录所含模块而定）与当前修复版不同，不能作为最新 RTL 的通过证据。历史文件名中的 `final` 只表示当时实验的最终快照，不代表本次交付版本。后续证据按来源依次由 `final_rtl`、`final_profiles`、`divider`、`tree_mapping` 及本次 `reducer_scheduler`、`weight_msb`、`compact_profiles` 补充，不能把任一历史目录整体改标为当前版本。

`tree_mapping` 内的旧版与 `baseline_fixed` 来源差异是故意保留的实验变量：后者采用旧四树公式加连接修复，当前生产版采用列共享公式加相同连接修复。完整 CSV、网表、早期启动/传输失败记录的外部路径及 SHA 见其 [evidence_index.json](tree_mapping/evidence_index.json)；不把旧失败 manifest 改写成通过。

## 有效综合结果与仍待验收的流程

有效 repaired baseline P7 使用 Vivado 2018.3、`xc7vx690tffg1761-2`、请求周期 1.5625 ns：**171,246 LUT、66,817 FF，WNS −17.426 ns**，状态为 `SYNTH_COMPLETE_TIMING_NOT_MET`。它使用旧四树公式、旧除法器和旧权重存储，不是当前 `fd885…` 的测量。结构与 DCP 来源见 [vivado_structure/archive_manifest.json](vivado_structure/archive_manifest.json)。WNS 的外部原始来源为交付工作区 `outputs/sar_adc_vivado_20260930/repair_baseline/synth/artifacts/vivado/20260929T190955.260429Z_p7_recovered/out/status.txt`，SHA-256 为 `87fa6c2d414d009e16214f0e469d714974481f068802e7985517e16579b69ba3`。

更早 baseline/candidate DCP 因 `Synth 8-3848` 无驱动、树根常量化已被判为无效诊断样本，其小 LUT/FF 数不能用来计算优化比例。Windows 外层 raw return code 0 不是成功依据；原始失败 manifest 保留，后续审查结果另存。

当前同约束 baseline/optimized 比较仍在执行，尚无最终差值。实际 buffered route、setup/hold 与约束完整性、完整生产顶层 mapped functional 结果仍待验收。脚本、公共端口 TB 及 CSV 审计器的准备/本地资格验证不等于远端 EDA 已通过；入口见 [buffered 实现说明](../../rtl/BUFFERED_OOC_IMPLEMENTATION.md) 和 [完整映射说明](../../rtl/FULL_TOP_MAPPED_FUNCTIONAL.md)。640 MHz 按用户确认是 ASIC 目标，FPGA 按实际可实现频率报告差距。

## 回归入口与 CI 边界

当前 [CI](../../../.github/workflows/ci.yml) 无 `--tops` 筛选调用 `python tools/run_open_rtl.py`，因此执行全部 21 项。该 runner 的注册名称与 `final_rtl/result.json` 的 21 项一致，但归约 TB 与权重源码已有上述后继修改；`tree_mapping_tb` 单独加入 `tree_mapping_dut.sv`。RTL job 上限 45 分钟；大型 `cal_weight_reduce_ppa_tb` 编译上限 **900 秒**，其他编译 300 秒；该 miter 的运行上限仍为 300 秒，严格 lint 为 120 秒。900 秒修复只放宽大型编译预算，没有删除测试或放松完成标记检查。独立算术 oracle 和六组综合/实现/映射流程测试也已接入 CI，`0c4a772` 发布前冻结的流程测试实测共 230 项通过（29+51+32+9+67+42）；它们使用 mocks，不能替代实际 EDA。

这里核对的是本地配置和已有实跑证据；不据此声称新的远端 CI 已通过。真实综合、实现和约束完整性必须以对应冻结来源的厂商原始报告另行判断。

## 后续物理验收流程补强

[physical_flow_checks](physical_flow_checks/README.md) 记录六组流程共272项通过，其中缓冲时钟流程109项。新增脉宽、失败端点、约束覆盖及真实2018.3报告解析检查；这些是流程验证，不是真实布线结果。源码摘要与原始日志单独封存。

## CI 0c4a 的已完成结果与后续修复

该轮CI已完成：8个job成功；RTL job内的21个bench和3类lint成功，但随后的独立算术审计因工具入口路径错误失败，因此整体CI为FAILED。[ci_rtl_0c4a](ci_rtl_0c4a/README.md) 精确记录受测merge与head整树一致及原日志。[arithmetic_runner_ci](arithmetic_runner_ci/README.md) 记录工具入口修复、随后发现的5.020文件输入调度缺陷、两版全量算术通过和两个陈旧输入负控；生产RTL没有因此改动。新提交需由自身CI验证，不能把局部通过写成此前job全绿。
