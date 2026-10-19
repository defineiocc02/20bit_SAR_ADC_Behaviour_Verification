# 20-bit SAR RTL 功能复核（Dr. RTL 方法，2026-10-07）

## 范围与判断

本次按本机 `asic-rtl-timing` skill 的“先确认源清单与功能边界、再区分 RTL 合同和实现证据”方式审查。目标仓库是 `outputs/sar_adc_review_20260919/calibration-closure`，当前 HEAD `01ed2b3e4fc4c7c9efd2153cfbfa5d456b10fb74`，工作树干净。以 `rtl/rtl_sources.f` 为入口检查全部 24 个生产 SystemVerilog 文件和两个参数头文件，并重点复核顶层、控制器、权重装载、校准归约、ADC2 解码、MAC、floor 除法及结果提交。

**本轮没有发现新的、已确认的默认配置功能错误。** 这不是全参数形式证明，也不是本轮重新跑过的 RTL 仿真、综合、STA 或模拟验证。现有算术/控制结论中，有几项是此前已有记录；本报告将其作为待遵守边界，不冒充新发现。

当前可执行环境没有 Verilator：`tools/run_open_rtl.py` 的定向运行在工具探测时退出码为 1，提示“Verilator is required; install it or set VERILATOR”。本次没有安装工具。`input_sha256.json` 保存本轮使用的源码与文件 SHA；本轮命令和失败日志在 `current_test_attempt.json` / `.log`。

有一项重要的来源核对：当前 24 个 RTL 文件及两个参数头的 SHA 与 2026-10-02 有界见证所封存的 26 个文件 **全部相同**。因此可把该见证视为“同一份 RTL 字节的历史动态证据”，但不能称为本轮重跑。2026-10-02 见证 manifest 的历史基线是 `71935ab54238c7ed638f5fa66c5ffb58e10287bb`；本轮 HEAD 更新的是文档/复现材料，RTL 源字节未变化。逐项比对结果在 `prior_rtl_identity_comparison.json`。

## 设计实际做什么

默认 `P_STRUCTURAL=1`。顶层把 7 路 Flash 温度计输入编码成 3-bit seed，两个 9-bit 粗 SAR 分别负责当前采样的 ping-pong bank；每个粗 SAR 固定高 3 位 seed，只对低 6 位逐次比较。共享 12-bit fine SAR 转换前一个 residue 的冻结上下文。18 个物理 slice 中每帧使用 8 个；池控制器将上一 acquisition 组转为 conversion 组，并从补集中选择下一组。16-phase 控制器没有等待外部比较器：`cmp_valid` 允许 SAR 暂停某次位判决，但 deadline 到达仍会取消未完成转换并报告错误。

`P_STRUCTURAL=0` 是兼容路径：外部已编码 SADC/ADC2 code 在 phase 8/14 捕获。它不是结构模式的等价实现，不能用其 ready/相位测试覆盖逐位 comparator handshaking。

校准分为两块：**系数训练/估计在片外；片上只有系数装载、物理权重归约与定点重构**。顶层并不含 LMS/RLS/SVD 学习器。数字系统保存每个物理单元的有效权重 `W`，它已把子阵列 bridge 衰减与有效反馈比例折入权重。名义总增益约 32 已经在 `G=Σ(W·a)` 中，校正后不能再额外乘/除 32。

## 校准数学与位宽复核

当前代码的整数关系为：

```text
T = Σ W
G = Σ (W·a)
R = Σ (W·s)
F = adc2_min_q + RTE((2·adc2_code+1)·(adc2_max_q-adc2_min_q) / 2^(N_ADC2+1))
num = (F-O)·2^30 - R·2^32 - T·I
shifted = (num + G·2^32)·2^20
word = floor(shifted / (G·2^33))
```

