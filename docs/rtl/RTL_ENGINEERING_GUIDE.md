# RTL 工程入口与控制协议

本文件解释当前代码的职责、状态和观测边界。它与源码一起阅读；状态图、模块名和注释本身不构成功能、时序或模拟电路验收。冻结测试与原始综合报告仍是验证证据。

## 1. 源码入口与两种结构

`rtl/rtl_sources.f` 是编译清单，包含 **22 个 core 模块与 2 个 top 目录模块**；另有 2 个参数头文件通过 include 使用。共 26 个生产 RTL 文件。`sadc_enc` 位于 top 目录，是可独立测试的编码器，不是第二个完整 ADC 顶层。

| 选择 | 输入和控制 | 实际职责 |
|---|---|---|
| `P_STRUCTURAL=1`，默认 | `flash_therm/flash_valid`、两路 coarse 比较器、一路 fine 比较器 | 双 9-bit seeded SAR、共享 3-bit Flash、18-slice 池与前代 residue 上下文；使用 `analog_phase_ctrl/sar_structural_ctrl` |
| `P_STRUCTURAL=0` | `sadc_code/sadc_rdy`、`adc2_code/adc2_rdy` | 兼容既有码域向量；phase 8/14 捕获外部完整码，使用 `ctrl_fsm/slice_alloc` |

两种选择都是**精化参数**，不是运行时可切换模式。结构模式在内部实例化 `sadc_enc(P_B1=3)`，不能沿用“Flash 始终位于数字核外”的历史描述。两种模式共享配置、物理系数和定点重构核；不能用兼容模式的 ready 或相位表解释结构模式的逐位比较。

固定点为权重 Q30/48 位、电压 Q32/64 位、受检累加 96 位和输出 20 位。合法权重只占 47 个数值位，必须满足 `0 < W < 2^47`。这些是本工程整数契约，不能据此推导 20-bit ENOB。几何固定为 18×71 系数、8 活跃 slice、63 主单元与 8 子单元。

## 2. 模块职责与调试入口

| 文件 | 职责 | 首选观测点 |
|---|---|---|
| `core/analog_phase_ctrl.sv` | 相位计数、注册 TP/AZ/参考/放大使能 | `phase/next_phase` 与注册输出 |
| `core/sar_trial_ctrl.sv` | seeded 二进制 SAR，隔离 trial 与已决码 | `busy/bit_index/trial_code/resolved_code/done/resolved_valid` |
| `core/sar_structural_ctrl.sv` | 结构模式集成、deadline、前后代样本控制 | `bank/sid/coarse_ok/fine_ok/error_code` |
| `core/slice_pool_ctrl.sv` | 上周期 acquiring→converting，补集选择下一组 | `acquiring_ids/converting_ids/primed/conversion_valid/cursor` |
| `core/cal_sample_context.sv` | 当前 residue 与前代 fine 物理上下文 | `residue_valid/fine_valid/fine_id/fine_slices` |
| `core/ctrl_fsm.sv` | 兼容模式 phase 与固定脉冲 | `ph/adv/sample_idx` |
| `core/slice_alloc.sv` | 兼容模式分配与样本序号 | `n/seen_sample/acq_slices/conv_slices` |
| `core/dem_state_gen.sv` | A/B 独立 DEM 状态推进 | `sid_a/sid_b/en_a/en_b` |
| `core/dem_addr_gen.sv` | 物理单元到主/子阵列逻辑位置 | `sid/main_logical/sub_logical` |
| `core/swap_decode.sv` | 粗码与 dither 到主/子计数、bridge 交换 | `main_count/sub_count/rdac_over` |
| `core/dither_gen.sv` | 确定种子的离散随机码与 valid | `dither_code/valid` |
| `core/unit_therm.sv` | 逻辑位置与计数到物理开关掩码/轨 | `main_on/sub_on/dither_rail` |
| `core/rdac_drv.sv` | 接受物理命令并注册数字输出 | `load/slice_sel/main_sw/sub_sw/dither_sw` |
| `core/weight_store.sv` | 系数、完整位图、精确行和、读回 | `accept/written/row_q/old_by_slice/load_complete` |
| `core/calib_regs.sv` | 三标量、四控制位与 validate | `scalar_written/cfg_ready/err_code` |
| `core/cal_weight_reduce.sv` | 物理掩码归约得到 T/G/R | `sum_W/sum_Wa/rails/invalid_slice` |
| `core/adc2_dec.sv` | ADC2 码仓中心，round-half-even | `fine_q/ovf` |
| `core/cal_residue_mac.sv` | 快照后的精确定点残差运算 | `a1/any_ovf` |
| `core/div_floor.sv` | 固定拍数恢复除法与负数 floor | `run/cnt/rem/quo/q/done/err` |
| `core/cal_output_stage.sv` | code/ID/flags 原子提交与粘滞异常 | `complete/dout_valid/result_flags` |
| `core/recon_core.sv` | 捕获、MAC、除法、提交的调度 | `stage_b/div_busy/div_done/sample_id_r/gain_s/rails_s` |
| `core/status_regs.sv` | 粘滞事件、上游组合错误码拼接与状态读回 | `status_word` |
| `top/sadc_enc.sv` | 温度计输入的平衡计数树 | `cmp_raw/count_tree/sadc_code` |
| `top/sar20_digital_core.sv` | 总线仲裁、profile、完整数字核集成 | `cfg_bus_ok/ctrl_seq/launch_recon/dout_sample_id/dout_flags` |

