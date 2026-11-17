# 工作树结构与版本一致性独立审查

- 审查对象：`merge/adi-repro`，`383112d673980dbd3d7a9e817a940309ff40c2c4`（下称「审查快照」）。`ebc9a8c` 是祖先，领先 1 个提交。起始无暂存、未暂存或未忽略的新增文件；Git 跟踪 1,046 个文件，本地另有 438 个忽略文件。见 **E01、E12**。
- 口径：用 Git 跟踪树、工作树存在性、源码引用、入口脚本与归档资料作静态核对。表中「文档称」是待核查主张；「实测」指本次读取、计数或引用解析。「历史报告记录」不等同于本次运行。复核命令都从仓库根运行，编号对应文末命令块。计数固定在审查快照，不含本报告。
- 交付只新增本报告；未运行回归、VCS、DC、FC、Formality，未连接远端，也未变更现有文件和 Git 索引。

## ① 结论摘要

- **版本管理存在会阻断复现的高严重度问题。** D3/D4 远端同步、v21 的部分编译入口和干净克隆/CI 的取文件路径彼此脱节。**E05、E07、E08**
- v21 顶层有 **87 个命名端口、8 个直接实例、10 个子模块类型（含传递依赖）**；验收脚本静态定义 **9 个 RTL 用例**。README 的 79 个端口、8 个用例已过期。**E03、E04**
- 三个 Formality 通过的候选模块都未接入 v21，但仍由归档顶层实例化；按「任何顶层」定义，已知 7 个有通过记录的模块中 **没有孤儿**。**E04、E06**
- 归档 RTL 三个文件全部仍有依赖；两个归档 Python 未见执行引用；文档归档中的行为指纹已迁走，活动门禁仍读旧路径。**E07、E08**
- 文档计数有正确项，也有过期分项；审计索引保留了已撤回的后端结论。跟踪树无常见临时波形/日志/缓存，有 **7 个 >1 MB 文件**及本机路径和账号标识。**E02、E09、E12**

## ② 问题清单

| 问题 | 严重度 | 证据命令 | 建议 |
| --- | --- | --- | --- |
| **P01** D3/D4 远端验收把归档顶层平铺到 `$WORK/rtl/`，L2/L2.6 编译却从 `rtl/_legacy/tops/` 取；新远端目录缺源，旧目录可能掩盖。`rtl/README.md` 称路径已同步且验证链完整，本轮源码不支持干净远端这一结论。 | 高 | **E07**：逐行对照 47、66–67、104、141。 | 保持上传与清单的相对路径一致；准备阶段逐项校验源文件，历史 PASS 保留执行环境和提交。 |
| **P02** `files_adc_v21.f`、继承它的 `files_adc_v21_sim.f` 以及 `fc_rtl_to_gds.tcl` 漏了 `recip_rnd_v21.sv`、`recip_q60.sv`，而 `recon_v21` 传递依赖二者。`verify_v21.sh`、DC、FM 的独立清单包含两者。 | 高 | **E04、E05**：实例路径与清单对照。 | 确定可复用的生产源清单；仿真在其上附加节点宏/TB，独立模块实验显式分开。 |
| **P03** `verify_clean_clone.sh` 仍执行根级 `tests/…`，而跟踪测试位于 `flow/tests/`；其指纹检查和工程自检 C7 固定读已迁走的文件。工程自检 C4 的 `figures.json`、`p4_ablation.json` 在快照也缺失。 | 高 | **E08**：路径、缺失文件、现有指纹三方对照。 | 把仍属门禁的基线移到明确的受控位置，并更新消费者；废弃的验证步骤要同时移除入口。 |
| **P04** CI 的 sdist 校验固定读缺失的 `docs/readme/data/source_bound_assets_manifest.json`；普通单测另读同目录四份缺失的 JSON/NPZ。另有两个测试把 `rtl_sources.f` 当作全部 RTL 的清单（实测 18/44），以及一个测试仍断言现 README 包含已经删去的增益说明。 | 高 | **E08**：CI/测试源码、Git 树和集合差集。 | [需仓主决策] 定义发版仍需保留的样本与清单覆盖范围，再同步 CI；不要用删除测试掩盖有效门禁。 |
| **P05** 三个归档顶层仍有消费者；同一归档提交同步了一些清单和 Tcl，却遗留 `digital_top.sv` 的平级搜索和 `digital_top_w.sv` 的平级通配。旧 DC 手册所列上传路径和源集合也不够。 | 高 | **E07**：旧文件去向、编译源和 TB 实例。 | [需仓主决策] 给继续维护的旧链指定入口和依赖；其余标为只读快照，废弃入口停用前先量化影响。 |
| **P06** 审计索引 `:28–29` 仍写「合计 160,951 µm²」「4 物理不可行」，与同文 `:14–15,84–85` 及 STATUS `:128–129` 冲突。另有「13/13 DC 完成」，但汇总的 13 行数据中包含强制 alpha 旁路诊断的 `recon_v21`；不能推为生产配置 13/13。 | 高 | **E09**：撤回条目、层级嵌套、DC 汇总原文。 | 全部入口统一为「13 行单模块记录，含诊断配置」「4 项在冻结输入和给定预算下未完成」；保留历史但标注限定。 |
| **P07** README/STATUS/INDEX 绝对化的「顶层从未综合成功」与专项记录的旧网表矛盾；审计索引笼统写 D2/D3「未在顶层实例化」，源码已连 `sar_evt_v21` 与 `dem_mask_v3`；缺的是特定相位/跟随模块。 | 高 | **E03、E04、E06、E09**。 | 分开写旧结果、当前 HEAD、整核与模块；明确哪一条 20 failing 属于哪个网表/设计。 |
| **P08** 合同与 v21 RTL 用 ADC2 13 bit/0.75 V，行为模型默认 14 bit/0.45 V；审计/待办把旧 12 bit/0.45 V 的 0.60 LSB20 口径归给 13 bit 合同。合同的 D3/D4 入口是叶子级，不能代替其所引验收表内的旧 IP 级入口；顶层相位输入外供。 | 高 | **E10**：常量、入口及量化步长复算。 | [需仓主决策] 确认两个 profile 的用途、接口和证据，标明工程合同 13 bit 与系统最终位宽待裁定的区别。 |
| **P09** 根 README 的回归数、端口数和 v21 文档索引，以及 INDEX 的审计数/历史子目录分项、RTL README 的目录数量有误。算法审计表为 14 行；待办 6 项中一项已完成；导航给了不同数字。 | 中 | **E02、E03、E04、E09、E11**。 | 导航用可执行的快照口径生成/校验；显示范围（目录根级、递归、历史、入口）和精确基线。 |
| **P10** 工作树快照无已跟踪临时日志/波形/缓存，但若干路径可误加；历史报告 PDF 当前仍被 `*.pdf` 规则匹配。7 个 >1 MB 文件含发布参考和历史件；仓库文本中有本机用户目录、远端工作目录、许可环境端点。 | 中 | **E12、E13**：有效忽略规则、Git 对象尺寸、只打印位置的路径扫描。 | [需仓主决策] 明确证据/资料保留与尺寸政策；限制白名单到确定文件；将活跃脚本的机器参数移到环境配置或本地样例。 |

六份文档的独立核对：