在 default contract 下，`W` 是 48-bit 无符号存储但合法范围为 `0<W<2^47`；Q32 电压 `F/O/I` 是 64-bit signed；`T/G` 是 64-bit unsigned；`R` 是 66-bit signed。`cal_residue_mac` 先显式扩位、分别对乘积和差值检查 96-bit signed 运算域；`shifted` 保留到 117 位，再取 `shifted >>> 33` 送 63-bit signed `div_floor`。除数是正的 unsigned gain。`div_floor` 对负且非整除结果下修 1，落实数学 floor；输出端先按 signed 商与 0、2^20 比较，再饱和到 20 位 offset-binary 字。

手工逐式对照源码，没有发现默认位宽/符号扩展不一致：ADC2 的码与 `2c+1` 是无符号；`max-min` 先扩为 65-bit signed；MAC 中总权重乘 signed injection 前先零扩展总权重；`rails` 符号扩展；floor 算法通过无符号幅值支持 signed 最小值。这些地方若删去显式 cast 或改作窄位表达式，会改变功能。

**已有边界 A：ADC2 RTE 的对象是相对 `adc2_min_q` 的增量。** 当前 RTL 算 `min + RTE(delta)`；当 `min` 是奇数且增量正好落在半整数时，它会与 `RTE(min+delta)` 相差 1 个 Q32 整数 LSB。这与冻结 RTL/Python contract 一致，不是默认 RTL/oracle mismatch；不得只改 RTL。历史 9-case RTL witness 覆盖了 6 个这类差异点并记录相对舍入合同通过。

**已有边界 B：配置合法不等于每个理论满幅都免于 MAC 溢出。** `cfg_ready` 检查载入完整、`min<max`、采样/量化 dither 互斥和写入空闲，但不检查所有权重/输入组合的 MAC 96-bit 工作域。历史 6-case 真实 RTL witness 确认 `cal_residue_mac` 能正确报告边界；合法写入范围允许某些放大系数导致 `acc_ovf`。应用软件/离线训练需要限制实际系数域，不能把 `cfg_ready` 解释成满幅算术安全证书。

`P_USE_ROW_TOTALS=1` 时，总权重来自 `weight_store` 的精确缓存树；on-tree 和 dither-column 仍取物理单元叶子。生产 top 的缓存与权重由同一个 `weight_store` 输出。直接单独例化 `cal_weight_reduce` 并启用缓存的集成方必须保证 `row_total[s] == Σ_u w_rom[s][u]`；模块不会自行检查陈旧缓存。

## 控制、复位、使能与样本归属

所有同步状态使用同一上升沿和同步低有效 reset。外部比较器数据/valid、配置总线、历史兼容模式 ready/code 都必须满足该时钟的 setup/hold；没有异步输入同步器或 CDC。模拟宏必须按数字接口交付有效时刻，不能把 `compare_enable` 当作 comparator reset/evaluate 脉冲。

| 边界 | RTL 接受/更新行为 | 主要功能条件 |
|---|---|---|
| 配置写/commit | `cfg_bus_ok` 在时钟沿门控写；控制字串行 3 沿后原子安装；`cfg_clear_valid` 撤销 ready 并使权重 bitmap 失效 | reset 优先；活动配置禁止改写；clear 保留旧权重值/行和，但新 epoch 仍必须把全部 1278 项重写并重新验证 |
| 相位/宏控制 | `analog_phase_ctrl` 同沿更新 phase 和注册 TP/AZ/reference/amplify enable | 复位后 steady phase-0 模拟电平需经历第一次 wrap 才出现；宏内非交叠和实际建立不由 RTL 证明 |
| seeded SAR | `reset/disable > cancel > idle start > busy && cmp_valid` | trial bit 与已决码分离；RDAC 只接受 resolved 更新；busy start 被忽略；cancel 清 busy/done pulse 但保留码/index；disable 清码 |
| slice 池 | advance 时旧 acquiring 组晋升 converting，再按补集扫描选下一组 | 首次 advance 仅 priming；扫描是固定长度组合逻辑；不是从全部 8-of-18 集合均匀抽样 |
| residue packet | phase 9 捕获当前物理 ID/mask/rails/injection/bad；下一 frame phase 0 转入 fine context | 同沿 capture/quiet 时 fine 收旧 packet，production schedule 把 strobe 分开；ID/物理 mask 必须同行冻结 |
| reconstruction | `cfg_ready && start && !busy` 时快照 ID、T/G/R、F/O/I 和 ADC2 数字 overflow；下一沿算 MAC/异常，再启动恢复除法 | COMMIT 沿 busy 已低，能提交旧 ID 同时接受下一 start；cfg-clear 通过组合进 recon reset 丢弃在途运算 |
| output/status | `dout_valid` 是完成事件；每次结果带 sample ID 与本样本 flags | `acc_ovf/gain_err` 时保留最后合法码；`adc2_ovf` 单独报告但不禁止更新；模拟异常另走 status sticky，不在 5-bit `dout_flags` 内 |