`params/rtl_params.vh` 是参数导出器生成物，不手工改动；`params/rtl_error_codes.vh` 是静态接口错误码。数值规格改变需先修改来源配置和导出契约，再运行漂移检查；源码排版不能改变参数或归约树。

这些观测点是现有内部网或端口，便于 testbench 波形定位；它们不是新增产品接口，也不意味着需要把宽调试总线保留到最终实现。

## 3. 同步前沿和非阻塞赋值

所有顺序逻辑使用统一 `clk` 上升沿、同步低有效复位。配置总线、比较器 valid/data 和兼容模式 ready/code 都必须满足这个时钟的建立保持时间；当前代码没有替异步输入加入 CDC。

本指南的“phase p 沿”始终指**上升沿到来前 phase=p**。`analog_phase_ctrl` 在该沿安装 `next_phase`，模拟控制在随后相位区间生效。例如 phase 7 沿将计数更新为 8，同时令 coarse cancel 注册为 1；phase 8 沿控制器读取上个沿的 `coarse_done`，SAR 读取 cancel 并清除 busy/done。不能把这两次前沿混写成一次比较。

同一个上升沿，所有 `always_ff` 读取旧寄存器值；非阻塞赋值在随后统一生效。因此：

1. `sid <= sid_a/sid_b` 与 DEM 状态推进同沿，当前样本使用旧 sid。
2. pool 的 converting 接收旧 acquiring，next acquiring 使用旧 cursor；PRNG 更新改变的是下次选择的 origin。
3. SAR 的 resolved_valid、已决码与 done 在判决沿产生，RDAC/截止控制在下一沿消费。
4. 当前 residue 捕获与 fine 上下文转交不是同一笔。若人为同沿置两个 strobe，fine 仍收到旧 residue，后写的 `residue_valid<=0` 优先；默认 phase 9/0 把两者隔开。
5. 重构完成沿可以同时接收下一 start；提交使用旧 `sample_id_r`，新的 ID 在 NBA 后进入下一笔快照。

RTL 仿真观测应在 NBA 更新后记录电平，并另保留“前沿接受条件”。仅看打印时的 phase 或 done，容易产生一拍偏移。

## 4. 结构模式相位表

以下是默认 `P_RESIDUE_CAPTURE=9/P_REF_ON=10`。相位不停等 comparator-valid；缺失判决会在截止处取消并报错。

| 前沿 phase | 数字事务 | 采样归属 |
|---|---|---|
| 0 | bank 交换；旧 residue→fine；锁存 dither/injection；清 coarse_ok/fine_ok | fine 属于前代，coarse 准备当前代 |
| 1 | pool 晋升；coarse 以 3-bit flash seed 启动；若 fine context 有效则启动 ADC2；锁存旧 sid/random 输出 | 当前 coarse 与前代 fine 并行 |
| 2..7 | 最快每沿一次粗比较，六次解决其余 6 bit | 当前粗码 |
| 2..8 | RDAC 接收上个沿已决码的 update；phase 2 首次写 seed，phase 8 可写最终决定 | 不接收 pending trial bit |
| 8 | 锁存 coarse_done→coarse_ok；cancel 清 SAR | 未完成当前 coarse 不生成有效 residue |
| 9 | 捕获当前物理 slice IDs、开关掩码、轨、注入和有效性；默认参考死区 | 保存给下周期 fine |
| 2..13 | 最快十二次 fine 比较 | 前代 residue |
| 14 | 锁存 fine_done/fine_code；cancel 清 fine SAR | 前代细码 |
| 15 | valid context、fine_ok 且 recon 空闲时启动数字重构 | 输出 ID 来自冻结 context |

注册电平的**当前 phase 区间**如下，与表中的“前沿事务”口径不同：

