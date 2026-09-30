# RTL、来源映射及交付复核（2026-09-30）

> **最新物理证据（本日更新）：** PR #4 的 `b9fcba4` 已通过 [9/9 项 CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36669604147)。当前缓存版 P5 在 Vivado 2018.3、Virtex-7、25 ns 约束下完成有效综合及真实 BUFG 布线；[原始布线证据](../evidence/20260930/vivado_cached_p5_route_25ns_fail/README.md)显示路由和 DRC Error 为 0，但 setup WNS 为 −0.280 ns，全路径 hold WHS 为 −2.361 ns，不能宣称 40 MHz 已实现。寄存器引脚到寄存器引脚的独立 STA 得到 hold +0.062 ns；外部配置输入仍缺少板级最短延迟条件。针对最差行缓存反馈路径的局部行更新候选虽通过22项本地RTL回归，[同约束有效综合](../evidence/20260930/local_row_trial_synth/comparison_vs_cached.json)的LUT却从106,205增至121,772（+14.66%），[真实路由](../evidence/20260930/local_row_trial_route_congestion/README.md)又因拥塞等级7失败。该版本已弃用；共享旧系数选择器的后备结构正在重新综合。下面按原日期记录的 `0c4a772`、早期CI及待验收状态是**历史快照**，不能覆盖本段最新证据。

状态：修复已发布到 PR #4 的 `0c4a7726597c49044d25117a701ba780137ddf04`。截至 2026-09-29 20:32 UTC（北京时间 2026-09-30 04:32），[CI 36626662809](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36626662809) 仍为 `in_progress`，不能写成全部通过。归约器、位级镜像和精简权重存储的定向复验已有结果；同约束 baseline/optimized 比较正在进行，buffered 实际布线与完整顶层 mapped functional 验证尚待完成。ASIC PPA 和模拟电路签核不在已完成范围内。

## 最新验收：共享读选修复（2026-09-30）

当前生产 RTL 的 26 文件内容 SHA-256 为 `89b9f8153fea49d82faba8464e45bb83c914b11ebf708fb623a9fe3a660b5d26`。本次仅修改 `weight_store.sv`：每个物理行的单位读选同时供配置读回和本行更替运算使用，避免行更新经过全局 slice 读回树；保持同沿写入、行和、合法性检查及 clear/validate 语义。

- [冻结 RTL](../evidence/20260930/shared_old_trial_rtl/README.md)：官方 Verilator 5.020 全部 22 项 testbench、3 类严格 lint 通过；18-slice P5/P6/P7 各 438 个输出、7,680 次协议检查，延迟仍为 15/13/11 拍。
- [同约束综合](../evidence/20260930/shared_old_trial_synth/comparison_vs_cached.json)：Vivado 2018.3、Virtex-7、P5、25 ns，108,842 LUT、66,492 FF、20 DSP、0 BRAM/latch。相对直接前代 106,205 LUT 增加 2.483%，这是时序修复的面积代价。源包 28 项仅权重存储一文件不同，XDC/Tcl 相同。
- [真实 BUFG 布线](../evidence/20260930/shared_old_trial_route_25ns/README.md)：寄存器间 setup WNS **+0.946 ns**、hold WHS **+0.052 ns**，路由/DRC 错误 0，16 类 check_timing 问题 0。前代同条件 setup 为 −0.280 ns。最差路径转为样本上下文到重构 rails，数据延迟 23.914 ns，其中布线 18.623 ns。
- [完整综合网表 XSim](../evidence/20260930/shared_old_trial_full_mapped/README.md)：3 模式 438 个输出、3,900 次配置读回、7,680 次协议检查、3 次在途取消，code/flags 无不匹配；输出 trace 与前代逐字节相同。原始网表、综合 DCP、源清单和运行日志的摘要相互绑定。无 SDF，非零 flags 与内部启动延迟由独立 RTL 测试覆盖，不归入此网表结果。