| 文件 | 版本字段/实际最后修改 | 文档声称与实测 |
| --- | --- | --- |
| `README.md` | 无 Git 版本字段；最后修改 `ebc9a8c`。`v2.0` 是权重接口修订名。 | `:16,71` 写 8 项；脚本 9 项，多 `tb_cfg_lifecycle`（40 checks 是脚本期望）。`:52` 写 79；实测 87（44 入/43 出）；`:117` 约 1,426 位，静态声明合计 1,432 位（309 入/1,123 出）。`:31–43` 的「四个顶层」是四个列举对象；三个已移到 `_legacy`，还另有 `rtl/top/sar20_digital_core.sv` 和 DSP 顶层。本文档将某些归档报告列为现行。**E03、E04、E11** |
| `docs/STATUS.md` | 声称 `a9193e4`，2026-11-17；最后修改 `ebc9a8c`，声称版本距 HEAD 5 个提交，分支正确。 | 9 项入口、三个具体模块没接 v21 已核实。`:73,77` 的 7 模块/2,189 点是历史模块报告，汇总加法成立，本次未重证。`:90` 的「从未」过宽；`:41,53` 的 §四.5 / §五.2 找不到对应论述。**E01、E03、E06、E09、E11** |
| `docs/INDEX.md` | 标 `ffc10ef`，2026-11-17；最后修改 `383112d`，基线距 HEAD 7 个提交。 | `127`、`105`、`14`、`v21=9`、`12 张 PNG` 均可复算。127 的定义只排除 `docs/_history`，仍计入 `audit/_superseded` 的 19 份；同时排除两层时为 108，不能把 127 理解为全是现行。审计实测 33 份 Markdown（根级 12、`artifacts`/`todo` 各 1、`_superseded` 19）/36 个文件，`32=11+21` 不成立。`_history/root_docs/` 不存在，`dfb21eb` 删除合并前的三个文件；其余见 E02。**E01、E02、E09、E11** |
| `docs/audit/README.md` | 标 `6df4ad2`，2026-11-17；最后修改 `95cccd1`，基线距 HEAD 9 个提交。 | 「现行 11 份」对应枚举 10 份报告 + 索引自身才为 11；当前同层另有审查提示词，连子目录非 superseded 共 14 份，不宜混用口径。`:114` 称另有 12 份只在历史；树里 `_superseded` 19 份，扣掉表中 7 份，余 12 仍可直接找到。`:18,26,28–29,37` 的数字与现行证据冲突。**E01、E02、E09** |
| `rtl/README.md` | 「`dfb21eb` 之后」，2026-11-17；最后修改 `ebc9a8c`，相对版本描述成立。 | 627 行、8 个直接实例准确；`:63`「8 个模块 + 顶层」实为 8 个 SV 文件总数（6 个综合子模块、1 个仿真宏、1 个顶层）。`:32` 的 `dem_phase_rr`「仅单模块验证」错误，旧 D3/D4 IP 默认实例化它；该 IP 的「6」是两种相位实现同时计入的文本数，单配置 5。旧远端验收不完整，见 P01。**E04、E07** |
| `docs/CONTRACT_CURRENT.md` | 规范版本 v2 + v2.1，无 Git 版本字段；最后修改 `b0be4e6`（2026-11-14）。 | v2.1 优先的表述与 INDEX 和两份合同一致。历史条目使用裸名，实际在 `docs/_history/`；`make verify-d3d4` 的范围仅是叶子级。入口摘要中的 bridge 应注明 `coin==1` 的条件；D3/D4 旧 IP 验收表和全局 STATUS 应声明各自适用范围。**E01、E10、E11** |


## ③ 多版本并存清单

实测 **9 组精确重名**，忽略大小写再多 1 组。「同名」先以全部跟踪的 RTL/Python/Markdown/相关源码扩展名做 basename 分组，并额外按大小写折叠；这些候选全部覆盖。再按 RTL 模块标识、源码实例、Python import、命令入口及文档明确替代关系核查异名版。普通 README、包初始化、独立对照模型不能仅凭 basename 判为过时；没有引用或替代证据的先保留为未判定。**E14、E15** 给出可重现全路径和引用。

| 同名组 | 并存文件 | 用途和现行判定 |
| --- | --- | --- |
| `source_acquisition_binding.sv`、`source_control_binding.sv`（各 2） | `flow/sim/fixtures/{source_control_component,fig19_ideal_ports}/` 各一组 | 各自 `binding.json` 对应源码校验；CI 用 `source_control_component`，`run_fig19_rtl_calibration.py` 用 `fig19_ideal_ports`。两套不同实现、相同模块名，都是各自配置的输入，不能一并编译。**E14、E15** |
| `fixed_point.py` | `src/adi_model/contracts/`、`flow/verification/reference/` | `adi_model` 的合同/导出器/模拟器与独立 scoreboard 的 reference 各自 import；保留两者。旧 cosim runner 的路径问题另列。**E15、E11** |
| `scheduler.py` | `src/adi_model/{control,simulation}/` | `control.module1` / 桥接用 control 的版本；行为仿真、公共包入口与测试用 simulation 的版本；不同命名空间。**E15** |
| `make_figures.py` | `docs/caaz/`、`flow/verification/reports/` | 分别由 CAAZ 文档和分级验证报告调用；不是替换关系。后者报告的部分示例仍用旧测试根。**E15** |
| `conftest.py` | `flow/tests/`、`subsys/extensions/caaz/tests/` | 前者是当前 CI 测试作用域；后者是 CAAZ 独立测试的适配器，但仍写 `from tests.conftest`，干净树无根级 `tests/`。**E08、E14、E15** |
| `__init__.py`（20） | `flow/verification`、`src/adi_model`、`subsys` 下不同目录的包标识，逐路径见 E14 | 这组是包标识，不是相互替代的版本。**E14、E15** |
| `README.md`（32） | 根目录及各子树，逐路径见 E14 | 根 README 是对外入口；索引、源码包、流程、证据目录各自 README，不能顺延为全仓替代。`docs/_history/v21_legacy/README.md` 为归档导航。**E02、E11、E14** |
| `STATUS.md`（2） | `docs/STATUS.md`、`docs/_history/archive/STATUS.md` | 根 README 与 INDEX 都导航到 `docs/STATUS.md`；另有异名归档 `STATUS_d5a1576.md`。旧 status 只作历史。**E03、E14、E16** |
| `architecture.md` / `ARCHITECTURE.md`（忽略大小写） | `docs/_history/archive/architecture.md`、`docs/guide/ARCHITECTURE.md` | 旧架构已移至历史；新架构由指南按显示文字引用，QUICKSTART 的目标仍写旧 `../design/ARCHITECTURE.md`。**E11、E14、E16** |

同模块名追加一组：`rtl/sched18.sv` 与 `docs/audit/artifacts/sched18_final.v` 都定义 `sched18`；前者进入 v21 和旧链，后者是保留的独立模块布局布线网表。布线证据不得混进 RTL 源清单。除它和两套 fixture 的模块标识外，跟踪的 `.sv/.v` 未发现其他重复定义。**E04、E14、E15**