| 信号 | 稳态为高的 phase |
|---|---|
| TP | 1..15 |
| RA AZ | 1..8 |
| reference precharge | 2..8 |
| RA amplify / accurate reference | 0、10..15 |
| flash acquire | 0、2..15；phase 1 低 |
| fine acquire | 0、14、15 |
| coarse cancel | 8、9 |
| fine cancel | 14、15 |

这只是数字宏边界的使能电平。非重叠开关、比较器复位/求值、AUX 实际路径、参考储能电容和建立时间仍由模拟宏实现并验收。

### 首轮启动不是稳态

复位后 phase=0，全部注册模拟使能为 0；稳态 phase 0 的 amplify/accurate-reference 高电平在首次 wraparound 后才出现。初始 pool acquiring IDs=0..7，converting 无效。第一次 advance 将 `primed` 置 1，`conversion_valid` 读取旧 primed=0；第二次 advance 才有效。coarse valid residue 再到下一周期才成为 fine context。因此“刚 cfg_ready 就有输出”不成立；必须依据 priming 与 context_valid 判断启动填充。

### SAR 转移优先级

`busy` 是实际状态位，没有单独枚举 FSM。前沿优先级：reset/disable > cancel > idle start > busy valid comparison。cancel 保留码和 index，但清 busy/done/resolved_valid；disable 清全部。busy 状态的 start 被忽略，若同时 cmp_valid 则仍正常判决。cmp_valid=0 保持 trial/index。最后一个有效判决使 done=1 一拍。

`compare_enable=enable && busy && !cancel` 是求值许可电平，不是模拟时钟或复位脉冲，且没有异步 rst_n 门控。图中若标出 reset，必须表达同步沿行为。

## 5. 兼容模式单独联调

`ctrl_fsm` 组合脉冲为 sample 0、SADC 8、DEM 10、RDAC 11、ADC2 14、recon 15，acquisition=0..6。phase 8 必须 `sadc_rdy=1`，phase 14 必须 `adc2_rdy=1`；只有对应采样沿有效，不追随后续 live code。injection 与模拟异常在 phase 14 同沿捕获。

`adv=rst_n && cfg_ready && run`，停机或未配置立即抑制组合脉冲，下个沿把 ph 归零；sample_idx 仅 reset 清零，停机保持。顶层在配置生效后连续运行，不提供独立 run 端口。该模式用于既有码域 oracle，不能证明内部 coarse/fine 逐位比较或完整模拟宏功能。

## 6. 配置状态与读写窗口

总线无独立 ready/ack。在 `rst_n=1` 的正常上升沿，写入是否被接受以顶层 `cfg_bus_ok` 为准；reset沿即使该组合条件为高也优先复位；非法写产生可定位错误，组合回读没有额外读握手。

| 地址 | 用途 | 合法写条件 |
|---|---|---|
| `0x0000 + 8*u`，u=0..70 | 当前 slice 的 Q30 权重 | 地址对齐、64-bit 高位为0、0<W<2^47 |
| `0x2000 + 0x100*s`，s=0..17 | 选择权重 slice 窗口 | 低8位为0，slice合法 |
| `0x1000/0x1008/0x1010` | offset/min/max，signed Q32 | cfg 尚未生效，提交/clear/控制序列空闲 |
| `0x1018` | `{quantizer,sampling,bridge,DEM}` | 高60位为0；四位随后原子更新 |
| `0x1020` | status 只读 | 写入拒绝 |

所有写还要求 `!cfg_ready && !cfg_validate && !cfg_clear_valid && ctrl_seq==0`。当前 slice 窗口和写权重不是同沿旁路操作，必须先完成窗口写，再写该 slice 的 71 项。

控制写接受沿把 `ctrl_seq=3` 并捕获四位；后续三沿执行 3→2→1→0，只有旧 seq=1 的第三沿在 calib_regs 原子安装四位。等待中不能写其他寄存器。`cfg_validate` 与等待冲突会暂停 seq 并拒绝提交；它不能用来提前完成控制写。

配置的逻辑状态为 LOAD、CTRL_WAIT、ACTIVE。commit 是一次检查请求：无 raw cfg_wr、无 ctrl_seq、无 recon busy，当前 epoch 的 1278 权重与三个标量都写齐，min<max，sampling 与 quantizer 不同时开启，才使 cfg_ready=1。提交冲突保留现有 ready；合法性失败报告具体错误。转换中系数与控制位锁定，不支持无停机重载。

`cfg_clear_valid` 优先，取消控制序列、清 ready/bitmap/scalar_written、复位窗口并中止数字转换；**保留权重、行和缓存及三个标量数值**。旧读回数值不代表新 epoch 完整，新 epoch 要重新写满。每次替换行和为 `H_new=H_old-W_old+W_new`，clear 后也必须减去保留的旧值。rst_n 复位才把数值清零。

