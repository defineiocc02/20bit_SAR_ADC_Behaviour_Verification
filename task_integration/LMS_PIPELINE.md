# 整体顶层的片上重构流水

`sar20_digital_lms_top` 在同一数字顶层实现 18 片调度、DEM、八片正向扰动、片上冻结 LMS 系数推理和 CAAZ。实际 SADC／RDAC 开关、斩波、AZ、RA 和 ADC2 是外部宏；LMS 训练在片外。每笔 ADC2 原码与实际转换、采样位图在同一握手登记。重构使用物理位权 `main_conv` 与 `sub_conv−sub_sample`，由实际位图完成去扰和桥接，不另加名义扰动／桥补偿。 [重构数值合同](../task_reconstruction/SPEC.md)

用独立两个队列槽保存已交接样本的 ID、实际斩波极性和 AZ 代／确认。ADC2 握手后立即释放转换组；等待重构的队列和下一个采集组可以并行。后端只有队首 ID 与重构结果一致时才能进入 CAAZ。重构正常数据在 ADC2 握手后第 22 拍发布，下一沿 CAAZ 受理。外部 ADC2 仍须在其转换组 N+2 发布至 N+15 的窗口完成交接，才可维持每 16 拍一笔。正常配置引擎在 16 拍输入周期下连续工作，并重叠两笔未退休样本。管线繁忙或宏晚到时通过 ready 保留当前组。

`cfg_start_i` 在尚未调度的清空周期内开启配置，`cfg_valid_i && cfg_ready_o` 顺序装载 396 个 26 位字。配置完成才允许调度。调度开始后，`cfg_start_i` 被拒绝，重新装载前应清空整个周期。`clear_i` 和 `enable_i=0` 取消在途事务并保留完整系数。`rst_ni=0` 取消配置资格。`adc2_valid_i && adc2_ready_o` 时接受 `adc2_sample_id_i[31:0]`、`adc2_code_i[11:0]`、`adc2_bad_i`、`adc2_chop_neg_i`、`adc2_az_pair_id_i[30:0]` 和 `adc2_az_ok_i`。错 ID 和命令发布前的良好原码报 `adc2_error_o`。同 ID 坏样本可提前终止，进入有序错误路径并锁存故障。 `recon_inflight_o[1:0]` 是已交接、未退休的样本数。其余端口见 [通用顶层规格](SPEC.md)。

关闭 DEM 的对照同时令 `dem_enable_i=0, bridge_enable_i=0`，开启跨段 Fig.21 时两者均置 1。各模式用相符的训练权重。CAAZ 关时完整 20 位码直接输出，开时先逐笔重构再对偶／奇码相减并除二。配对元数据、残差和码用于数字仿真与版图交付；硅上性能另外依赖宏建立、时钟与标定。