**验收边界：**25 ns 寄存器间时序通过支持当前记录条件下的 40 MHz 核心、16 拍/样本即 2.5 MS/s。零最短输入延迟的 OOC 外部端口 hold 仍为 −2.278 ns，因此整体 `TIMING_MET=0` 与核心 `REGREG_TIMING_MET=1` 同时保留；没有板级 IO 或 ASIC 640 MHz 签核。逐行独立读选候选曾增加 14.66% LUT 且拥塞路由失败，已弃用，不能与本版本混淆。

## 历史交付版本与证据身份

- 发布提交：[0c4a772](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/commit/0c4a7726597c49044d25117a701ba780137ddf04)。当前生产 RTL 的 26 文件内容集合 SHA-256 为 `fd885e882ad5447f87626dc034fb59a6853a86f01050957a93b8a6407ebbbc13`，算法与逐文件摘要见 [compact 身份清单](../evidence/20260930/compact_profiles/rtl_compact_identity.json)。内容摘要不是 Git 提交号。
- 历史 `39e9c27` 的本地 21 项 RTL/3 类 lint 成功记录保存在 [final_rtl](../evidence/20260930/final_rtl/result.json)，对应旧 RTL 集合 `169f957dc7fe…` 和当时的测试台。它不是 `0c4a772` 全量 CI 成功证据。
- 相对旧 RTL 集合，compact 生产源码只修改 `weight_store.sv`。归约 testbench 与 Python 镜像也另有修复，不能宣称旧 84 项来源文件与当前仓库完全相同。
- 原始失败日志与 manifest 保留；修订解释和后续运行另存，不能覆盖旧失败状态。目录入口及边界统一见 [证据索引](../evidence/20260930/README.md)。

## 已修复问题及直接证据

### 结构控制、归约连接与除法

1. `sar_structural_ctrl` 的 Flash/后级采集使能和截止 cancel 改为寄存器输出；cancel 多保持一拍，使释放时 busy 已清零。历史定向负控分别在 234 ns、242 ns 抓到原组合译码和旧取消信号问题。这是源级相位扰动隔离证据，不是 SDF/物理无毛刺保证。见 [协议负控](../evidence/20260930/rtl/structural_protocol.negative_controls.json)。
2. `cal_weight_reduce` 按公共 dither 物理列先求和再取正负，并共享掩码权重和，保持数据格式和流水节拍。保守源级加减表达式计数 249→75、条件取负 72→4，只是源码结构比较。前向 generate 层级引用后来被真实 Vivado 暴露为根节点无驱动；当前 reducer 与 `sadc_enc` 已改为显式 net 数组连接。两种修复公式的真实小型网表各完成 12,904 次 XSim 比较，见 [tree_mapping](../evidence/20260930/tree_mapping/README.md)。
3. `div_floor` 使用与分子等宽的余数、扩展一位的借位减法及宽分母分类，保持负数 floor、最小负数和展开 padding 语义。RTL 小几何穷举共 67,592 项，另有独立任意精度对照和负控，见 [divider](../evidence/20260930/divider/README.md)。

### 39e9c27 的 CI 失败：测试台调度与镜像漂移

`39e9c27` 的 RTL CI 在 Verilator 5.020 上已经进入运行，在归约实例 `NS=32,NM=2,ND=1,DE=3` 的第 13 次检查失败；这不同于更早 `50b5733` 的 300 s 编译超时。原七实例测试台在本机官方 5.020 上复现。printf-only 诊断还发现第 9 个用例两份实现的 T/G 都为 0，而原始刺激对应的独立值是 24，因此只比较新旧相等会接受双方共同错误。

修复只改变测试台：在明确的测试时钟沿捕获整包输入，另一沿用逐物理单元 scalar oracle 同时检查新旧结果，保持生产 RTL、原参考模块、种子、激励顺序和计数。Verilator **5.020 与 5.49 各完成 1,430,848 次检查、7 种几何、43,758 个生产分配组合，退出码均为 0**。将双方共享权重快照清零的负控在第 9 次检查被独立 oracle 抓到并以 134 退出。两个工具重复同一集合，不叠加为额外覆盖。源码、日志、工具来源和复现命令见 [reducer_scheduler](../evidence/20260930/reducer_scheduler/README.md)。