顶层错误读回优先级为总线错误 > 配置错误 > 权重拒绝 > 当前 profile 控制错误。状态表中的 ACTIVE/WAIT 是现有寄存器的解释，不是新增枚举状态硬件。

## 7. 重构捕获、提交与异常归属

`recon_core` 仅在 `cfg_ready && start && !busy` 接受一次转换。该沿捕获 T/G/R、后级 bin center、offset、injection、ID 和后级异常。下一沿依据本次快照求 MAC 并捕获算术异常，同时启动唯一迭代 floor 除法。负商的非零余数需要额外减一，不能用向零除法替代。

实际控制编码为 `stage_b/div_busy/div_done`：IDLE→MAC_LAUNCH→DIV_RUN→COMMIT事件。COMMIT 时 busy 已为0，输出模块在下一沿读取 div_done，故可同沿接受新请求。不要为了画图人为增加一拍 busy 状态。

延迟为 `ceil(63/P_RECON_STAGES)+2` 个完整时钟间隔：P5/P6/P7 分别 15/13/11。顶层允许 P_RECON_STAGES=5..63，以满足16相位帧预算；改阶段数要复查接受间隔、ID 与flags，而不是只看除法器速度。

输出 `dout_flags[4:0]={clip_high,clip_low,adc2_ovf,gain_err,acc_ovf}`。acc_ovf/gain_err 时仍产生完成事件和本次 ID/flags，但保留上次合法字及 clip 电平；单独 adc2_ovf 不阻止字更新。下游必须同时消费 valid、ID、flags，不能把“valid=1”解读为数值一定合法。顶层模拟异常通过独立状态通道报告，不是由细码是否到轨反推。

`clr_ovf` 清粘滞算术事件，同沿新错误优先再置位；它自身不是取消。顶层 cfg_clear 同时撤销 epoch，因此另外取消状态机与在途结果。

## 8. 波形定位与验证顺序

| 症状 | 先检查 | 再检查 |
|---|---|---|
| cfg_ready 不起 | 1278 written、3 scalar_written、ctrl_seq | min/max、dither模式、提交时 raw cfg_wr/rc_busy |
| 开机无输出 | primed/conversion_valid/context_valid | coarse_done@8、fine_done@14、launch@15 |
| 粗码或RDAC偏一位 | bit_index/trial/resolved/cmp_valid | seed 写入与 resolved_valid 的下一沿消费 |
| 输出错配样本 | residue/fine ID与冻结物理IDs | sample_id_r、COMMIT同沿下一start |
| code异常但仿真未报错 | dout_flags和status | T/G/R/F/O/I 快照与独立整数 oracle |
| 清配置后有旧结果 | epoch_rst_n/cfg_ready/div reset | dout_valid取消、written/scalar完整性重新建立 |

建议从 `sar_trial_tb`、`structural_protocol_tb`、`review_config_tb`、`review_recon_protocol_tb` 定位协议，再运行全部23bench（原22项及新增FSM见证）与3种严格lint。实际命令由 [run_open_rtl.py](../../tools/run_open_rtl.py) 维护，例如：

```sh
python tools/run_open_rtl.py --tops sar_trial_tb structural_protocol_tb review_config_tb review_recon_protocol_tb
python tools/run_open_rtl.py
```

要求 Verilator 5.x 的 timing 支持及 C++20 工具链；`VERILATOR` 环境变量可以指定可用执行命令。日志写入 `sim/artifacts/open_rtl/`；缺工具、编译失败、缺完成标记都会失败。每项 testbench 的断言范围不同，不能把所有检查数相加后当成同一个仿真覆盖率。

源码工程化修订严格保持有效 token 与预处理指令不变；这能说明电路表达式未修改，却不能代替编译/仿真或形式等价。以前的真实 Vivado/XSim/route 证据仍只直接证明它所绑定的原文件字节。最新文本与其语义关系、当前复验和报告发布摘要由交付清单逐项记录。

综合、BUFG OOC 布线、setup/hold、约束覆盖及后续 ASIC 流程见 [FPGA_SYNTHESIS_AND_STA.md](../../synth/FPGA_SYNTHESIS_AND_STA.md)。不要用解释状态机图证明 PPA、布线成功、640 MHz 或论文模拟性能。与来源的机制对应和未实现项目见 [FULL_AUDIT_20260930.md](FULL_AUDIT_20260930.md) 及 [ADR0018](../adr/0018-physical-calibration-and-structural-controls.md)。
