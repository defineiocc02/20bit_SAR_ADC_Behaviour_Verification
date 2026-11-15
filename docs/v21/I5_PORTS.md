# I5 端口时序表（79 端口，9 组）

时间：2026-11-15　顶层：`rtl/v21/adc_digital_ip_v21.sv`
依据：方案 v1.4 §4.1。**此表与工艺无关**，40 nm 重映射时原样复用。

## 全局约定

| 项 | 值 |
|:---|:---|
| 时钟 | `clk` 单域，40 MS/s → **周期 25 ns** |
| 复位 | `rst_n` 异步置位、同步释放 |
| 边沿 | 除复位外全部在 `clk` **上升沿**接收 |
| 写门语义 | `pre_wr`/`post_wr` 与数据**同沿注册**；无命令时为 0 |
| 空泡 | 写门 0，已写状态保持 |


## (未分组)

*—*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `clk` | input |  1 | posedge |  |
| `rst_n` | input |  1 | posedge |  |

## 事务

*prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `prepare_valid` | input |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_ready` | output |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_tag` | input |  [31:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_word` | input |  [5:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_h` | input |  [2:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_v` | input |  [2:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_half` | input |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_coin` | input |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_U` | input |  [2:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_flash` | input |  [2:0] | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `prepare_qid` | input |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |
| `stop_i` | input |  1 | posedge | prepare 握手：valid&ready 同高沿接受；被拒不推进 PRNG |

## 采样事件

*公共沿后的资格报告，与 sample_event 同拍*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `sample_event` | input |  1 | posedge | 公共沿后的资格报告，与 sample_event 同拍 |
| `input_captured` | input |  1 | posedge | 公共沿后的资格报告，与 sample_event 同拍 |
| `residue_captured` | input |  1 | posedge | 公共沿后的资格报告，与 sample_event 同拍 |
| `hold_ack` | input |  1 | posedge | 公共沿后的资格报告，与 sample_event 同拍 |

## 事件入口

*已决事件，posedge 接受；与 CONV 上下文原子绑定*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `ev_start_i` | input |  1 | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_bit_valid_i` | input |  1 | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_bit_i` | input |  1 | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_bit_index_i` | input |  [3:0] | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_event_index_i` | input |  [3:0] | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_qid_i` | input |  1 | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_flash_valid_i` | input |  1 | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |
| `ev_flash_code_i` | input |  [2:0] | posedge | 已决事件，posedge 接受；与 CONV 上下文原子绑定 |

## 片组

*sched18 输出或外部覆盖，随 commit_fire 推进*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `sched_ext_sel` | input |  1 | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_acq` | input |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_conv` | input |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_idle` | input |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_ids` | input |  [39:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_seed_we` | input |  1 | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `sched_seed` | input |  [31:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `acq_sel_o` | output |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `conv_sel_o` | output |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |
| `idle_sel_o` | output |  [17:0] | posedge | sched18 输出或外部覆盖，随 commit_fire 推进 |

## 物理宏接口

*写门与数据同沿注册（§4.1）*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `rdac_pre_state_o` | output |  [71:0] | posedge | 写门与数据同沿注册（§4.1） |
| `pre_wr_o` | output |  [17:0] | posedge | 写门与数据同沿注册（§4.1） |
| `rdac_post_state_o` | output |  [359:0] | posedge | 写门与数据同沿注册（§4.1） |
| `post_wr_o` | output |  [17:0] | posedge | 写门与数据同沿注册（§4.1） |

## I3：ADC2

*宏握手；valid&ready 沿接收整字*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `adc2_valid_i` | input |  1 | posedge | 宏握手；valid&ready 沿接收整字 |
| `adc2_ready_o` | output |  1 | posedge | 宏握手；valid&ready 沿接收整字 |
| `adc2_raw_i` | input |  [12:0] | posedge | 宏握手；valid&ready 沿接收整字 |
| `adc2_range_err_i` | input |  1 | posedge | 宏握手；valid&ready 沿接收整字 |
| `adc2_capture_arm_o` | output |  1 | posedge | 宏握手；valid&ready 沿接收整字 |

## 节点模型快照出口

*仿真边界接口，**不进生产综合***

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `node_post_o` | output |  [159:0] | posedge | 仿真边界接口，**不进生产综合** |
| `node_pre_o` | output |  [31:0] | posedge | 仿真边界接口，**不进生产综合** |
| `node_ids_o` | output |  [39:0] | posedge | 仿真边界接口，**不进生产综合** |
| `node_qid_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `node_tag_o` | output |  [31:0] | posedge | 仿真边界接口，**不进生产综合** |
| `node_valid_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `node_h_o` | output |  [2:0] | posedge | 仿真边界接口，**不进生产综合** |
| `node_v_o` | output |  [2:0] | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_rsp_valid_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_rsp_tag_o` | output |  [31:0] | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_rsp_qid_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_rsp_raw_o` | output |  [12:0] | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_rsp_range_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_late_err_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |
| `adc2_release_ok_o` | output |  1 | posedge | 仿真边界接口，**不进生产综合** |

## D5 配置与输出

*配置为静态信号；输出 valid&ready 退休*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `w_main_tbl` | input |  [39:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `w_sub_tbl` | input |  [39:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `cfg_epoch` | input |  [7:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `b_q24_cfg` | input | signed [26:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `out_valid` | output |  1 | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `out_code` | output |  [19:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `out_sat` | output |  1 | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `out_err` | output |  1 | posedge | 配置为静态信号；输出 valid&ready 退休 |
| `out_tag` | output |  [31:0] | posedge | 配置为静态信号；输出 valid&ready 退休 |

## 观测

*调试用，不影响功能*

| 端口 | 方向 | 位宽 | 采样沿 | 说明 |
|:---|:---|:---|:---|:---|
| `apply_fire_o` | output |  1 | posedge | 调试用，不影响功能 |
| `k_o` | output |  [9:0] | posedge | 调试用，不影响功能 |
| `k_bad_o` | output |  1 | posedge | 调试用，不影响功能 |
| `final_applied_o` | output |  1 | posedge | 调试用，不影响功能 |
| `final_k_o` | output |  [9:0] | posedge | 调试用，不影响功能 |
| `err_o` | output |  1 | posedge | 调试用，不影响功能 |
| `done_o` | output |  1 | posedge | 调试用，不影响功能 |
| `tag_o` | output |  [31:0] | posedge | 调试用，不影响功能 |
| `qid_o` | output |  1 | posedge | 调试用，不影响功能 |
| `post_slot_o` | output |  [159:0] | posedge | 调试用，不影响功能 |