同次 Python CI 发现 `div_floor` 已采用 `W_R=WA`，而 [位级镜像](../../sim/ref/recon_rtl_mirror.py) 仍沿用旧余数宽度。`0c4a772` 同步余数宽度、宽分母高位分类和 `W_R+1` 位借位减法。[六组小几何测试](../../tests/unit/test_recon_mirror.py) 共穷举 **55,432 个输入对**，含 308 个零分母、31,744 个高分母分类用例，覆盖最负数与非整除展开 padding；25 项镜像测试通过。它是 Python 商值/错误语义验证，不是 RTL 时序测试，也不同于 RTL 的 67,592 项穷举。

本地 `PYTHONPATH=$PWD/src python -m pytest -m "not slow" -q` 另记录 918 passed、3 deselected。该轮收集后新增的完整映射流程测试单独验证；六文件流程测试共 230 项通过（29+51+32+9+67+42），属于静态/mock 流程检查，不能替代真实 EDA。外部证据位于 `outputs/sar_adc_vivado_20260930/python_regression/manifest.json` 与 `full_mapped_flow_checks/`，相对于交付工作区；这两组计数不相加为 RTL 覆盖量。

### 权重最高位不变量与 compact 三档复验

合法写入满足 `0<W<2^47`；复位写零，其他分支保持原字，因此每个权重的 bit47 在复位后恒为零。`weight_store.sv` 仅在已获准写入时显式与 `W_MAX-1` 相与，不改变 48-bit 端口、Q30 精度、合法值域或拒绝规则。

[weight_msb](../evidence/20260930/weight_msb/README.md) 保存旧/新实现同时对照独立逐字状态模型的实际结果：三种几何、3,023 步、1,517 次合法写、1,184 次拒绝写、2,411,234 次逐字访问均检查两份实现；另有三类严格 lint、91,142 项 leaf 检查、624 个配置输出和 709 项外设检查通过。旧 DCP 中的 1,278 个高位 FF 只是优化机会，不能直接宣称新网表已经少了同样数量的 FF/LUT。

[compact_profiles](../evidence/20260930/compact_profiles/README.md) 从只读 `fd885…` 冻结源码实跑生产 18×71 顶层。P5/P6/P7 各含 3 种模式、438 次正确输出、438 次精确延迟检查、7,680 个协议检查节拍，延迟分别 **15/13/11 拍**，编译和运行均退出 0。该轮未重跑全部旧 21 项，也未重跑旧版每档 32 笔的连续启动夹具；后者 busy 释放 **14/12/10 拍**仍属于 [final_profiles 历史记录](../evidence/20260930/final_profiles/recon_ppa_latency_tb.run.final.log)。

## 有效综合基线与待完成对比

Vivado 2018.3、`xc7vx690tffg1761-2`、P7、请求周期 1.5625 ns 下，修复连接后的有效 baseline 完整综合结果为 **171,246 物理 LUT、66,817 FF、20 DSP、0 BRAM，WNS −17.426 ns**，状态为 `SYNTH_COMPLETE_TIMING_NOT_MET`。这是旧四树公式、旧除法器和旧权重存储的独立基线，不是最新 compact 源码的综合结果，更不是 route 后频率。

