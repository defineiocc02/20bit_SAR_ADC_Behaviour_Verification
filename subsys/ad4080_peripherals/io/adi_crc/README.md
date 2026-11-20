# AD4080 FIFO 抓包与 CRC24 离线复核

在已核的 ADI 公开驱动、指南与本机相关资料中，取得了 SPI 的读取长度和 LVDS 的尾字段位置，尚未取得可重算的商业芯片完整 FIFO CRC 位流或 CRC24 参数。当前器件绑定状态为 **UNBOUND**。已核源码、版本以及本机检索范围见 [SOURCES.md](SOURCES.md)。

## 快速运行

从交付根目录执行。工具用 Python 3.9 及以上版本的标准库，只读用户指定的本地输入，JSON 结果写到 stdout。

```bash
python3 -B io/adi_crc/fifo_crc_probe.py io/adi_crc/synthetic_capture.json \
  --hypotheses io/adi_crc/hypotheses.example.json
python3 -B -m unittest discover -s io/adi_crc -p 'test_*.py' -v
```

示例用独立 GF(2) 长除法合成五条训练帧和三条留出帧，另附 SPI1/4 格式样本。预期报告 `silicon_binding: synthetic_only` 及 `fit.status: ambiguous`。三个模型能预测完全相同的线 CRC，包含固定头或配对反射的模型也会存活。显式错误多项式被训练集拒绝。此例的 `0x6812AD`、`0x34C917`、`0xA8CA6D` 只用于验证求解器。

第一个命令省略 `--hypotheses` 即仅解析。应先核对 `watermark`、`samples_raw20`、`crc_wire_bits`、SPI 超出参考时钟数的尾位及哈希，再进行模型检验。输入错误返回 2。可解析但无法唯一辨识属于正常审计报告。

## 抓包清单

顶层 `format` 为 `ad4080-fifo-capture-v1`。`source` 记录 `kind`、`device`、`reference` 和 `session`。`kind` 可取 `synthetic`、`silicon_capture` 或 `adi_reference`，`device` 为 `AD4080`。真实采集的 `reference` 指向保留的原始文件或归档索引，原厂参考取文档页、正式答复链接或可追溯版本。可以追加板卡、硅片丝印、寄存器值及示波器或逻辑分析仪信息。工具校验声明和文件 SHA-256，来源的真实性由实物或原厂确认。

每条记录提供唯一 `id`、`mode`（`lvds`、`spi1`、`spi4`）、`split`（`train`、`holdout`、`format`）、`watermark`（1 至 16,384）和 `capture` 对象。同一清单的 CRC 拟合应来自同一器件和同一种已确认的 FIFO 工作配置。三种 readout 可放在同一采集会话内，求解仅使用 LVDS 的 `train` 和 `holdout`。

### 已解码记录

```json
{
  "id": "example-lvds",
  "mode": "lvds",
  "split": "holdout",
  "watermark": 2,
  "capture": {
    "encoding": "decoded",
    "header": "0xAA",
    "samples": ["0x135A9", "0xE62B4"],
    "crc_wire": "0xA5176C"
  }
}
```

上段的尾是展示格式用的任意位型。`crc_wire` 是从线上先发位到后发位拼出的 24 位显示值，首发位放在最高位，独立于厂商对 CRC 寄存器的位编号。只含转换结果的 ACE/IIO 导出无法补回头和尾。SPI 的 `decoded.samples` 提供实际收到的 24 位字，保留高四个符号位供检查。

### 原始位与采样沿

| 模式及编码 | `capture` 字段 | 校验或提取方法 |
|---|---|---|
| LVDS `wire_bits` / SPI1 `wire_bits` | `bits` 为先到后到的 `0` 或 `1` 字符，允许空白 | LVDS 整条 `8+20N+24` 位，SPI1 至少 `8+24N` 位且头为 `0xAA` |
| SPI4 `abcd_beats` | `beats` 为十六进制字符，每字符的 bit3 至 bit0 接物理 `SDOA/B/C/D` | 每线先发 `0xAA`，然后 `6N` 个 DCLK 的符号扩展样本。例 `F0F0F0F0 08CA59 F764D2` 解出 `135A9 E62B4` |
| SPI `spi_events_csv` | `path`，可选 `sha256` 和从零计数的 `transaction` | CSV 列为 `time_ns,dcs,dclk,sdob` 或 `time_ns,dcs,dclk,sdoa,sdob,sdoc,sdod`，每行是完整数字状态。DCS 定界，Mode 3 上升沿采位 |
| LVDS `lvds_edges_csv` | `path`，可选 `sha256`、数据行半开区间 `rows` | CSV 为 `time_ns,dco_edge,da`，`dco_edge` 交替 `fall`/`rise`，`da` 为该边沿对应稳定数据。行区间从第一条数据行起按零计数 |

CSV 路径相对于清单解析。时间严格递增。SPI 转换 `DCS` 时需要 `DCLK` 空闲高，且 `DCS` 与 `DCLK` 边沿在导出时间上可分辨。SPI 数据在采样上升沿恰好变化的事件应重查原记录或用已在稳定窗口采出的 `wire_bits`/`abcd_beats`。LVDS 从差分对确定逻辑极性，导出时关联同一边沿的稳定值。原始模拟波形应先用仪器或经确认的导出流程转换成事件/边沿表。