结构模式默认每 frame 16 个 clock interval。粗 SAR 接受 seed 后需 6 次有效 comparator 决策；fine SAR 需 12 次；任何缺失 valid 不会自动把整帧拉长，deadline 会取消并丢该 conversion。默认 `P_RECON_STAGES=7` 时从 start 到 `dout_valid` 的合同延迟为 `ceil(63/7)+2=11` 个完整周期；`P5` 是历史综合使用设置，延迟为 15 周期。不能把两套设置的 STA/吞吐结果混用。时钟期望/相位逻辑计算不是 STA，也不说明 comparator settling 或模拟宏 bandwidth。

## 全部 24 个生产模块的功能复核

| RTL 模块 | 功能与接口边界 | 功能核对要点 |
|---|---|---|
| `top/sar20_digital_core` | 配置、profile、物理开关、重构、状态集成 | 默认结构 profile；两路径是 elaboration 选择；外部模拟输入需同步；clear 同时 reset 结构/重构状态 |
| `top/sadc_enc` | thermometer input 的平衡 popcount | 不检测 bubbles/亚稳；结构模式 7 个比较器编码 3-bit seed；standalone 511 输入参数不是生产 flash 宽度 |
| `analog_phase_ctrl` | phase 计数及注册模拟使能 | `phase` 表示前沿之前的 phase；数字 enable 不是模拟开关/非交叠验证 |
| `sar_structural_ctrl` | coarse/fine 并行调度、deadline、ID 和 packet | comparator ready 丢失会 drop；错误码为单个最近事件寄存器，有覆盖优先级而非事件 FIFO |
| `sar_trial_ctrl` | 固定高位 seed + 低位二进制 SAR | trial/resolved 分开；有效比较才推进；没有重看 flash seed 高位的冗余修正 |
| `slice_pool_ctrl` | 18 物理 slice 的 8-of-18 因果池选择 | 先用上一 acquisition、再准备下一组；初始 priming；LFSR 选扫描起点不等于均匀集合抽样 |
| `slice_alloc` | 兼容 profile 的 0..15 固定 A/B ping-pong | 仅兼容模式；16/17 spare 永远不用，与结构池不同 |
| `dem_state_gen` | A/B 两个 9-bit DEM 状态 | 两 bank 独立加 433 mod512；同沿消费者用旧 sid，随后状态推进 |
| `dem_addr_gen` | sid 到主/子物理单元逻辑顺序 | 仅已验证 63/8、8×8 拓扑；不含状态；物理索引和值不可倒置 |
| `dither_gen` | 8步展开 LFSR + 离散整数 dither | 默认 D=2 下 valid 恒1；其他 D 可能拒绝尾部 raw，调用点未对同一样本做重抽，当前生成参数下不触发 |
| `swap_decode` | 粗码+dither、clip、bridge 零和交换、切分主/子计数 | signed k 命令、先裁剪、再桥接；分拆依赖非负及 512 电平码范围 |
| `unit_therm` | 逻辑位置/计数→物理热码掩码和采样轨 | 位序对齐物理权重；只做组合译码，不实现模拟驱动/非交叠 |
| `rdac_drv` | 8路物理命令写进 18行寄存器 | load 清旧行再 scatter；重复 ID 后写覆盖前写，依靠上游唯一性不变量 |
| `weight_store` | 18×71 权重、written bitmap、行和 | 合法 `0<W<2^47`；clear 只清 bitmap，数值保留并需从旧值增量更新 |
| `calib_regs` | Q32 offset/min/max 与控制配置提交 | signed `min<max`、权重/标量齐、dither 互斥、禁止活动期写；不做 MAC 域检查 |
| `cal_weight_reduce` | 物理权重转 T/G/R | 重复 ID 按物理行 OR 且报 invalid；cached row total 要求与 w_rom 同源 |
| `cal_sample_context` | residue → fine 旧样本 packet | 保存 ID+物理 mask+rails+sampling+injection；模拟异常在规定 quiet 边沿采样 |
| `adc2_dec` | raw ADC2 code→Q32 center | 增量 RTE、宽 signed range、异常时钳位；不推断模拟 ADC2 越轨 |
| `cal_residue_mac` | 快照的 F/O/I/T/G/R→受检 shifted A1 | 先对 96-bit 合同检查，溢出时 A1 不可信且后级由 flag 阻止覆盖合法输出 |
| `div_floor` | signed numerator / unsigned gain 的有符号 floor | 迭代恢复余数；P_STAGES 决定单周期链长与迭代周期数；零分母定长报错 |
| `recon_core` | 捕获、MAC、迭代除法和事务提交 | ID/flags 与数值快照同行；start busy 不接收；默认 P7 与历史 P5 延迟不同 |
| `cal_output_stage` | 20-bit码/ID/flags 原子提交及算术 sticky | valid 不是“结果一定合法”；acc/gain错误保留旧码；ADC2 numeric overflow 可伴随新码 |
| `status_regs` | sticky error/clip 与 status_word 拼接 | 4类 sticky、2类当前 clip；err_code 组合透传；不是模拟异常的逐样本 FIFO |
| `ctrl_fsm` | 兼容 profile 的 16-phase 节拍 | 不可套用到结构模式；sample_idx 与 slice_alloc 同沿推进 |

