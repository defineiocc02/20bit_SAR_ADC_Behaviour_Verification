# AD4080 数字 RTL 完成度盘点

> 日期：2026-10-19（仿真器接入后重新评估）
> 口径：按“手册功能覆盖 / 验证深度 / 签核”三条轴分别打分，**不合并成一个好看的数字**。

---

## 1. 总览

| 轴 | 完成度 | 说明 |
|---|---:|---|
| **手册数字功能（RTL 行为）** | **95%** | 除 6 项明确登记的 P2/待确认外，手册数字特性全部实现 |
| **验证深度** | **90%** | 5 个 TB（顶部 77 项）+ 覆盖率 88.8%/77.8% + ECP5 P&R + 形式 BMC；无门级、无 ASIC STA |
| **物理签核** | **35%** | 已有 ECP5 真实 P&R：73.59 MHz、6894 LUT、20 BRAM；仍无 PDK/ASIC STA |
| **文档与可追溯** | **100%** | 规范、三轮审查、逐行对照、仿真报告齐全 |

一句话：**设计基本做完，验证做到“功能自检全过”，离签核还差工艺库和 STA。**

---

## 2. 手册功能覆盖（逐项）

| 功能块 | 覆盖 | 未闭合项 |
|---|---:|---|
| 系统误差校正 y=m·x+c | 100% | — |
| 两点校准 | 100% | 测试点固定为编译期常数（10%/90% FS），运行时配置需另加除法器 |
| SINC1（D=2..1024） | 100% | — |
| SINC5（D=2..256） | 100% | — |
| SINC5+comp（N×2） | 90% | 补偿系数手册未给，C0/C1/C2 为参数占位 |
| 16K FIFO | 95% | +3 CNV 启动延迟未建模；允许同拍读写（偏离单口 SRAM） |
| FIFO 四模式 | 95% | mode 11 的 WATERMARK 取整（非报错）；事件位置回写已实现 |
| 事件检测/迟滞/ALERT | 95% | 2 样本延迟已加；未做模拟侧建立误差 |
| GPIO（4-bit SEL） | 100% | — |
| 寄存器 0x00–0x29 | 95% | 0x0E/0x0F、STRICT partial-access 未实现 |
| SPI Mode3/streaming/短指令/CRC/SEND_STATUS | 95% | 多字节寄存器起始字节序、CRC 后续种子构成 [待确认] |
| CNV / tCONV 检测 | 100% | 代理指标（忙时 CNV），非模拟 tCONV 测量 |
| LVDS 1/2 lane 并串 | 90% | DDR PHY / 差分驱动 / Manchester 属电气层 |

加权：**95%**。

## 3. 验证深度

| 手段 | 状态 | 证据 |
|---|---|---|
| pyslang 语法 + elaboration | ✅ | 15 RTL 0 errors；5 条位宽警告有 WAIVER |
| 文件头/风格门禁 | ✅ | 15/15 |
| Icarus 功能仿真 | ✅ | **5/5 TB PASS**：cal、sinc5、fifo（716,157 checks）、spi（16 项）、top（33 项） |
| Verilator lint | ✅ | 无 latch / 多驱动 / 组合环 |
| Yosys 综合 check | ✅ | `check -assert` 通过 |
| Verilator 覆盖率 | ✅ | 行 88.8% / 分支 77.8% / 翻转 53.4%（top TB，77 项） |
| ECP5 综合 + P&R | ✅ | `synth_ecp5` + `nextpnr`：73.59 MHz，20 DP16KD，`check -assert` 通过 |
| 覆盖率闭合 | ✅ 大部分 | SINC5+comp / LVDS / CNV / GPIO / FIFO 10/11 已补；剩错误分支 |
| 门级/时序仿真 | ❌ | 无网表/SDF |
| ASIC STA | ❌ | 无工艺库 |
| 形式验证（BMC） | ✅ | SPI：写地址/读不写/15-bit 地址，depth 60 PASSED（5m51s）；FIFO：count≤DEPTH/满不写/空不读，depth 40 PASSED |

加权：**90%**。

## 4. 仿真抓到的 bug（本轮新增）

1. **SPI 写地址偏移**：`we` 寄存输出，streaming 已把 `addr` 推到下一个寄存器
   （默认递减 → 写到 addr−1）。所有 SPI 写被静默写错位置。
2. **FIFO 读口异步复位**导致无法推断 RAM，327,680 个触发器。
3. 三处工具兼容性问题（function output 参数、return、always_ff 内 for 循环），
   已改为 Icarus/Yosys 都接受的可综合写法。
4. TB 自身 3 处错误（补码激励、SINC1/SINC5 配置码、CRC 使能值 0x42）。

## 5. 要走到 100% 还差什么

| 优先级 | 事项 | 预估 |
|---|---|---|
| P0 | ASIC 综合 + STA + 面积/功耗（ECP5 已到 73.6 MHz） | 需要 PDK |
| P1 | Hogenauer 位宽裁剪，把 CIC 从 61-bit 收窄以过 100 MHz | 需数值/溢出预算，1-2 天 |
| P1 | 形式：补辅助不变式做无界归纳（SPI 帧结构一致性） | 1-2 天 |
| P1 | streaming 字节序 / CRC 种子位序用示波器或 ADI 确认 | 外部信息 |
| P2 | 0x0E/0x0F、STRICT partial-access | 1 天 |
| P2 | SINC5+comp 真实系数 | 需要 ADI 数据 |
| P2 | 门级仿真（SDF） | 需要网表 |