| 依据引用确认的异名版本/实现族 | 并存文件 | 现行、并行或历史 |
| --- | --- | --- |
| 集成顶层 | `rtl/v21/adc_digital_ip_v21.sv`；`rtl/_legacy/tops/{d3d4_ip,digital_top_w,digital_top}.sv`；`rtl/top/{sar20_digital_core,sadc_enc}.sv`、`subsys/ad4080_dsp/rtl/{sar20_system_top,sar20_ad4080_top,ad4080_top}.sv` | v21 为该交付链的现行顶层。三份归档顶层的输入仍有消费者；早期 core 和 DSP 是独立入口（含 CI/开放仿真）而非 v21 旧文件。`sadc_enc` 也可独立验证。**E04、E07、E15** |
| 重构 / 权重 | `rtl/v21/{recon_v21,wsum_v21}.sv`；`rtl/recon_d5{,_w,_w2}.sv`；`subsys/archive/180nm/recon_d5_pipe{,2}.sv`；`rtl/core/{recon_core,cal_residue_mac,cal_weight_reduce,weight_store}.sv` | v21 两模块由顶层接入；`digital_top` 用 `recon_d5`，`digital_top_w` 用 `recon_d5_w2`，`_w` 仍有 TB；pipe/pipe2 有旧 TB/说明但无 v21 依赖。core 分支自身的 top/filelist 有在用的重构链。**E04、E07、E15** |
| DEM、扰动、跟随 | `rtl/{dem_mask_v3,dem_shuffle,dem_phase_rr,dem_phase_gen,dither_pre,dither_split,follower,follower_rt}.sv`；`rtl/v21/sar_evt_v21.sv` | v21 用 `dem_mask_v3/dither_pre/sar_evt_v21`，相位由外部输入；旧顶层仍调用 `dem_shuffle/dither_split/follower_rt`，`dem_phase_rr` 默认、`dem_phase_gen` 宏开关 A/B。`follower` 有旧 TB 等引用。按入口分开保留。**E04、E07、E15** |
| 调度 / ADC2 | `rtl/sched18.sv`、`rtl/slice_pool.sv`、`rtl/core/slice_pool_ctrl.sv`；`rtl/v21/{adc2_capture,adc2_node_macro}.sv`、`rtl/core/adc2_dec.sv` | v21 用 `sched18/adc2_capture`；`adc2_node_macro` 是外围仿真模型。`slice_pool` 和 `slice_pool_ctrl/adc2_dec` 分别仍有旧 TB 或 core 分支消费者，功能分层不同。**E04、E15** |
| Python 前向/闭环 | `tools/forward_model.py`、`tools/_legacy/fwd_model.py`；`tools/closed_loop.py`、`tools/_legacy/closed_loop2.py` | `forward_model` 由 `closed_loop/regression4/dem_abcd/dem_spectrum/dither_physical` 实际导入；`closed_loop` 是可单独调用的 WIP，未见主入口反向调用。两份 `_legacy` 没有活动执行引用；`closed_loop2` 迁移后自身的 import 路径也不自包含。合同旧名字尚未改。**E07、E15** |
| Python 其他原型与现行入口 | `tools/{e2e,run_review2_figure,run_review_figures}.py`；`src/adi_model/{scheduler18,sched18_rtl}.py` | `e2e.py` 文件头自称唯一，实际公开入口是 `pyproject.toml` → `adi_model.cli:main_simulate`。`Scheduler18` 仅本文件使用，`sched18_rtl` 被向量/统计工具选择。旧 `run_review2_figure` 用 63+8 且导入已搬的 `tests.unit`；`run_review_figures` 由本轮图索引和干净克隆入口调用，图表范围并不等同。**E15** |
| 合同与旧接口 | `docs/CONTRACT_CURRENT.md`、`docs/ISSCC2024_ONCHIP_CONTRACT_v2.md`、`docs/ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md`；历史 `CONTRACT_V2_REVIEW`、`PHYSICAL_CONTRACT_DRAFT`、`D2_INTERFACE_SPEC` 等 | README 和 INDEX 指向唯一合同导航；v2 主体与 v2.1 补充同时生效，冲突用 v2.1。旧 D3/D4 接口页对应不同交付链，不代替 v21 配置合同。**E10、E16** |
| 全局整合和验收状态 | `docs/STATUS.md`、`docs/ACCEPTANCE_STATUS.md`，历史 `STATUS_d5a1576`、`archive/STATUS`、两份 `ACCEPTANCE_2026111{2,3}`、`status_w8` 等 | 全局入口由 README/INDEX 指定；`ACCEPTANCE_STATUS` 的具体内容是旧 D3/D4 局部验收。相近历史状态/检查点仍在树，不能拿旧口径覆盖当前源码。**E03、E10、E16** |
| v21 过程记录 | `docs/v21/{FORMAL_STATUS,FORMAL_DIAGNOSIS,FORMAL_RND96,SYNTH_STALL,PROOF_LEDGER,…}.md`；历史 `v21_checkpoints/CHECKPOINT_*.md`、`v21_legacy/{CHECKPOINT_I5_*,GATE_EQUIV_superseded,TIMING_BUDGET_FINAL_superseded,DRIVING_CELL_BUG_superseded,PPA_DIV_VS_RECIP,…}.md` | 现行状态与按主题的归档来源分开；I1/I2/I12、I3/I3_FULL、I4/I4B 与 I5 记录是不同阶段，不互换。README 中把多份旧证据列在现行目录。**E06、E11、E16** |
| ABI2 现行和早期算术规范 | `docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md`、`docs/rtl/RTL_ARITHMETIC_CONTRACT.md`、`P1_INTERFACE.md`、`P2_INTERFACE.md` | 工程指南及旧规范自述指向 ABI2 作为 `sar20_digital_core` 路线现行；`RTL_ARITHMETIC_CONTRACT` 明写历史，P1/P2 引用的是历史阶段合同，均不可跨用到 v21。相邻指南还写 `rtl_sources.f` 有 20，实测只有 18。**E08、E16** |
| 后端结论 / wsum | `docs/audit/{ALL_13_MODULES_FINAL,WSUM_FINAL_VERDICT}.md` 与 `docs/audit/_superseded/*.md` | 审计索引明确给出替代表；当前主张须以更正后的限定为准。`TWO/THREE/FOUR/SIX_MODULES_PASSED` 和 `BACKEND_FINAL*`、FC 分支/PG 探索是不同范围快照；`WSUM_{PHYSICAL_COST,PLACEMENT_OVERLAP}` 被现报告取代。**E09、E16** |
| 多轮审查回复、release 证据 | `docs/_history/archive/review_response_2026-09-11{,b,c,d,e}.md`；`docs/_history/release_v8.2.{0,1,2}/` | 均可按段落、标题和各自版本证据区分；没有在现行入口被指定为当前状态。是历史序列。**E07、E16** |

本地另有 **190 份**与跟踪源同 basename 的忽略文件（148 个 `.sv`、42 个 `.vh`，30 种 basename）；比较同名所有候选的内容，186 份与至少一份跟踪文件相同，4 份不同：`flow/verification/build/{rtl_inputs_pkg,v3_top_8664,v3_topprobe}/rtl_params.vh`、`flow/verification/build/probe_sadc/tb_sadc_enc.sv`。`vcs_runner.py` 的 staging 代码按 basename 复制，旧 stage 不能当成当前生产源；不将 190 份列表混入版本化文件。**E14** 可打印所有工作树副本的路径与比较结果。


## ④ 孤儿模块、归档安全与死链

| v21 顶层直接实例 | 模块 |
| --- | --- |
| 192 `u_pd` | `dither_pre` |
| 204 `u_sched` | `sched18` |
| 219 `u_ctx` | `acq_conv_ctx` |
| 293 `u_evt` | `sar_evt_v21` |
| 320 `u_dem` | `dem_mask_v3` |
| 478 `u_adc2` | `adc2_capture` |
| 572 `u_wsum` | `wsum_v21` |
| 611 `u_recon` | `recon_v21` |

**E04**：`recon_v21:314 → recip_rnd_v21:59 → recip_q60` 是传递层级；仿真节点宏 `adc2_node_macro` 由 TB 实例化。`prepare_h/v/half/coin` 来自外部输入；v21 缺相位发生器的实例，不等于未实现 DEM mask 或事件 follower 的全部功能。

| 有 Formality 通过记录、但未接 v21 的 RTL | 历史模块汇总（通过/失败/未验证） | 至少一个其他顶层消费者 |
| --- | --- | --- |
| `dem_phase_rr` | 97/0/0 | `d3d4_ip.sv:278`（默认） |
| `dem_phase_gen` | 126/0/0 | `d3d4_ip.sv:264`（`D3D4_PHASE_XORSHIFT`） |
| `follower_rt` | 72/0/0 | `d3d4_ip.sv:338`、`digital_top_w.sv:311` |

