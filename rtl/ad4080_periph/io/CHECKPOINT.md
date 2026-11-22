# DATA INTERFACE / CLK / Pad 检查点

## 2026-11-20 原件和工作边界

- 只读核验 AD4080 Rev. B 95 页 PDF 与 IPBrain 裸片图，SHA-256 和 `../floorplan/plan.json` 记录吻合。已读表 2、20–23、48–50、引脚表 6，逐图核对图 74–91。关键交叉核验见 `SPEC.md`。
- 新建 `io/`，无既存文件需覆盖。项目其他目录由各自负责人维护。本工作包可按整个 `io/` 独立归档或回退。
- 数字边界采用 20 位并行字到单/双 lane 边沿字的组合适配。时钟、复位、valid/tag、Pad/PHY 及帧调度的归属见 `microarchitecture.md`。不改动任何既有 ADC 或 DSP 数字功能源。
- 本机实际调用 Verilator 发行目录 `bin/verilator` 和 YoWASP Yosys，均可启动。商业 EDA 与真实 Pad/PHY 模型的运行状态保持 `NOT_RUN`。

## 2026-11-20 实现前合同

- `SPEC.md` 规定槽 0 最早，每槽下降沿在前，下一上升沿在后。双 lane 的 DA 发全部奇数位，DB 发全部偶数位。检查模式用 `20'hAC5D6`。高槽和闲置 DB 的逻辑字固定为零。
- `verification_plan.yaml` 和 `requirements_trace.csv` 登记样本、检查模式和无状态契约的验收方法。待实现、执行和归档结果。

## 2026-11-20 实现与验证完成

- `IO_PLAN.md` 和 `package_pins.csv` 记录照片区位、目标走廊、商用封装以及自研组序的不同证据层级。目标模式和 Pad pin freeze 嵌在交接表，签核输入仍待提供。
- `ad4080_ddr_edge_words.sv` 的 `-Wall` lint、Verilator 运行通过，1109 组字检查和双模固定码边沿字面值检查通过。
- Yosys WASM 带 ABC 的首轮执行在 ABC 退出前尚未走到脚本末尾。改用 `synth -noabc` 完成通用综合与独立 SAT 证明，日志包括 SAT SUCCESS 和 `End of script`。
- 复跑方法、状态和归档对照见 `README.md`、`handoff_manifest.json` 与 `evidence/`。交付目录内没有 ADI 手册拷贝或转写页。

## 2026-11-20 FIFO 记录审查与修正

- 审查已备份至任务专用目录，既有 DDR 边沿映射源码和 1109/SAT 证据继续保留。通过 Rev. B 图 87–89、ADI 固件和 no-OS 核对固定头、SPI 主机前缀及单 LVDS 尾字段，具体来源见 `FIFO_RECORD.md`。
- 新增 `ad4080_fifo_record_framer.sv` 和独立收线测试。组帧器使用原 transport 的单 LVDS 双沿 pair，四线结果 SPI 采用每线同头、独立符号扩展和物理位序。CRC 服务未绑定时 LVDS 拒绝命令。SPI 默认在官方驱动长度结束，扩展尾须专门绑定。
- Verilator 两种配置通过 98,509/227 条 beat 断言和各 15 条故障检查。默认四 SPI 已走完 16,384 字，另覆盖 SPI/头/CRC 背压、主机结束、缺字错位和掉电复位。两种通用 Yosys 检查均完整结束，独立向量和来源核查一并归档。

## 2026-11-20 连续原始时钟修正

- 同步头前预取 `min(N,2)`，起发后头、载荷和尾每拍持续，末载荷沿必须接受外部 CRC。缺 CRC、撤销 ready 或载荷断供均有确定的退出原因。当前运行为默认/扩展尾各 98,611/329 次检查，各 21 条故障。上一节是旧版允许等拍的历史回放。
- 真实 SRAM 到单 lane 记录通过 2.5 ns 自由运行 CLK 的两字 36 拍和单字 26 拍。完整的 112 个原始采样沿含两个失败用例的空拍，见 `integration/evidence/raw_record.csv`（从外围包目录算起）。首末装载事件来自 `frame_q`，与 CRC 退休分别验证。
- transport 重跑图 74/75 两组 512 帧，也重跑了冻结 ADI 收端的四组。CRC 仅使用检查夹具参数，真实器件 CRC24 和 pad 传播时序仍待绑定。
