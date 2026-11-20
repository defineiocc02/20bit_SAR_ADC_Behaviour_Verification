# 外围装配复核与修复

复核对象为旧交付 ZIP，SHA-256 为 `23c4b80789b53c8d173bcdea1dad374814853ae48d67a0a05a55eb78e768d114`。从该包在 ASCII 临时目录展开实际 RTL，给旧台架加定向断言。修订及复测位于独立交付的 `integration/`。使用 Verilator 5.30 和 Yosys 0.69，功能组仓库 `main@7408edf` 作为只读接口依据。

## 逐条裁定

| 复核点 | 旧包观察与范围 | 修订及随包回归 |
| --- | --- | --- |
| 未完成采集 | 总线无当前 SRAM 请求且管理侧空闲为高时，旧壳接收待机。现有接口没有布防/部分记录资格。 | 新增 AON 记录资格 `capture_incomplete_aon_i`。同时请求待机、软复位和 BIST 均被挡住，新转换仍可继续完成当前记录。 |
| 完成记录、非法模式与唤醒 | 供电主管控制许可，原联合台架曾仅验证正常模式和软复位保持。 | 保留结果的 20 位字从 SRAM 回读。模式 `01` 拒绝。外供待机封新转换与 LVDS，保留已有 SRAM 读。陈旧或缺失 wake ack 均封新转换，新的完成握手放行。 |
| BIST 进入与撤回 | 定向 probe 在核心收到 ENTER 保持后提交 start，AON 尚未授予，旧 SRAM 内容已经被标作废。 | 核心功能保持与破坏性授权拆分。先回到 AON，再向核心同步释放 grant。撤回授权立即封测试访问，且回收仍需往返。并行软复位与模式请求在 BIST 获资格时不受理。 |
| BIST 原有未受理请求 | 原功能请求被 ready 门控后送 MBIST。旧包错误地在 raw valid 保持时给 `start_ready`，以及在旧请求仍在时解除作废。 | 静默判断直接包含上游 raw valid。测试进入和重建结束均验证该条件。原有已受理读能排尽。 |
| 软/硬复位、REFIN 与 IOVDD | 保留与禁用范围按真实 supervisor 输出分别核。 | 已完成结果和 OTP 影子隔离于软复位，再初始化期间第二次软复位拒绝。REFIN 掉落禁止新写并透传独立 `por_ana_event_aon_o`，保留旧读。IOVDD 掉落停读停送，既有载荷保持。硬 POR 优先于 OTP 校验通过。 |
| 固定一拍返回 | 读回适配器已按单笔预留约束返回。合法数据的身份仍依赖 SRAM 读口的时序与独占归属。 | 保留满态停发，加入无请求返回与新地址同沿的反例。缺失及无配对 valid 都锁存错误并禁止后续新读。对于在合理窗口误送的数据，源端仍应保证单口所有权和内容。 |
| 工具提前退出 | 对旧 runner 注入 `/usr/bin/true`，模拟 Yosys 空跑而成功退出，脚本照样打印结构 PASS。 | 两脚本核对完成标记、`check -assert` 完整性、生成 JSON 的指定顶层和存储尺寸。空跑两个入口均返回 1。空仿真日志和错顶层 JSON 各返回 1。 |
| 完整记录 | 原联合证据只到 20 位单 lane 载荷。 | 可综合记录叶子重用现有读回和组帧器。未绑定格式或 CRC 服务拒绝，记录中止冲刷旧字。两字预算拦第三读地址。实例使用同一块 SRAM 输出 `AA_95E2B_80001_81A4D4`，含头和尾共 36 个位对。 |

## 接入者与证据边界

- 记录控制/系统集成负责将真实 `capture_incomplete` 与已完成记录指针、转换停流和在途 I/O 对齐送到 AON。现有独立 DSP 捕获控制可以提供记录语义，实际接入需复用同一块结果 SRAM。台架中只有一套功能存储阵列。
- 核心域每个同拍 read valid/ready 受理一个地址。完整记录叶子固定单 lane，根据已提交记录字数限制读请求。CRC 服务与 IO 域 `record_start_accept_o`、`crc_ready_o`、`crc_cancel_o` 相配。正常结束、主动中止和掉电分别由记录控制器维护读指针。CDC 捆绑字数、硬件时钟及收据需要对应的实现约束。
- 完整记录黄金的 48 位前缀为 `0xAA95E2B80001`。将前缀左移 24 位与初值 `0xB704CE` 左移 48 位异或，再除以 GF(2) 多项式 `0x1864CFB`，独立余数为 `0x81A4D4`。这是确定的台架算法绑定，实际产品 CRC 多项式、初始化、位序与覆盖由设备/协议负责人交接。
- PHY/时钟团队负责实际 ODDR 装载、DCO/CNV 对齐、终端、电气与 STA。CSR 团队负责将 `por_ana_event_aon_o` 变成器件相应的粘滞诊断和 W1C。实际 OTP 镜像、宏、trim 分发与转换 IP/独立 DSP 的整片接入分别验收。

## 复跑与对照

```sh
YOSYS=yosys bash integration/run_verify.sh
YOSYS=yosys bash integration/run_linked.sh
YOSYS=/usr/bin/true bash integration/run_verify.sh
YOSYS=/usr/bin/true bash integration/run_linked.sh
shasum -a 256 -c integration/evidence/sources.sha256
shasum -a 256 -c integration/evidence/readback_sources.sha256
```

前两项预期退出 0。空跑综合两项预期在真实仿真之后退出 1。旧包反例回执在 [audit_red.log](evidence/audit_red.txt)，修订与入口验收回执在 [regression.log](evidence/regression.txt)、[readback_link.log](evidence/readback_link.txt) 和 [audit_gate.log](evidence/audit_gate.txt)。旧壳反例临时探针保留在本轮工作主机 `/tmp/ad4080-integration-baseline.KIx9Ac/integration/peripheral_services_tb.sv`，只替换测试台激励。封装、真实目标库及电气验收的结果仍由其原工艺交付确定。
