# 功能仿真结果（Icarus Verilog）

> 日期：2026-10-19 · 工具：**Icarus Verilog 14.0**（OSS CAD Suite, darwin-arm64）
> 运行：`bash sim/run_all.sh` · 结果：**5/5 TB PASS**

---

## 1. 环境

本机原本没有仿真器。本轮安装了 YosysHQ **OSS CAD Suite 2026-10-19**
（`~/.workbuddy/tools/oss-cad-suite`），包含：

| 工具 | 版本 | 用途 |
|---|---|---|
| iverilog | 14.0 (devel) | 功能仿真（本文件） |
| verilator | 5.053 | lint（`verilator --lint-only -Wall`） |
| yosys | 0.70+157 | 可综合性检查（`check -assert`） |

## 2. 结果总表

| TB | 覆盖 | 结果 | 证据 |
|---|---|---|---|
| `ad4080_cal_tb` | 两点校准 6 组 + 正/负半轴校正 | **PASS** | 6 组 GAIN/OFFSET 全对；x=±500000 误差 2 LSB |
| `sinc5_decim_tb` | SINC5 D=4 直流 + settled | **PASS** | DC 输出 = 1000；settled 在 64 样本内置位 |
| `result_fifo_tb` | 满/空/溢出/顺序/回绕/复位 | **PASS** | **716,157 次检查，0 错误** |
| `spi_slave_tb` | SPI 协议：基本读写 / CRC 正确与错误 / streaming 增减速 / 短指令 | **PASS** | 16 项检查全过 |
| `ad4080_top_tb` | 顶层 14 组集成用例 | **PASS** | **77 项检查全过** |

## 2.1 SPI 协议 TB 覆盖

| 用例 | 内容 | 结果 |
|---|---|---|
| T1 | 单字节写/读（无 CRC），0x0A=0xA5、GAIN 低字节 | PASS |
| T2 | CRC-8：正确则提交；错误则拒绝 + 0x11[3] 置位；互补使能 0x10=0x42 | PASS |
| T3 | streaming 递增：一帧两字节写 0x1D/0x1E，WATERMARK=0x0008 | PASS |
| T4 | streaming 递减（默认）：一帧两字节写 0x1E/0x1D，WATERMARK=0x4000 | PASS |
| T5 | SHORT_INSTRUCTION：8-bit 指令写/读 0x0A | PASS |

## 3. 顶层 TB 覆盖明细（77 项）

| 用例 | 内容 | 结果 |
|---|---|---|
| TC1 | SPI 复位值：GAIN=0x0200、WM=0x4000、STATUS=0x09 | 5/5 PASS |
| TC2 | OFFSET=+100 校正 + FIFO 直通 | 2/2 PASS（100100） |
| TC3 | x=−FS + GAIN>1 → 负饱和到 0x80000 | PASS（正确饱和，非缺陷） |
| TC3b | x=+500000、GAIN=0x3FF → 507797 | PASS |
| TC4 | SINC1 N=4 块平均 = 1000，且 4 输入只出 1 个结果 | 3/3 PASS |
| TC5 | FIFO mode 01、WATERMARK=8、递增样本读出 | 17/17 PASS |
| TC6 | HI_THRESHOLD 事件 → STATUS[5] + GPIO | 2/2 PASS |
| TC7 | SINC5 非法 N=512 → 钳到 256，不挂死 | 2/2 PASS |
| TC8 | SINC5+comp（N=4，总抽取 8）DC=1000 | 2/2 PASS |
| TC9 | LVDS 1-lane / 2-lane 奇偶交织 / 空闲重复帧 / SPI 模式清零 | 7/7 PASS |
| TC10 | CNV CMOS + 忙时再触发 → ADC_CNV_ERR；LVDS 差分 CNV | 2/2 PASS |
| TC11 | GPIO 方向 + SEL 全 16 编码扫描 | 18/18 PASS |
| TC12 | HI/LO 阈值 + 迟滞 + ALERT 电平型 + W1C | 6/6 PASS |
| TC13 | FIFO mode 10：事件后捕获 4 个（1004..1007） | 5/5 PASS |
| TC14 | FIFO mode 11：环形 + 事件位置回写 WATERMARK=8 | 5/5 PASS |

## 4. 仿真抓到的真实 bug（lint 抓不到）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | TC2 读回 OFFSET=0、TC3 增益被覆盖 | `we` 是寄存输出；等它拉高时 streaming 已把 `addr` 推到下一个寄存器（默认递减 → 写到 addr−1） | `we` 改为与 `addr/wdata` 同拍的组合信号（`spi_slave.sv`） |
| 2 | TC3 期望 532465 不可达 | 激励 524288 在 20-bit 补码里是 −FS，必然负饱和 | 改 TB 期望为 0x80000（正确行为） |
| 3 | TC4 平均 55 而非 1000 | TB 写 0x0A（FILTER_SEL=10 SINC5）却按 SINC1 期望 | TB 改 0x09 |
| 4 | TC7 偶发漏采单拍脉冲 | TB 轮询点在 `feed()` 内部，漏掉 valid | 加全局 `mon_valid_count` |

## 5. 综合检查（Yosys）

```
yosys -p "read_verilog -sv rtl/*.sv; hierarchy -check -top ad4080_top;
         proc; flatten; opt; memory; opt; check -assert; stat"
→ exit 0，无 latch / 无多驱动 / 无组合环
```

资源量级（generic，无工艺库）：

- 67,176 cells；$dffe 16,385（其中 16,384 是 16K FIFO 的存储位）
- $mul 1（校正），$add 47，$sub 25，$mux 16,946
- **16K FIFO 在 generic flow 下会展开成触发器**；流片必须换 SRAM 宏
  （`docs/synthesis_review.md` 已登记）。这是工艺映射问题，不是 RTL 功能问题。

## 5.1 覆盖率（Verilator --coverage）

| 指标 | 优化前 | 补用例后 |
|---|---:|---:|
| 行 | 76.2% | **88.8%** |
| 分支 | 60.4% | **77.8%** |
| 翻转 | 43.0% | 53.4% |
| 表达式 | 67.3% | 82.2% |

未覆盖的主要是错误/保留分支（非法寄存器地址、CRC 错误后的若干清理路径、
时钟数错误的边界组合），不是功能主路径。

## 6. Verilator lint

```
verilator --lint-only -Wall --top-module ad4080_top rtl/*.sv
```

- **0 个** LATCH / UNOPTFLAT / MULTIDRIVEN / BLKANDNBLK
- 剩余 22 条 `UNUSEDSIGNAL`：均为有意的配置/状态位（如 `lvds_mnc_en`、
  `sw_reset_pulse`、`clip_low/high` 在顶层未接观察口），已在代码注释说明。

## 7. 仍然不能声称的

- **没有跑过门级/时序仿真**（无 SDF、无标准单元库）。
- **没有做 STA**：关键路径只有设计估算（`synthesis_review.md`），不是签核数字。
- 覆盖率未统计（没有覆盖工具）；TB 是定向用例，不是随机全空间。