**E04、E06**：三者均为「未接入 v21」，按包含旧顶层的「任何顶层」口径不算孤儿。其他四个通过记录对应 `sched18`、`sar_evt_v21`、`adc2_capture`、`acq_conv_ctx`，都直接连到 v21；`234+133+97+72+126+490+1037=2189`。七项是历史单模块报告；树中只有 `sched18` 的网表/SVF 保留作可核对的实体产物，三个候选的原始日志和签名未入库。带时钟门控的 `sched18` 20 点失败，以及旧整核 `u_wsum` 20 点失败，是两项记录。`485abbbd` 是网表指纹前缀；`check_formal.sh` 只传 Tcl，未对已有 RTL、网表、SVF 强制绑定同一提交。`formal/v21/run_fv.tcl` 是 VC Formal 属性证明流程，不是这组 Formality 报告。**E06**

| 归档区 | 实测引用（`tb/ tools/ src/ flow/ formal/`） | 判定 |
| --- | --- | --- |
| `rtl/_legacy/`：3 | 五目录中 **11 行精确归档路径**，都指三份 RTL；另以模块标识找到 `d3d4_ip` 2 处、`digital_top` 1 处、`digital_top_w` 7 处 TB 实例。有 filelist、命令、Tcl 及参考说明。两份忽略的本机综合 Tcl 也引用归档 TopW。 | **不可当作可移除归档。** `d3d4_ip` 有 `Makefile` 正式入口；TopW 有 `run_topw.sh`；早期顶层有 `files.f` 和旧 TB。**E07** |
| `tools/_legacy/`：2 | 排除自引用后，精确新路径、旧路径和 import 名均无命中。`closed_loop2.py` 搬后默认 `../src` 解析为 `tools/src`，同目录没 `forward_model.py`；合同旧文字仍写 `tools/fwd_model.py`。 | **无活动执行依赖**；可保留源码，不宜称独立可复跑。**E07、E15** |
| `docs/_history/`：130（其中 105 Markdown） | 无单文件以新路径作为五目录的直接编译/模型输入；计数工具遍历此目录；C3 递归读取时误排除了 `history` 而非 `_history`。全 basename 搜索有 13 个候选（含通用 `README.md` 碰撞），多为旧名注释、说明和冻结文本；行为指纹确有两个可执行旧路径消费者。 | **半归档**。活动门禁读不到被迁走的基线；不能把该目录视为与门禁隔离。**E07、E08** |

| 死链或不完整入口 | 影响及证据 |
| --- | --- |
| **D3/D4** | 远端 P01；默认本地 VCS 分支又把编译放 CWD、运行放默认的 `$WORK/tb`。综合手册中的手工上传也平铺顶层，且少传 `dem_phase_rr`、`sched18`、`follower_rt`。`dc_hold_unc0.tcl` 的独立源清单也没列默认所需这三份；它的 `synth/d3d4` 是旧远端布局，不能直接当 checkout 相对路径。**E07** |
| **v21 源、旧 gate wrapper** | P02；`check_gate_equiv.sh` 走当前生成器，但未按当前 `check_gate_e2e.sh` 暂存共享 `tb_e2e_{decl,body}.svh`/仿真节点宏。是否有可用的外部网表或旧暂存目录本轮未验证。**E05、E06、E11** |
| **验证工具/CI** | P03–P04；只读工程自检实测 **4/7**，C3、C4、C7 失败，C3 有将 `0..17` 当链接的误报，其输出不能直接作真实死链总数。`flow/sim/run_vcs.sh` 计算出缺失的 `flow/tools/run_rtl_sim.py`；历史 cosim staging 把 `rtl/core、rtl/top` 拼成 `flow/rtl/…`。测试导入 `verification` 的可见性随环境而变，根和 `src` 都不能单独解析此包。**E08、E11** |
| **六份入口** | 19 个显式本地 Markdown 链接都存在。根 README 中用代码格式写出的 `docs/v21/_history/`、`docs/v21/STATUS.md`、`DRIVING_CELL_BUG.md`、`GATE_EQUIV.md`、`TIMING_BUDGET_FINAL.md`、`PPA_DIV_VS_RECIP.md`、checkpoint 和 `reports/eda_environment/vm-yian-tools.md` 不在所指位置；实际归档名见 E11。README 的作废清单在 STATUS §六；STATUS §四.5/§五.2 没有匹配条目。INDEX 的 `_history/root_docs/` 不存在。**E02、E11** |
| **相邻指南/合同** | `docs/guide/QUICKSTART.md` 指向不存在的 `../design/ARCHITECTURE.md`；`tools/results/README.md` 两条 `docs/archive` 链接仍用旧名；`docs/randomization_caaz/README.md` 还把失效的旧状态页称为全局权威。v2.1 声称的附件 `verify_contract_v21.py` 和 `verification_v21_results.json` 没有入库；v2 仍写搬走的 `tools/fwd_model.py`。`MANIFEST.in` 根级 `README_EN.md` 路径也已失效，迁入 `docs/meta/` 后该英文页首行仍用仓库根作相对基准。不能据此断言 hatchling 本身失败。**E10、E11、E15、E16** |

## ⑤ 核查过且没问题的项

| 检查项 | 结果 |
| --- | --- |
| Git 版本、未修改现有文件 | 分支和祖先关系满足要求；起始工作树干净、无子模块 gitlink，交付差异只允许一份未跟踪报告。**E01** |
| 部分索引数量和链接 | 5 份仓库根级 Markdown、14 份 docs 根级、非 `_history` 127 份、历史 105 份、`docs/v21` 9 份、12 张新目录 PNG；六份入口的 19 个显式本地链接均可解析。**E02、E11** |
| v21 拓扑的正确主张 | 627 行 / 8 直接实例；单模块通过记录不会使整核等价或签核自动成立。`verify_v21.sh` 内列了倒数依赖，且把工具形式证明未完成单独列出。**E03、E04、E05、E06** |
| 合同入口 | 两份生效规范的文件都在，入口与 INDEX 对 v2.1 优先级一致，13 bit 工程配置可从 RTL 接口/模型常量定位。**E10** |
| 跟踪产物与证据例外 | HEAD 未跟踪 `.log/.vcd/.vpd/.fsdb/.pyc`、`__pycache__` 等常规临时产物。两份 `.rpt` 是单测读取的 Vivado fixture，`sched18_final.v/.svf` 有证据说明，8 个 `.out` 是保留输出（7 个 dump + bring-up 输出），不按散落二进制判。**E06、E12** |

尺寸明细：按 **1 MB = 1,000,000 bytes**，也恰好都超过 1 MiB。**E12** 可复算；提交钩子的 512 KB 门限是新增文件配置，不代表现存每个文件已被它检查过。

| >1 MB 的跟踪文件 | bytes | 保留依据/必要性 |
| --- | ---: | --- |
| `references/PKU_VLSI2025_C32_1_SLIDES.pdf` | 5,288,256 | 参考原件白名单/清单；不参与 EDA 编译，保留政策由仓主决定 |
| `references/CICC2026_13_6_SLIDES.pdf` | 3,110,942 | 同上 |
| `references/CICC2026_13_6_PAPER.pdf` | 2,351,182 | 同上 |
| `references/ISSCC2024_SLIDES.pdf` | 1,950,386 | 同上，且审计文档引用讲稿页码 |
| `tools/results/report.html` | 1,650,940 | 发布参考输出白名单；确认保留或迁到发版资产 |
| `references/PKU_VLSI2025_C32_1_PAPER.pdf` | 1,480,856 | 参考原件白名单/清单 |
| `docs/_history/report/reproduction_report.pdf` | 1,413,057 | 历史复现报告；仍被跟踪但命中 `*.pdf`，建议显式处理 |

