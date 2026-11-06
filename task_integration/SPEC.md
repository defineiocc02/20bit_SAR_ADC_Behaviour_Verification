# 20 位数字顶层接口 v1.0

顶层 `sar20_digital_top` 串接 18 片调度、DEM、八片 RDAC 扰动控制和 CAAZ 配对。采用 160 MHz 核心时钟和每 16 个上升沿一笔的输入节拍。CAAZ 关时输出 10 MS/s，开时合法连续的正、反相非重叠配对输出 5 MS/s。两个速率都以外部宏遵守下述时序为前提。

当前边界包括实际控制位、最终粗码映射和 20 位有符号结果的配对。SADC 辅助 DAC、参考开关、RA、ADC2、LMS 训练及按真实电荷重构的乘加由外部提供。数字宏输出逐笔采样/转换掩码、同笔扰动字和桥接补偿供外部重构使用；模拟斩波和自零的建立、噪声及电荷保持由模拟验证负责。八片扰动用原生子阵列低到高的正注入，CAAZ 必须收到在斩波坐标系下已去扰的重构结果。 [模块二](../task2/eight_rtl/SPEC.md) · [模块三](../task3/README.md)

所有端口同属 `clk_i` 域。总线按 slot 顺序排列，slot 0 位于最低位。`rst_ni` 是低有效同步复位；`clear_i` 和全局 `enable_i=0` 同样取消在途事务并复位样本号。外部宏须同时取消未完成的样本并清除有效信号，复位后 ID 从 0 重新分配。顶层仅按 32 位模序号确认所属样本。

| 端口 | 方向和位宽 | 约定 |
| :--- | :--- | :--- |
| `clk_i`, `rst_ni`, `clear_i`, `enable_i` | 入各 1 | 上升沿；复位、清空、禁用优先于受理。 |
| `advance_i`, `advance_ready_o` | 入／出各 1 | 两者同为 1 才向调度器申请新采集。资格不足由 `stall_o` 报告，上游重试。 |
| `eligible_i`, `shuffle_i`, `dem_enable_i`, `bridge_enable_i`, `dither_enable_i`, `caaz_enable_i` | 入 18/1/1/1/1/1 | 物理释放资格、逐笔调度和扰动控制。CAAZ 在同一清空周期内保持固定。 |
| `seed_load_i`, `slice_seed_i`, `dem_seed_i`, `dither_seed_i`, `seed_error_o` | 入 1/32/32/31，出 1 | 三个非零种子原子重载。重载与申请同沿冲突时两者均拒绝。 |
| `slot_accepted_o`, `stall_o` | 出各 1 | 调度成功和资格不足的一拍响应；模拟孔径以 `acq_valid_o` 为准。 |
| `acq_valid_o`, `acq_sample_id_o`, `acq_slice_ids_o` | 出 1/32/40 | 同一笔有序八片采样描述符，一拍有效。 |
| `acq_word_o`, `acq_lower_eighths_o`, `acq_sample_sub_high_o` | 出 6/5/40 | 扰动字、SADC 辅助 DAC 的低路分子、每片五位原生子阵列采样高参考掩码。 |
| `acq_chop_neg_o`, `acq_az_capture_o`, `acq_az_hold_o`, `acq_az_pair_id_o` | 出 1/1/1/31 | 同笔斩波与 AZ 捕获／保持命令。CAAZ 关时四字段为零。 |
| `conv_ready_o`, `conv_sample_id_o`, `conv_slice_ids_o` | 出 1/32/40 | 当前未退休的转换组，外部在有效期间读取。 |
| `conv_word_o`, `conv_total_eighths_o`, `conv_sample_sub_high_o` | 出 6/6/40 | 对应本笔采样快照。扰动分子的单位为原生一档的八分之一。 |
| `code_valid_i`, `code_sample_id_i`, `code_i` | 入 1/32/9 | 最终 SADC 粗码，采一拍申请。 |
| `mask_valid_o`, `code_error_o`, `command_code_o`, `main_mask_o`, `sub_mask_o`, `bridge_correction_q32_o` | 出 1/1/9/136/40/32 | 合法粗码一拍命令和 Fig.21 名义桥补偿；错 ID、重复、越界及未就绪请求报错。 |
| `recon_valid_i`, `recon_ready_o`, `recon_sample_id_i`, `recon_code_i` | 入 1、出 1、入 32/20s | 同笔外部重构结果，仅握手才退休；有效申请被拒绝时 `recon_error_o` 脉冲。输入码是斩波坐标下已去扰的有符号二补数。 |
| `recon_bad_i`, `recon_chop_neg_i`, `recon_az_pair_id_i`, `recon_az_ok_i` | 入 1/1/31/1 | 模拟坏样本、实际施加极性、AZ 代及宏捕获／保持确认。坏样本可不经粗码直接退休。 |
| `out_valid_o`, `out_code_o`, `out_sample_id_o`, `out_first_sample_id_o`, `out_az_pair_id_o`, `out_pair_o`, `out_difference_o` | 出 1/20s/32/32/31/1/21s | CAAZ 关时单笔，开时两笔的 `floor((even−odd)/2)`；21 位差码完整保留。 |
| `recon_error_o`, `fault_o`, `fault_pulse_o`, `caaz_fault_code_o`, `alignment_fault_o` | 出 1/1/1/3/1 | 错配或早到拒绝重构并保留当前资格。CAAZ 或采样控制 ID 对齐故障锁存，直到清空。 |