## 已知问题与未闭合验证边界

1. **没有新增确认的默认 profile 算术错误。** 这是源码与已有有界证据的综合判断，不等于对所有异步、亚稳、非法输入、参数组合、故障注入的完整覆盖。
2. **MAC 工作域安全需要软件/离线校准约束。** 历史受限见证说明载入器接受的某些系数仍可让 `acc_ovf`。验收应把允许的 G、输入/offset/injection 范围与模拟可用范围写成机器可检验约束。
3. **Analog error 与本样本归属有输入保持条件。** `cal_sample_context` 在 quiet/transfer 边沿采 `analog_bad`；capture 时冻结的 `current_bad` 只有 RDAC range。宏错误若未维持到接口约定沿会丢失或被归到另一个上下文。模拟状态不包含在每笔 `dout_flags`，软件只能读 sticky 状态；需定义过期、取消、busy 拒绝的 fault 归属策略。
4. **Flash seed 对高位错误没有修复能力，popcount 不认证温度计码。** 生产代码用 3 个最高 seed 位加 6 个低 SAR 位。bubble 或错误 seed 跨过 64-code bucket 会导致高位错误无法由 6 次低位搜索纠正，宏需给定 seed/valid 精度合同并做 offset/bubble/迟到扫描。
5. **模型与论文/专利的实现程度有限。** 当前同步 binary SAR、有限相位、线性增量 RTE、整数权重学习合同、两种互斥 dither 模式、静态数字开关选择均是工程实现选择；论文/专利的双 dither、ADC 跨通道 tracking、模拟 sampling/precharge/AZ/参考切换/settling 不是由这些数字模块完整证明。该项属既有审查结论，不是本次新增的源代码缺陷。
6. **参数化 dither valid 路径只对当前默认安全。** `dither_gen` D=2 时 `LIMIT=256`，valid 恒1；若将头文件生成成其他不整除 256 的 D，拒绝区间可被抽中，顶层 `dith_q` 保持旧值、结构控制器写零，均未对同一个样本重抽。应锁定 D=2，或统一修改调用协议与测试。当前默认参数不触发。
7. **时序/物理边界需要单独验证。** 本次未运行 STA；代码的 16 相位和 11/15周期 latency 只是 RTL 周期合同。没有比较器建立、TP开关偏差、RA/reference settling 或物理 setup/hold 的新证据。历史 FPGA/ASIC报告应按其自己的 source hash/器件/工艺、约束阶段分别陈述，不把历史 P5 结果套到源码默认 P7。