- 已归档 [结构审计与利用率](../evidence/20260930/vivado_structure/README.md)、[DCP 身份](../evidence/20260930/vivado_structure/archive_manifest.json)。DCP SHA-256 为 `9c3bc09dd193d0bffcd6d00f9af83cc887fde5423402416af751f4a2d1ef506b`。
- WNS 原始来源在交付工作区 `outputs/sar_adc_vivado_20260930/repair_baseline/synth/artifacts/vivado/20260929T190955.260429Z_p7_recovered/out/status.txt`；该文件 SHA-256 为 `87fa6c2d414d009e16214f0e469d714974481f068802e7985517e16579b69ba3`，同目录上层 manifest 绑定工具、约束与每个源文件。
- EDIF 的 277,037 个 LUT primitive 不是物理 LUT 利用率，不能混用；`flatten_hierarchy rebuilt` 后的层级归属也不能直接代表原 RTL 模块的独立成本。
- 更早带 `Synth 8-3848` 无驱动及常量化的 baseline/candidate DCP 是无效诊断样本，不能用其小资源数算优化收益。Windows 包装器 raw exit code 0 也不能覆盖 status 的时序失败或完整性拒绝；原始 manifest 保留。

同约束 baseline/optimized 比较正在运行，尚无最终优化差值。buffered 实际 route、setup/hold、约束完整性以及完整生产顶层 mapped functional 结果仍待验收。流程及公共端口 TB 已准备，见 [buffered 实现入口](BUFFERED_OOC_IMPLEMENTATION.md) 和 [完整顶层映射入口](FULL_TOP_MAPPED_FUNCTIONAL.md)。本地 RTL TB qualification 与 mock 成功不能改写成 XSim/route 成功。

用户确认的 640 MHz 是 ASIC 目标；FPGA 按实际可实现频率验证并报告差距。FPGA OOC、vectorless 功耗及模拟宏边界均不能替代同一 ASIC 库、约束和 PVT 下的物理签核。

## 历史证据保留与架构差距

[arithmetic](../evidence/20260930/arithmetic/manifest.json) 保留早期独立任意精度 90,058 个向量与 8 组 flags 场景，两种错误变异均被抓到；[reduction](../evidence/20260930/reduction/manifest.json) 保留早期新旧 miter 及四项集成结果（物理校准 2,048、恢复 2,048、拟合 128/1,278 系数、理想后端全码 1,048,576）。它们的源文件摘要仅对当次实验成立，不声称匹配最新源码；尤其早期 miter 没有后来加入的 scalar 判据。

仍需闭合的架构与模拟事项：

- 论文量化器到 RDAC 两端口 dither 扩大 2 bit 尚未闭合。ADR0009 的 `dR=4*dQ` 是工程候选；当前合法配置互斥 sampling 与 quantizer 两路径，需要同时设计残差公式、满量程和注入扣除。
- US10707889B1 跨 ADC 最新转换码 tracking 未形成完整数字流程；这是专利覆盖差距，尚无证据说明每个专利实施例都是该 ISSCC 芯片必须采用的路径。
- 参考、AUX、RA/AZ、匹配采样时间常数及实际比较器时序仍需 AMS、晶体管和 PVT 验证。
- 不通过截掉合法数值域、固定可配置权重、隐藏假路径或改变吞吐制造 PPA 优势。21 项旧回归、最新定向复验、映射功能、时序闭合分别验收。

## 后续验收门禁补强

缓冲时钟实现新增脉宽与16项覆盖检查，六组流程现共272项通过，见 [physical_flow_checks](../evidence/20260930/physical_flow_checks/README.md)。这些结果不替代实际布局布线或ASIC时序签核。

## CI 入口与算术激励修复

0c4a的CI最终为8个job成功、RTL job失败；其21bench/3lint步骤已成功，失败发生在后续独立算术入口。新补丁保留Ubuntu分离安装布局，避免无条件重设VERILATOR_ROOT，并用测试台时钟快照修复5.020对文件输入的组合更新调度。两版工具均通过40010 MAC、40000 ADC2、10048除法和8组flags；两个陈旧输入负控被检出。见 [完整21项CI来源](../evidence/20260930/ci_rtl_0c4a/README.md) 与 [算术审计修复](../evidence/20260930/arithmetic_runner_ci/README.md)。不把生产RTL为适应工具而改写；CI整体状态仍等待修复提交的真实运行。