## 每笔的周期预算

| 上升沿 | 事件 |
| :--- | :--- |
| N | `advance_i && advance_ready_o`。 |
| N+1 | 调度接受或 stall。接受时原采集组晋升转换。 |
| N+2 | 顶层转换上下文有效；新采集组的 dither 描述符产生。首笔启动暂无转换。 |
| N+3 | 新采集组的 CAAZ 控制与 dither 描述符同 ID 发布。模拟孔径在此之后。旧组若已在沿前提交粗码，则同沿发最终 RDAC 掩码。 |
| N+4 | 上一组最早可退休，合法粗码已发一拍。 |
| N+15 | 若要每 16 拍接新请求，上一组最迟在本沿交付重构。自 N+2 算有 13 拍，最早命令 N+3 至 N+15 有 12 拍。 |
| N+16 | 下一笔请求。前一组未退休则反压，直到同 ID 重构或显式坏样本结束。 |

每次只有一个等待分配的请求和一个未退休的转换组。上游可让 `advance_i` 等待 `advance_ready_o`，重载须占单独的沿。`code_valid_i` 在前次粗码合法发布后重复提交时拒绝。正确 ID 的 `recon_bad_i=1` 先于同沿粗码和采样发布，顶层终止该笔并停止继续分配，CAAZ 发故障。有效输入被接收之前，外部须保留其总线。正常结果在接收沿后输出，CAAZ 开时仅奇相结果的接收沿后输出。

| 需求 | 验收 |
| :--- | :--- |
| REQ-TOP-01 | 每 16 拍请求连续 4096 笔；八片采集／转换组互斥且 ID 按序，模块二和 CAAZ 采样标签同笔。 |
| REQ-TOP-02 | 错 ID、超窗粗码、重复命令、无命令重构和晚到反压均有自检；清空消除在途事务。 |
| REQ-TOP-03 | 旁路逐笔结果、CAAZ 成对差码和负数舍入均核对；两次扰动独立、逐笔去扰后解调；错 AZ 和坏样本置故障。 |
| REQ-TOP-04 | 顶层和全部叶在同一个可复现的 SKY130HD 设计包中综合、布局布线、导出 GDS、报告抽取后 6.25 ns STA 和独立物理验证状态。 |
| REQ-TOP-05 | 片上冻结重构流水每 16 拍接受一笔 ADC2，逐码对拍两组固定失配数据，坏样本交接后冻结新采样。 [片上合同](LMS_PIPELINE.md) |

基线 `b592ba825fa2c156f58fab5b2f528d90e49cacff`，本机回滚引用 `refs/codex/backups/20b10mtask-before-eight-dither`。本合同冻结为当前学生交付的同步数字边界。真实模拟宏时序、CDC、180 nm BCD PVT、权重部署和签核另由目标集成验收。