LVDS 默认对选中窗口检查精确位数。`scan_header: true` 可在一个已限定的窗口中搜索完整 `0xAA` 候选，只有一个候选才采用，并报告裁去的前后位数。头是交替码，任意整段波形可能有多个同值片段，此时使用触发位置和明确行窗口。图 87/88 周期以外的 SPI 位在 `spi_extra_wire` 中按线序保留，`spi_extra_clocks` 给出额外拍数，这些位的含义由抓包和实际电平决定。

## 假设文件与结果解释

第二个 JSON 使用 `format: "ad4080-crc24-hypotheses-v1"` 和 `profiles` 数组。每个假设都明确指定下表项目，程序没有预置的 ADI CRC 参数。

| 字段 | 取值和算术定义 |
|---|---|
| `name`、`poly` | 唯一名称。`poly` 是省略首项 `x^24` 的候选列表，常数项为 1。`"derive"` 时从同长度训练帧的差分求候选 |
| `coverage` | `payload` 覆盖全部样本，`header_payload` 加固定头，`last_sample` 只覆盖最后一个样本 |
| `packing` | `raw20` 按 20 位紧密拼；`zero_extend24` 或 `sign_extend24` 逐样本扩展；`pad_burst8` 将紧密拼接的总载荷尾部补零到字节边界 |
| `refin` | `true` 时对已选字段拼接后的每组八位反序，最后不足八位时仅反序实际位 |
| `augment_bits` | `0` 或 `24`，在上述输入位序完成后额外推进这么多个零位 |
| `init`、`xorout` | 24 位显式数值或 `null`。未知项进入 GF(2) 求解 |
| `refout` | `true` 时对 LFSR 的 24 位最终余数整体反序，然后异或 `xorout` |
| `wire_order` | 从数值到线尾的排列。`msb` 为整体高位先发，`lsb` 为 24 位整体反序，`byte_reverse` 为三个字节逆序，`lsb_per_byte` 为每个字节内反序 |

LFSR 在正向表示中逐位左移，输入位和寄存器最高位异或作为反馈，反馈为 1 时异或 `poly`。对于同长度的两条记录，初值推进和 `xorout` 可以用异或消去。按假设还原线尾和反射后，`(消息差 << 24) XOR 尾值差` 必须能被候选生成多项式整除。多个差值的 GF(2) GCD 给出约束。去掉无关的 `x` 因子后，最多枚举与 24 次多项式相除的 12 次低次商。若约束仍太宽，报告 `candidate_space_incomplete`，可收集更多不同的同长度消息，或输入有限多项式清单。

已定多项式时，用训练帧的线性方程求 `init` 和 `xorout` 的解及其秩。`free_degrees` 非零时，输出值是自由变量置零得到的代表解。留出记录如果与训练冲突会被拒绝。若留出曾被用于增加秩，则记为 `holdout_constrained`，需要新的留出帧来独立验证。完整相同的训练消息重传仅验证重复性，`holdout_repeats_training` 记录其身份。同一载荷两次不同的尾触发 `capture_conflict`。

`unique_within_declared_hypotheses` 只描述已列假设。对于固定的 `0xAA`，把头计入前缀与调整进入载荷时的 `init` 可以严格等价。即使换不同长度，也不能单独解决这个覆盖差异。余数反射和线上全反序也可能等价。只有原厂给定独立参数，或确有能改变相应条件的实物/原厂记录，才能解释这些区别。工具的来源声明和拟合结果独立记录，任何 `synthetic` 输入均输出 `synthetic_only`。

## 最少首轮采集工况

1. **单线 LVDS FIFO，Echo clock，正常原始转换。** 用即时触发设 `N=1,2,3`，保留 `0x15/0x16/0x17/0x1C–0x1E` 实际寄存器、采样条件和器件/板卡身份。保存 `CNV`、`FIFO_FULL`、`FIFO_READ_DONE`、差分 `CLK`、`DCO`、`DA`。对齐可以先用 `0xAC5D6` 单独验证，正式抓取使用正常转换并保留完整头、`20N` 位样本和最后 24 位。`READ_DONE` 可早于线尾末位。[Rev. B 图 89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=60)
2. **建议首轮训练五条、留出三条。** 训练三条 `N=1` 不同样本、两条 `N=2` 不同序列。留出 `N=1,2,3` 各一条全新序列。覆盖正负及不同首、末位，含一个首末变化的多字记录。留出应事先分配。这个数量用于第一次检查 GCD 和方程秩，实际所需由未决多项式、候选覆盖和秩决定。若需要区分固定头覆盖、初始化和输出反射的等价模型，需配合原厂确认；固定头上的更多记录只能增加某些约束。
3. **单线及四线 SPI 分开采集。** 同时抓 `DCS/DCLK/SDOA..D`，至少各一条 `N=1` 和 `N=2` 的完整图示长度。若要判断图示读长之后的输出，保存另一条受控延长时钟的完整原始记录，保留 `DCS` 和实际物理电平；逻辑分析仪布尔值无法独立判定高阻。将 SPI 额外位作为候选观测单列，等待原厂或实物证据确认其定义。[Rev. B 图 87/88](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59)

## 回归覆盖

独立合成测试覆盖 5 条训练与 3 条留出的参数闭合、错多项式、留出翻位、重复消息冲突、同长度训练导致秩不足、留出增秩、固定头与线序等价、反射加增广、20/24 位符号检查、单线和四线 SPI 与单线 LVDS 的 CSV/线位格式及 SHA-256 错误。算术用标准校验串和独立 GF(2) 长除交叉核对。可执行测试见 [test_probe.py](test_probe.py)。