[需仓主决策] 参考原件、历史 PDF、参考报告的保留/尺寸策略。`*.log` 在根、`tb`、`formal` 有漏项；`tools/results/**` 的白名单例外连波形/缓存也可放行，`!flow/build/` 让 filelists 外的普通文件可进库。当前 438 忽略文件来自 424 暂存、11 综合结果、3 验证报告图；其中无 >1 MB 文件。HEAD 可达 Git 历史另有 26 个 >1 MB 的不同 blob（按对象计，不是当前 7 个路径）。**E12**

实测跟踪文本含个人主目录、远端 PDK/工作目录、许可环境配置和 Windows 用户目录；Git 历史作者/提交者字段还有一个内网域名地址（1,102 次出现）。报告只定位位置，不复述私有路径或值。历史有显式 revert/WIP；仅凭相同摘要不能判为重复提交或当前失效。**E13**

## ⑥ 不确定项

- [需仓主决策] **旧链是否仍是受支持交付**：旧 RTL 的消费者和静态断链都能定位，真正停用哪些入口取决于旧 IP、TopW、SMIC180、开放仿真/DSP 的支持范围。**E04、E07、E11**
- [需仓主决策] **现行回归基线和样本如何恢复**：历史同名指纹、C4 同名存证、旧资产清单都不能自动顶替当前门禁；需要指定对应提交/数据清单。**E08**
- [需仓主决策] **ADC2 的系统位宽与 v21 缺失模块接入**：只核实当前合同、配置、源码和历史模块记录；没有推断系统整定、量产位宽或接入方案。**E04、E06、E10**
- **环境与证据**：未查询远端现存文件、工具许可、外部 staging 或日志；没有本轮运行可证明真实通过数、同版综合、布线、Formality 或 signoff。历史三个候选的输入/输出哈希缺证；“从未”无法由当前树证明。**E05、E06、E09**
- **隐私扫描范围**：跟踪文本和本地提交元数据；不据此宣称 PDF/二进制内部或已发布远端不含信息。不知道域名/路径已对外暴露的范围；是否重写 Git 历史 [需仓主决策]。**E13**
- **多版本语义范围**：同 basename、同模块标识全量列举；异名按明确的替代、入口和引用识别。无引用的任意异名同义实现、外部调用或运行时生成内容不能仅凭静态树作穷尽证明。**E07、E14–E16**


## 复核命令

在审查 checkout 的仓库根执行。以下只读；`git grep` / `test` 用于证明缺失时，无输出或返回 1 是预期现象。真正跑验收的命令只作为被审查的文档内容，不属于复核命令。

### E01 · 分支、版本、文档基线

~~~bash
export GIT_OPTIONAL_LOCKS=0
git branch --show-current
git rev-parse HEAD
git merge-base --is-ancestor ebc9a8c 383112d && git rev-list --count ebc9a8c..383112d
git diff --name-status ebc9a8c..383112d
git status --porcelain=v1 --untracked-files=all
git ls-files -s | awk '{n[$1]++} END{for (k in n) print k,n[k]}'
for audit_rev in a9193e4 ffc10ef 6df4ad2 dfb21eb; do
  git show -s --format='%h %cs' "$audit_rev"
  git merge-base --is-ancestor "$audit_rev" 383112d && git rev-list --count "$audit_rev"..383112d
  git diff --name-only "$audit_rev" 383112d -- rtl/v21 tools/verify_v21.sh flow/synth/v21
done
for audit_doc in README.md docs/STATUS.md docs/INDEX.md docs/audit/README.md rtl/README.md docs/CONTRACT_CURRENT.md; do
  printf '\n%s\n' "$audit_doc"
  git log -1 --format='%h %cs' 383112d -- "$audit_doc"
  sed -n '1,7p' "$audit_doc"
done
~~~

### E02 · 独立计数与归档搬移

~~~bash
python3 -B - <<'PY'
from collections import Counter
import subprocess
p = [s.decode() for s in subprocess.check_output(
    ["git", "ls-tree", "-rz", "--name-only", "383112d"]).split(b"\0") if s]
d = [s for s in p if s.startswith("docs/")]
m = [s for s in d if s.endswith(".md")]
h = [s for s in m if s.startswith("docs/_history/")]
print("all", len(p), Counter(s.split("/")[0] if "/" in s else "(root)" for s in p))
print("root md", sum(s.endswith(".md") and "/" not in s for s in p))
print("docs root md", sum(s.count("/") == 1 for s in m))
print("docs subtree (all / md)")
for x in sorted({s.split("/")[1] for s in d if s.count("/") > 1}):
    prefix = "docs/" + x + "/"
    print(x, sum(s.startswith(prefix) for s in d), sum(s.startswith(prefix) for s in m))
print("non-history md", len(m)-len(h))
print("without either archive", sum(not s.startswith(("docs/_history/","docs/audit/_superseded/")) for s in m))
for x in ("docs/audit/", "docs/_history/"):
    print(x, Counter(s[len(x):].split("/")[0] if "/" in s[len(x):] else "(root)"
                     for s in m if s.startswith(x)))
print("20261117 png", sum(s.startswith("docs/figures/20261117/") and s.endswith(".png") for s in p))
PY
git grep -nE '127 份|11 现行|105 份|根级|root_docs|现行文档|更早的 12 份' -- docs/INDEX.md docs/audit/README.md
git diff --name-status dfb21eb^ dfb21eb
sed -n '25,54p' tools/check_doc_counts.py
git ls-tree -r --name-only 383112d -- docs/_history/root_docs
~~~

同一基线下，INDEX 其余目录的 Markdown 数如表可对照：`adr=22, mechanisms=13, guide=4, meta=3, rtl=17`（含所称 7 份支撑）、`verification/spec/caaz/acceptance` 各 2、`review/decisions/figures/randomization_caaz` 各 1。史区分别为根 38、`archive` 34、`v21_checkpoints` 9、`v21_legacy` 11、`rtl_reviews` 11、`v21_status` 1、`restructure` 1。仓内的 `tools/check_doc_counts.py` 只比较三个总数，不查子项，而且用工作树 `find`，新增本报告后不能用它还原提交时的 127。

### E03 · 验收用例与端口口径

~~~bash
git grep -nE '8 项|9 项|79 端口|87 个端口|约 1,426|cfg_lifecycle|VC Formal' -- \
  README.md docs/STATUS.md docs/audit/PORT_AND_ONCHIP_AUDIT.md tools/verify_v21.sh
sed -n '/^TESTS=(/,/^)/p' tools/verify_v21.sh
sed -n '/^TESTS=(/,/^)/p' tools/verify_v21.sh | grep -c '^[[:space:]]*"tb/'
sed -n '/^module adc_digital_ip_v21 (/,/^);/p' rtl/v21/adc_digital_ip_v21.sv |
  awk '/^[[:space:]]*(input|output)[[:space:]]/ {
    n[$1]++; w=1;
    if (match($0,/\[[0-9]+:[0-9]+\]/)) {
      split(substr($0,RSTART+1,RLENGTH-2),a,":"); w=a[1]-a[2]+1;
    }
    b[$1]+=w;
  } END{print "in",n["input"],b["input"],"out",n["output"],b["output"],
              "total",n["input"]+n["output"],b["input"]+b["output"]}'
~~~

### E04 · RTL 实例与可达性