## 证据与可复现建议

本轮 `current_test_attempt.log` 精确保留了尝试的测试命令与失败原因；不以工具缺失记作 RTL 失败，也不将缺少结果包装为 PASS。源复核使用的所有文件 SHA 在 `input_sha256.json`；当前与历史封存源身份的 26 项比较在 `prior_rtl_identity_comparison.json`。

### 当前 HEAD 对应的 GitHub CI 封存记录

补充核对 `../baseline/github_pr_ci.json`：GitHub 检查记录绑定 `headRefOid=01ed2b3e4fc4c7c9efd2153cfbfa5d456b10fb74`，9 项状态均为 `SUCCESS`，完成时间为 2026-10-02。名称包括 Verilator RTL simulation、ruff、mypy、Python 3.10/3.11/3.12/3.13 测试、determinism 和 wheel CLI。对应 `.github/workflows/ci.yml` 中的 RTL job 除 flow 单测外，还运行 `tools/run_open_rtl.py` 和 `tools/audit_rtl_arithmetic.py`；Python 3.12 测试 job 另运行 acceptance sweep。因此，这是该精确 HEAD 的 CI 动态证据，补足本机缺少 Verilator 时“本轮未重跑”的边界。

证据边界：9 项检查并不等于 9 组 RTL 测试；它们混合了 RTL/算术回归、软件质量、跨 Python 版本、可重复性与安装检查。CI 只证明 workflow 中实际执行的有限场景通过，不证明全参数或故障状态覆盖，不证明模拟宏行为/样本级模拟 fault 归属，不构成形式证明，也不提供综合、FPGA 新布线、ASIC PPA 或 STA 结论。本报告中的动态 CI 结论应理解为“已封存记录对应当前审查 HEAD”，而非 2026-10-07 当天重新触发。

历史同源码有界动态证据包括：

- `docs/evidence/20261002/principle_reaudit/control_minimal_witness/result_manifest.json`：Verilator 5.020；1,926 个沿、57 次成功接受、1,920 个结构协议检查；context 到接受 age 15T，fine_code 到接受 age 1T。它是 finite controller wrapper，不含真实全顶层重构/门延迟/模拟 settling/SEC。
- `docs/evidence/20261002/principle_reaudit/calibration_evidence_manifest.json`：两个定向 bench 的精确源副本及运行命令；ADC2 RTE 9例，MAC 工作域 6例；记录了基础镜像和放大 `2^20` 但仍满足权重写入界的边界镜像。它不是 full top load+conversion，也不是形式证明。
- `sim/artifacts/open_rtl/*.run.log` 的 P2/P3/profile logs 存在并显示 PASS，但该 ignored logs目录未提供本轮完整 24 RTL SHA 与每次执行命令的联合锁定；本报告把它们视作历史辅助记录，而不作为本次新仿真门禁。

回到有 Verilator 5.x 与 C++20 的固定环境后，建议先重跑现有 `tools/run_open_rtl.py`：

```sh
python3 tools/run_open_rtl.py --tops sar_trial_tb review_config_tb review_recon_protocol_tb structural_protocol_tb p2_tb p3_top_tb
```

保存 runner 输出目录 `sim/artifacts/open_rtl/` 全部日志，并把当前 `input_sha256.json` 一同绑定到运行记录。随后把模拟接口输入时刻、MAC合法工作域、cfg-clear取消、first valid/disable/re-enable 及 per-sample fault ID 加入可机器判定回归。功能验证通过以后，按 skill 要求另行用指定库、SDC、角和寄生条件做实际综合/STA；本分析不晋级任何 RTL 改写候选。
