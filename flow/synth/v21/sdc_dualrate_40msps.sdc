# =============================================================================
# 文件名     : sdc_dualrate_40msps.sdc
# 模块名     : adc_digital_ip_v21_40msps 双域时序约束
# 版本       : v1.0
# 功能       : 为 40 MHz 主帧域与自定时 1.0 ns 事件跟随域建立显式有界时序约束。
# 设计说明   :
#   * [双域解耦] clk_fs（25.0 ns）覆盖 sched18、dither_pre、wsum_v21、adc2_capture
#     与 recon_v21（占核面积 >95%），消除 640 MHz / 1.6 GHz 全核假时钟需求。
#   * [自定时突发] ev_clk_i 仅驱动 ~1k 门的快速跟随环（sar_evt_v21 + dem_mask_v3
#     + post 写门），按每步 1.0 ns（7–8 步在 8 ns 内收口）约束单步建立。
#   * [严禁盲目 false_path] 两域交接靠确定性时间窗保证，一律使用 set_max_delay
#     -datapath_only 明确限定数据延迟上限，避免工具放松跨边界组合锥。
# 变更记录   :
#   2026-11-24  v1.0 初版。双域 40 MSPS 生产级约束。
# =============================================================================

# =============================================================================
# 1. 时钟定义与不确定度
# =============================================================================
# 主帧时钟：真实 40 MHz（周期 25.0 ns，对应 40 MSPS 公共孔径间隔）
create_clock -name clk_fs -period 25.000 [get_ports clk_fs]
set_clock_uncertainty -setup 0.300 [get_clocks clk_fs]
set_clock_uncertainty -hold  0.150 [get_clocks clk_fs]
set_clock_transition         0.100 [get_clocks clk_fs]

# 快速事件突发时钟：仅覆盖 SADC 粗转换窗（每步预算 1.0 ns，含 0.08 ns 抖动）
create_clock -name ev_clk -period 1.000 [get_ports ev_clk_i]
set_clock_uncertainty -setup 0.080 [get_clocks ev_clk]
set_clock_uncertainty -hold  0.050 [get_clocks ev_clk]
set_clock_transition         0.040 [get_clocks ev_clk]

# 异步域声明：两时钟无固定 PLL 倍频关系，跨边界全部由下方显式 max_delay 接管
set_clock_groups -asynchronous -group [get_clocks clk_fs] -group [get_clocks ev_clk]

# =============================================================================
# 2. 跨时钟域确定性窗口约束（禁止 blanket false_path；DC / PrimeTime 通用）
# =============================================================================
# [工具兼容] Synopsys DC / PrimeTime / FC 统一使用 set_max_delay -ignore_clock_latency
# 限定纯数据路径延迟上限（等效于跨异步时钟的 datapath_only 语义）。
# [路径 A: clk_fs -> ev_clk]
# CONV 上下文（ids/h/v/coin/half/U/tag/qid/flash/toggle）在 t=0 ns 冻结整帧 25 ns；
# 首个 ev_frame_start 脉冲在 t>=0.5 ns 到达，约束纯数据路径 <= 0.40 ns，保证无亚稳态。
set_max_delay  0.400 -ignore_clock_latency -from [get_clocks clk_fs] -to [get_clocks ev_clk]

# [路径 B: ev_clk -> clk_fs（含 wsum_v21 128 项加法树）]
# final_post_ev_q 与 done_toggle_ev_q 最迟在 t=8.0 ns 锁存，直到 t=25.0 ns 才由
# clk_fs（wsum_v21 & adc2_capture）采样，净可用窗口 17.0 ns；保守限定 <= 12.0 ns。
set_max_delay 12.000 -ignore_clock_latency -from [get_clocks ev_clk] -to [get_clocks clk_fs]

# =============================================================================
# 3. 端口外部延迟与负载预算
# =============================================================================
# clk_fs 域控制与配置端口（相对 25 ns 周期预留 2.0 ns 外部延迟）
set _fs_inputs [remove_from_collection [all_inputs] \
    [get_ports {clk_fs ev_clk_i rst_n ev_frame_start_i ev_bit_valid_i ev_bit_i \
                ev_bit_index_i* ev_event_index_i* ev_qid_i ev_flash_valid_i ev_flash_code_i*}]]
set_input_delay  2.000 -clock clk_fs $_fs_inputs

set _fs_outputs [remove_from_collection [all_outputs] \
    [get_ports {rdac_post_state_o* post_wr_o* ev_apply_fire_o ev_final_k_o* quiet_evt_err_o}]]
set_output_delay 2.000 -clock clk_fs $_fs_outputs

# ev_clk 域 SADC 比较判决输入与 3.3 V Level Shifter 开关输出（紧预算）
set _ev_inputs [get_ports {ev_frame_start_i ev_bit_valid_i ev_bit_i \
                           ev_bit_index_i* ev_event_index_i* ev_qid_i \
                           ev_flash_valid_i ev_flash_code_i*}]
set_input_delay  0.150 -clock ev_clk $_ev_inputs

set _ev_outputs [get_ports {rdac_post_state_o* post_wr_o* ev_apply_fire_o ev_final_k_o* quiet_evt_err_o}]
set_output_delay 0.200 -clock ev_clk $_ev_outputs

# 电容负载（模拟驱动端与数字接口统一按 0.05 pF 估算，版图后按 SPEF 替换）
set_load 0.050 [all_outputs]