~~~bash
wc -l rtl/v21/adc_digital_ip_v21.sv
git ls-files rtl/v21
git grep -nE '^[[:blank:]]*[[:alpha:]_][[:alnum:]_]*[[:blank:]]+u[[:alnum:]_]*[[:blank:]]*(#|\()' -- \
  rtl/v21/*.sv rtl/_legacy/tops/*.sv
git grep -nE 'D3D4_PHASE_XORSHIFT|prepare_(h|v|half|coin)|\.h_phase_i|\.v_phase_i' -- \
  rtl/_legacy/tops/d3d4_ip.sv rtl/v21/adc_digital_ip_v21.sv
git grep -nE '^[[:blank:]]*(dem_phase_rr|dem_phase_gen|follower_rt|adc2_node_macro)[[:blank:]]+' -- \
  'rtl/*.sv' 'tb/*.sv' 'tb/*.svh' 'src/*.sv' 'subsys/*.sv' 'flow/*.sv' 'formal/*.sv'
git grep -nE 'run_open_rtl|source_control_contract_tb|sar20_(digital_core|system_top|ad4080_top)' -- \
  .github/workflows/ci.yml tools/run_open_rtl.py flow/sim/tb/source_control_contract_tb.sv \
  rtl/rtl_sources.f subsys/ad4080_dsp/rtl/sar20_system_top.sv
~~~

### E05 · v21 并行入口的源码清单

~~~bash
git grep -nE 'recip_(rnd_v21|q60)|recon_v21|dem_phase_rr|follower_rt|^-f|set (RTL|DESIGN)' -- \
  flow/build/filelists/files_adc_v21.f flow/build/filelists/files_adc_v21_sim.f \
  flow/synth/v21/{fc_rtl_to_gds,dc_synth_v21,fm_equiv}.tcl tools/verify_v21.sh
~~~

### E06 · Formality 来源、范围、历史网表

~~~bash
git grep -nE 'SUCCEEDED|2,189|clock_gate_acq_q_reg|2589|2,589|u_wsum|485abbbd' -- \
  docs/audit/{ALL_13_MODULES_FINAL,FORMAL_SUCCEEDED}.md docs/v21/FORMAL_STATUS.md \
  docs/_history/v21_legacy/GATE_EQUIV_superseded.md
git show d8b5f37:docs/audit/ALL_13_MODULES_FINAL.md | grep -nE 'fm_dem_phase_rr|fm_follower_rt|fm_dpg'
git diff --name-status d8b5f37..383112d -- rtl/{dem_phase_rr,dem_phase_gen,follower_rt}.sv
git diff --name-status 054cfed..383112d -- rtl/v21
git ls-tree -r --name-only 383112d | grep -E '\.(v|svf|def|gds|rpt|log)$'
git ls-files docs/audit/artifacts
git grep -nE 'RTLDIR|set_svf|read_verilog -container|ls -la|scp|fm_shell|sha256sum' -- \
  flow/synth/v21/fm_equiv.tcl tools/check_formal.sh tools/check_gate_e2e.sh
git grep -nE 'FPV|未成功|TIMEOUT|Verification SUCCEEDED' -- formal/v21 docs/v21/FORMAL_RND96.md
~~~

### E07 · 全归档反向引用及旧部署入口

~~~bash
for audit_dir in rtl/_legacy tools/_legacy docs/_history; do
  printf '%s: ' "$audit_dir"; git ls-files -- "$audit_dir" | wc -l
done
git ls-files -- rtl/_legacy tools/_legacy docs/_history |
  git grep -nI -F -f - -- tb tools src flow formal ':(exclude)tools/_legacy/**'
git grep -nI -E 'rtl/_legacy/|tools/_legacy/|docs/_history/|(^|[^[:alnum:]_])(closed_loop2|fwd_model)([^[:alnum:]_]|$)' -- \
  tb tools src flow formal ':(exclude)tools/_legacy/**'
python3 -B - <<'PY'
from pathlib import PurePosixPath
import subprocess
for d in ("rtl/_legacy", "tools/_legacy", "docs/_history"):
    files = subprocess.check_output(["git","ls-files","-z","--",d]).split(b"\0")
    for f in filter(None, files):
        name = PurePosixPath(f.decode()).name
        r = subprocess.run(["git","grep","-nI","-F","-e",name,"--","tb","tools","src","flow",
                            "formal",":(exclude)tools/_legacy/**"], capture_output=True, text=True)
        if r.returncode not in (0, 1):
            raise SystemExit(r.stderr)
        if r.stdout:
            print(f.decode(), len(r.stdout.splitlines()))
            for line in r.stdout.splitlines():
                # 避免整行发布本机路径，仅保留位置
                print(" ", ":".join(line.split(":", 2)[:2]))
PY
git grep -nE '^[[:blank:]]*(d3d4_ip|digital_top_w|digital_top)[[:blank:]]+(#|[[:alnum:]_]+[[:blank:]]*\()' -- tb
git grep -nE 'verify-d3d4-ip|files_topw\.f|digital_top(_w)?\.sv' -- \
  Makefile flow/build/filelists tools/run_topw.sh tools/topw_mutation.sh \
  flow/synth/synth.tcl flow/synth/fm/synth_with_netlist.tcl
git grep -nE 'SRCS=|IPF=|EAF=|WORK=|mkdir -p .*WORK/rtl|scp -q \$?SRCS|_legacy/tops|RUNCD|cd \$WORK/tb' -- tools/verify_d3d4_ip.sh
git grep -nE 'mkdir|scp|set sdc|_legacy/tops|sched18\.sv|follower_rt\.sv|dem_phase_rr\.sv' -- \
  docs/SYNTH_D3D4.md flow/synth/d3d4/dc_d3d4_ip.tcl flow/synth/d3d4/dc_hold_unc0.tcl
git ls-files -oi --exclude-standard -- flow/synth/results |
  while IFS= read -r audit_f; do
    grep -qF 'rtl/_legacy/tops/digital_top_w.sv' "$audit_f" && printf '%s\n' "$audit_f"
  done
~~~

### E08 · 测试、干净克隆、指纹与 CI 的缺失输入

~~~bash
git grep -nE 'tests/(unit|verification|audit|integration)|behavior_fingerprint\.json|20261113_p3p4|"history" in path.parts' -- \
  tools/{verify_clean_clone.sh,check_engineering.py,behavior_fingerprint.py}
git ls-tree -r --name-only 383112d -- tests docs/readme docs/restructure docs/_history/restructure docs/reviews
git ls-tree -r --name-only 383112d -- flow/tests | wc -l
git grep -nE 'pytest flow/tests|source_bound_assets_manifest|docs/readme/data|calibration_40m|interleave_40m|README should|31\.028' -- \
  .github/workflows/ci.yml flow/tests/unit/{test_readme_effect_evidence,test_default_mismatch_anchor}.py
git grep -nE '32\.0|31\.028|设计目标|(^|[^A-Za-z0-9_])g0([^A-Za-z0-9_]|$)' -- README.md
git grep -nE 'rtl_sources\.f|rglob\("\*\.sv"\)|sorted\(rels\)|set\(declared\)' -- \
  flow/tests/unit/{test_review_trace,test_mutate_rtl}.py
python3 -B - <<'PY'
from pathlib import Path
actual = {p.as_posix() for p in Path("rtl").rglob("*.sv") if p.is_file()}
listed = {l.strip() for l in Path("rtl/rtl_sources.f").read_text().splitlines()
          if l.strip().endswith(".sv")}
print("actual, listed, missing, extra:", len(actual), len(listed), len(actual-listed), len(listed-actual))
print("\n".join(sorted(actual-listed)))
PY
env GIT_OPTIONAL_LOCKS=0 python3 -B tools/check_engineering.py
~~~


### E09 · 文档冲突、后端证据与计数

~~~bash
git grep -nE '从未综合|未综合过|同版网表|dcwork4.*成功|1fb9408|未在顶层实例化' -- \
  README.md docs/{STATUS,INDEX}.md docs/v21/{SYNTH_STALL,FORMAL_DIAGNOSIS}.md docs/audit/README.md
git grep -nE '160,951|160951|95\.8|物理不可行|13(/13| 个模块)|alpha.*(旁路|bypass)' -- \
  docs/{STATUS,INDEX}.md docs/audit/{README,SYNTH_PER_MODULE,ALL_13_MODULES_FINAL}.md \
  docs/audit/artifacts/DC_MODULE_SUMMARY.txt
git grep -nE '(12|13) 项|6 项.*全部未开始' -- docs/INDEX.md docs/audit/README.md
sed -n '/^## 1\. 结论汇总/,/^## 2\./p' docs/audit/AUDIT_ALGO_VS_SOURCE.md |
  grep -E '^\| [0-9]+ \|'
sed -n '14,24p;138,144p' docs/audit/todo/README.md
~~~

### E10 · 合同、工程配置与行为模型

~~~bash
sed -n '1,45p' docs/CONTRACT_CURRENT.md
sed -n '1,8p' docs/ISSCC2024_ONCHIP_CONTRACT_v2_1_ADDENDUM.md
git grep -nE 'ADC2(_N)?|ADC2_YMIN|12582912|adc2_(raw_i|n_bits|v_min|v_max)|coin==1|pre\[slot|verify-d3d4|verify_contract_v21|verification_v21_results' -- \
  docs/ISSCC2024_ONCHIP_CONTRACT_v2*.md docs/ACCEPTANCE_STATUS.md \
  rtl/v21/{adc_digital_ip_v21,adc2_node_macro,wsum_v21}.sv \
  src/adi_model/defaults.py Makefile
git grep -nE 'tb_dem_dither|tb_d3d4_ip|L3' -- tools/verify_d3d4.sh tools/verify_d3d4_ip.sh
git grep -nE '0\.60|0\.15|待裁定' -- \
  docs/audit/{AUDIT_ALGO_VS_SOURCE,PORT_AND_ONCHIP_AUDIT,ALL_13_MODULES_FINAL}.md docs/audit/todo/README.md
python3 -B -c 'for_checks=[(13,.75),(12,.45),(14,.45)]; print([(n,w,w/(2**n*32)/(6/2**20)) for n,w in for_checks])'
git ls-tree -r --name-only 383112d | grep -E '(^|/)(verify_contract_v21\.py|verification_v21_results\.json)$'
~~~

计算的是名义 ADC2 **步长**输入折算：窗口 / `2^N` / `G=32`，以 ±3 V 口径的 `6/2^20` 为单位。13 bit/0.75 V 为 0.50，12 bit/0.45 V 为 0.60，14 bit/0.45 V 为 0.15；不从步长推定系统噪声或精度是否达标。

### E11 · 文档及脚本旧路径

~~~bash
python3 -B - <<'PY'
from pathlib import Path
from urllib.parse import unquote, urlsplit
import re
docs = ("README.md","docs/STATUS.md","docs/INDEX.md","docs/audit/README.md",
        "rtl/README.md","docs/CONTRACT_CURRENT.md")
for name in docs:
    p = Path(name)
    n = 0
    for no, line in enumerate(p.read_text().splitlines(), 1):
        for m in re.finditer(r"\]\(([^()\s]+)\)", line):
            link = m.group(1)
            u = urlsplit(link)
            if u.scheme or not u.path or ("/" not in u.path and not re.search(r"\.[A-Za-z][A-Za-z0-9]*$", u.path)):
                continue
            n += 1
            print(f"{name}:{no}", "OK" if (p.parent/unquote(u.path)).exists() else "MISSING", link)
    print(name, "local inline", n)
PY
git grep -nE '^ *\[[^]]+\]:|\]\([^)]*#' -- README.md docs/{STATUS,INDEX,CONTRACT_CURRENT}.md docs/audit/README.md rtl/README.md
sed -n '21,28p;152,165p;209,213p;218,228p' README.md
git grep -nE '^##|§四\.5|§五\.2' -- docs/STATUS.md
for audit_target in docs/v21/_history docs/v21/STATUS.md docs/v21/DRIVING_CELL_BUG.md \
  docs/v21/GATE_EQUIV.md docs/v21/TIMING_BUDGET_FINAL.md docs/v21/PPA_DIV_VS_RECIP.md \
  docs/v21/CHECKPOINT_AUDIT3.md reports/eda_environment/vm-yian-tools.md; do
  test -e "$audit_target" || printf 'MISSING %s\n' "$audit_target"
done
git ls-tree -r --name-only 383112d -- docs/v21 docs/_history/{v21_legacy,v21_status,v21_checkpoints}
git grep -nE 'ARCHITECTURE\.md|archive/STATUS\.md|docs/archive/|README_EN\.md|RTL_SYNTHESIS_REVIEW_20260920' -- \
  docs/{guide/QUICKSTART,randomization_caaz/README}.md tools/results/README.md flow/synth/README.md MANIFEST.in
git ls-files -- '*README_EN.md' '*ARCHITECTURE.md' '*REPRODUCING.md' '*digital_status_matrix.md'
git grep -nE 'tb_e2e_.*svh|adc2_node_macro|for f in|python3 tools/gen_gate_tb.py|SCRIPT_DIR|run_rtl_sim\.py|ROOT =|ROOT\.glob|copy2\(ROOT|rtl/top/sadc_enc' -- \
  tools/{check_gate_equiv.sh,check_gate_e2e.sh,gen_gate_tb.py} \
  flow/sim/run_vcs.sh flow/verification/{run_verification.py,cosim/vcs_runner.py}
test -f tools/run_rtl_sim.py && test ! -f flow/tools/run_rtl_sim.py && \
  test -f rtl/top/sadc_enc.sv && test ! -f flow/rtl/top/sadc_enc.sv
python3 -I -B - <<'PY'
from importlib.machinery import PathFinder
from pathlib import Path
r = Path.cwd()
for name in (".","src","flow"):
    print(name, bool(PathFinder.find_spec("verification", [str(r/name)])))
PY
~~~

### E12 · 忽略、跟踪产物与尺寸

~~~bash
git ls-files -- '*.[lL][oO][gG]' '*.[vV][cC][dD]' '*.[vV][pP][dD]' '*.[fF][sS][dD][bB]' \
  '*.[pP][yY][cCoO]' '*.vvp' ':(glob)**/__pycache__/**' ':(glob)**/.pytest_cache/**' ':(glob)**/simv.daidir/**'
git ls-files -ci --exclude-standard
git ls-files -- '*.rpt' '*.out' '*.svf'
git grep -nE 'check_timing\.rpt|timing_summary\.rpt|reports/dumps|top_bringup\.out' -- \
  flow/tests/unit/test_vivado_buffered_impl.py flow/verification flow/tests/verification
git ls-files -oi --exclude-standard |
  awk -F/ '{a[$1"/"$2"/"$3]++} END {for(k in a) print a[k],k; print NR,"total"}'
git ls-files -oi --exclude-standard -z | python3 -B -c 'import os,sys; a=[os.lstat(p).st_size for p in sys.stdin.buffer.read().split(b"\0") if p]; print(max(a,default=0),sum(n>1000000 for n in a))'
printf '%s\n' root.log tb/example.log flow/sim/example.log flow/synth/example.log formal/example.log \
  flow/build/generated.csv flow/verification/build/stage.csv tools/results/example.log \
  tools/results/example.vcd tools/results/__pycache__/example.pyc test.vcd \
  py/__pycache__/cache.pyc simv.daidir/test formal/formality_svf.svf \
  | git check-ignore -v -n --no-index --stdin
git check-ignore -v --no-index docs/_history/report/reproduction_report.pdf
git ls-tree -rl 383112d | awk '$2=="blob" && $4>1000000' | sort -nk4,4r
git rev-list --objects 383112d | git cat-file --batch-check='%(objecttype) %(objectsize) %(objectname) %(rest)' |
  awk '$1=="blob" && $2>1000000 {n++} END {print n+0, "reachable blobs >1 MB"}'
git grep -nE 'maxkb|check-added-large-files|tools/results|references/.*\.pdf' -- \
  .pre-commit-config.yaml .gitignore references/source_manifest.json
~~~

`check-ignore -v -n` 中空来源（`::`）表示未命中，`!…` 是放行规则。以上仅 probe 路径，不创建文件。

### E13 · 主机绑定与 Git 元数据（输出经过缩减）

~~~bash
git grep -nI -o -E '(/(Users|home|root)/|[A-Za-z]:/Users/)' 383112d -- \
  docs/acceptance docs/review docs/audit docs/_history/v21_checkpoints \
  flow/synth/v21 flow/tests/fixtures
git grep -nI -o -E '(SNPSLMD_LICENSE_FILE|LM_LICENSE_FILE|/opt/synopsys/|^set PDK|VERIF_HOST)' 383112d -- \
  tools/verify_d3d4_ip.sh tools/verify_v21.sh docs/v21/I5_ENVIRONMENT.md \
  flow/synth/v21 flow/scripts/clean_remote.sh flow/verification/cosim/vcs_runner.py
git log 383112d --format='%ae%n%ce' |
  awk '/[.]local$/ {n++; a[$0]=1} END {print "entries",n,"distinct",length(a)}'
git log 383112d --format='%h %s' --grep='^[Rr]evert' --grep='^[Ww][Ii][Pp]'
git log 383112d --format='%s' | LC_ALL=C sort | uniq -cd
git grep -nE '^version[[:space:]]*[:=]' -- CITATION.cff pyproject.toml
~~~

`CITATION.cff` 标注 8.2.2、`pyproject.toml` 分发版本 9.0.0；两者用途可不同，若计划共同发布，[需仓主决策] 指定引用版本是否同步。相同 Git 摘要筛查未发现重复摘要，不能视为同补丁去重检查。（**E13**）

### E14 · 精确同名、模块重复与本地暂存副本

~~~bash
python3 -B - <<'PY'
from collections import defaultdict
from pathlib import Path
import os, re, subprocess
def git_lines(*args):
    return [Path(os.fsdecode(p)) for p in subprocess.check_output(
        ["git",*args]).split(b"\0") if p]
t = git_lines("ls-tree","-rz","--name-only","383112d")
extensions = {".v",".sv",".svh",".vh",".vhd",".vhdl",".py",".md",".rst",".txt",".tex",".html"}
for fold in (False, True):
    by = defaultdict(list)
    for p in t:
        if p.suffix.lower() in extensions:
            by[p.name.casefold() if fold else p.name].append(p)
    print("casefold",fold)
    for name, files in sorted(by.items()):
        if len(files)>1:
            print(name,len(files),*[str(p) for p in files],sep="\n  ")
mods = defaultdict(list)
for p in t:
    if p.suffix in {".v",".sv"}:
        for n,line in enumerate(p.read_text(errors="replace").splitlines(),1):
            m=re.match(r"\s*module\s+([A-Za-z_]\w*)\b",line)
            if m:
                mods[m[1]].append(f"{p}:{n}")
for name, files in sorted(mods.items()):
    if len(files)>1:
        print("duplicate module",name,*files)
by = defaultdict(list)
for p in t:
    if p.suffix in {".v",".sv",".svh",".vh",".vhd",".vhdl"}:
        by[p.name].append(p)
copies=[p for p in git_lines("ls-files","-oiz","--exclude-standard") if p.name in by]
print("stage copies:",len(copies),"basenames:",len({p.name for p in copies}))
same=0
for p in sorted(copies):
    identical=any(p.read_bytes()==q.read_bytes() for q in by[p.name])
    same+=identical
    print("identical" if identical else "different",p)
print("identical:",same,"different:",len(copies)-same)
PY
git grep -nE 'copy2|STAGE_MANIFEST|stage_dir|ROOT\.glob' -- flow/verification/cosim/vcs_runner.py flow/verification/run_verification.py
~~~

### E15 · 版本组的执行引用

~~~bash
git grep -nE 'fig19_ideal_ports|source_control_component|sha256|sv_path' -- \
  .github/workflows/ci.yml flow/sim/fixtures/{source_control_component,fig19_ideal_ports}/binding.json \
  tools/run_fig19_rtl_calibration.py
git grep -nE 'adi_model\.(control|simulation)\.scheduler|adi_model\.contracts\.fixed_point|verification\.reference\.fixed_point|make_figures|tests\.conftest|main_simulate' -- \
  pyproject.toml src tools docs/caaz flow subsys/extensions/caaz
git grep -nE '^[[:space:]]*(recon_d5[_a-z0-9]*|recon_core|recon_v21|wsum_v21|sar20_digital_core|sadc_enc|follower(_rt)?|sar_evt_v21|dem_shuffle|dither_split|dither_pre|sched18|slice_pool(_ctrl)?|adc2_(dec|capture))[[:space:]]+(#|[[:alnum:]_]+[[:space:]]*\()' -- \
  'rtl/*.sv' 'tb/*.sv' 'flow/*.sv' 'subsys/*.sv'
git grep -nE '(^[[:space:]]*(from|import).*forward_model|closed_loop2|fwd_model|唯一入口|WIP|Scheduler18|sched18_rtl)' -- \
  tools/{closed_loop,e2e,forward_model,sched18_stats,gen_vec_r18,dem_abcd,dem_spectrum,dither_physical,regression4}.py \
  tools/_legacy src/adi_model/{scheduler18,sched18_rtl}.py docs/ISSCC2024_ONCHIP_CONTRACT_v2.md
git grep -n -w -e e2e -e closed_loop -e closed_loop2 -e fwd_model -e forward_model -e scheduler18 -e sched18_rtl -- \
  src tools .github Makefile pyproject.toml docs/ISSCC2024_ONCHIP_CONTRACT_v2.md
git grep -nE 'run_review2_figure|run_review_figures|_default_pool' -- \
  tools/verify_clean_clone.sh tools/run_review2_figure.py docs/figures/20261117 flow/tests/unit/test_dem3d.py
git grep -nF 'sched18_final.v' -- docs/audit tb tools src flow formal
~~~

### E16 · 文档的正式入口、异名替代和历史版本

~~~bash
git grep -nE 'CONTRACT_CURRENT|STATUS\.md|D3D4_IP_INTERFACE|ARCHITECTURE\.md|STATUS_d5a1576' -- \
  README.md docs/{INDEX,STATUS,ACCEPTANCE_STATUS,CONTRACT_CURRENT}.md \
  docs/{guide/QUICKSTART,randomization_caaz/README,_history/archive/STATUS}.md
git grep -nE '取代|作废|CHECKPOINT_I5_SYNTH|review_response_2026-09-11|WSUM_(PHYSICAL_COST|PLACEMENT_OVERLAP)' -- \
  docs/audit/README.md docs/v21/FORMAL_RND96.md docs/_history/{v21_legacy,v21_checkpoints,archive} \
  tools/check_recip_correction.py
git ls-tree -r --name-only 383112d -- docs/_history docs/v21 docs/audit |
  grep -E '(STATUS|ACCEPTANCE|CHECKPOINT|superseded|review_response|release_v8\.2\.)'
git grep -nE 'ABI2|历史合同|含20个模块|现行接口与算术合同|^\[中文主文档' -- \
  docs/rtl/{CURRENT_INTERFACE_AND_ARITHMETIC,RTL_ARITHMETIC_CONTRACT,RTL_ENGINEERING_GUIDE}.md docs/meta/README_EN.md
head -n 12 docs/_history/archive/STATUS.md
~~~
