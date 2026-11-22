# 外围装配检查点

- 已核对 `rtl/result_store.sv` 的 14 位地址、20 位载荷、四银行单口契约。供电、OTP、MBIST 和边沿映射的生产端接口已由各模块负责人确认。
- 装配分域为 AON 管理及 OTP、核心时钟域单口存储及 MBIST、独立 I/O 边沿字。AON 发送测试保持请求，等核心回传已保持。退出还等待读写权限恢复及 FIFO 元数据重建握手。
- OTP 启动等首次初始化。软件复位后的再次初始化保留已提交 shadow。OTP 宏的实际供电有效由独立 AON 输入给出。
- 已确认本机 Verilator 入口可执行，Yosys 0.69 可执行。产物仅新增于 `integration/`，验证构建走 `/tmp`；可通过移开新增目录独立回退。
- 装配 RTL、三域定向台架及复现脚本已交付。`evidence/regression.txt` 和 `evidence/sources.sha256` 对应 `/tmp/ad4080-peripherals.8yYun4` 的完整运行。严格 lint 通过，联合仿真十组通过，Yosys 结构检查报 0 个问题、四个存储单元。
- 追加单 lane 采后读回绑定 `ad4080_result_readback_link.sv` 和 `peripheral_readback_link_tb.sv`。四格容量预留、五字连续、模式拒绝、掉压冲刷和复电首字、BIST 静默及固定一拍错误均通过。最终运行 `/tmp/ad4080-readback-link.K7hjI7`，归档 `evidence/readback_link.txt`、`evidence/readback_sources.sha256`，哈希复核通过。新增 RTL 注释 75/90，台架 56/267。
- 首轮核对时，采后 FIFO 仿真覆盖 20 位载荷。后续整记录与工艺接入范围见下列复核回执。
- 独立复核已完成。旧 ZIP 的未完记录受理、BIST 未授权起跑及 held valid 进出四个探针均触发断言。新 `capture_incomplete_aon_i`、分离的 BIST grant 与公开 `por_ana_event_aon_o` 通过三域 13 场景。空跑 Yosys 的假绿由真实 JSON、正确顶层、存储尺寸及完成行门禁拒绝。
- 完整单 lane 记录叶子 `ad4080_fifo_lvds_record_link` 已用同一结果 SRAM 验收。格式或 CRC 未绑定拒绝、abort 退场、字数预算、两字全帧 `AA_95E2B_80001_81A4D4` 通过。试验余数算法和实际器件 CRC 的交接范围见 `REVIEW.md`。
- 稳定回执由 `/tmp/ad4080-peripherals.aj7WMP` 和 `/tmp/ad4080-readback-link.VNb3Tu` 的真实运行归档至 `evidence/`，两份 SHA-256 清单与现源码通过。
