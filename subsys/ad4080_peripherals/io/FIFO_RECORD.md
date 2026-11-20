# FIFO 结果记录合同

## 原件与适用配置

Rev. B 图 87、88 和 89 的同步头均为 `0xAA`。图 87/88 分别给出单、四线 SPI `8+24N`、`8+6N` 个 DCLK。图 88 每根 SDO 都看到完整 8 周期的 `0xAA`。图 89 在 8 位头、`20N` 位载荷之后另画一个 24 位 CRC。第 51 页的 CRC 总述使用 FIFO 结果记录口径。其尾字段的算法与覆盖范围并未公开到可据以生成硅片期望值的程度。[结果 CRC 和符号扩展，第 51–52 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=51) · [FIFO 读波形，图 87–89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=59)

ADI 固件在数据 SPI 读取中对单线请求 `3N+1` 字节，对四线请求 `3N+4` 字节，在此次传输后改变片选，并从偏移 1/4 逐样本按 BE24 解析。no-OS 底层读取接口依调用方参数搬运字节。ADI 的 AXI AD408X 资料和 PHY/顶层 RTL 描述 LVDS 接收、对齐和采样链路，其中未找到 FIFO CRC24 算法或覆盖域。默认 SPI 组帧依该主机读取前缀结束，硅片在增加 DCLK 时还会送出什么需补实物或厂商记录。[ADI 固件读取函数](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L1227-L1261) · [ADI 固件 BE24 解析](https://github.com/analogdevicesinc/precision-converters-firmware/blob/6835d5c30d25d37499de5f210256828193d5c78e/projects/ad4080_iio/app/ad4080_iio.c#L1418-L1466) · [ADI no-OS 读取源](https://github.com/analogdevicesinc/no-OS/blob/68f9869649621441de4a1949caeef0be3f3e2692/drivers/adc/ad4080/ad4080.c#L103-L112) · [ADI AXI AD408X 文档](https://analogdevicesinc.github.io/hdl/library/axi_ad408x/index.html)

## 模式、线序与返回字数

| `mode_i` | 每 beat 出口 | 头与载荷 | 终点 |
| --- | --- | --- | --- |
| `0` 单 LVDS | `[1]` 首发下降沿，`[0]` 随后的上升沿 | 四个 `2'b10` 同步头 beat。每字照原始 transport 发 10 个 pair，从 D19 下降/D18 上升到 D1 下降/D0 上升 | 每条记录一个外部 CRC24，12 个 pair，合计 `4+10N+12` 个有效 pair |
| `1` 单 SPI | `[0]` 接 SDOB，下降沿移出 | 8 个 DCLK 同步头。每字由 `D19` 复制四次再接 D19…D0，24 个 DCLK | 默认 `8+24N` clocks，末位后主机可结束 DCS。明确绑定 SPI CRC 尾时加 24 个 clocks |
| `2` 四 SPI | `[3:0]` 对应 SDOA、SDOB、SDOC、SDOD，下降沿同时移出 | 每 lane 8 个 DCLK 同发同步头。先四个符号位，然后第 1 个数据 nibble 在物理 A/B/C/D 上为 `D16,D17,D18,D19`，最后为 `D0,D1,D2,D3`。每字 6 个 DCLK | 默认 `8+6N` clocks，随后主机可结束 DCS。明确绑定 SPI CRC 尾时加 6 个 clocks |

`N` 在记录接纳时锁定，范围为 1 至 16,384。即时触发、读最新记录时，采集控制器用约定的 WATERMARK；读整个结果存储时用 16,384，早触发导致的先前样本定义归上游记录管理。结果 SPI 的 DCS/DCLK 驱动与时序在 PHY 或主机。ADI 答复指出结果 SPI 采用 Mode 3，头的首位随第一下降 DCLK 发出，DCS 下降仅选择器件。数字 framer 的 `beat_last_o` 与 `done_o` 给 DCS 拥有者精确的结束标志。[Rev. B 表 27 与图 87–89](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=57) · [ADI 结果 SPI 实测答复](https://ez.analog.com/data_converters/precision_adcs/f/q-a/603310/fifo-full-signal/597558)

## 真实装配

```text
SRAM 读回 → 已有跨域传输及 DDR 边沿排列 → pair、first、last ┐
已在 IO 域准备的 SPI 20 位样本 → sample_valid/ready/last ─┤
记录命令与长度 → start_valid/ready/accept ───────────────┤
CRC 服务 ← 会话启动及已交付头/载荷 → crc_valid/ready/i ──┤
                                                        └→ 同步头 / N 字 / 已绑定尾 → 目标 PHY
```

所有端口由 IO 域使用。`start_ready_o` 允许消费一次命令，只有 `start_accept_o` 才启动源读取和 CRC 会话。模式 3、空长度、超过 16,384 或未绑定的 profile 均拒绝。`format_bound_i` 来自产品约定。选用单 LVDS 或 `SPI_CRC_TAIL_BOUND=1` 时，`crc_service_ready_i` 必须为 1 且空闲中无旧 `crc_valid_i`。默认 SPI 由 `SPI_CRC_TAIL_BOUND=0` 选择 ADI 固件结束的前缀，启动无须 CRC 适配器。可以在专用产品配置启用 SPI 外部尾，由该产品的板级或厂商证据签署物理线序和时钟数。

CRC 服务在 `start_accept_o` 同沿建立新会话，获取模式、长度和配置。如以实际输出为校验源，可以观察 `beat_valid_o && beat_ready_i` 且 `beat_kind_o` 为头或载荷的交付 beat，再由已绑定的算法规则选择覆盖项。结果在 `crc_valid_i && crc_ready_o` 同沿捕获。CRC24 已按物理传输的时间顺序打包，首发位在 `crc_i[23]`，四 SPI 情况 `crc_i[23:20]` 就是第一拍物理 A/B/C/D。组帧器保持这种顺序。固化 `CRC poly/init/xor/reflection/coverage` 前须获得供应方或实测契约。`crc_error_i` 与响应同拍有效并废弃记录。默认 SPI 没有 CRC 握手。测试中 `A5C39E` 和复位后的 `693FA1` 是外部响应字面量，用于验证时序、位传输和旧尾隔离。

### 状态与故障

1. 空闲接受命令后进入同步头。头末 beat 同沿给现有 transport 首字准入，随后接 `payload_pair_*`。任一 LVDS 字启动后要接满十个 pair，字末允许同沿装后续字，已到第 N 字则关闭新字准入。SPI 预取一字，旧字最后 beat 同沿可接受后续字，其 `sample_last_i` 必须和剩余计数吻合。
2. 需要 CRC 时，最后 payload beat 可同沿接受完整尾。若服务延迟则去 `WAIT_CRC`，期间不会发有效 beat。SPI 前缀在末样本末 beat 完成。尾最后 beat 或 SPI 前缀末 beat 只有与 `beat_ready_i` 握手才退役。`beat_first_o` 是记录首头，`beat_last_o` 是该 profile 唯一的成功结束标记。
3. 可背压头、SPI 载荷与尾。现有单 LVDS transport 启动后连续输出一字，`payload_start_allow_o` 仅禁止新字，已在途 pair 遇到下游拒收或中途缺拍会废弃本记录。字间可等待尚未到的源字。固定不中断的 Pad 波形要求上游按确定时钟与缓冲预算越过这些等候条件，并使 CRC 及时可用。整字上游断供由会话控制器超时后 `abort_i`，本模块没有猜测或填零补帧。
4. 掉电、复位与显式中止先切断有效输出、样本和启动握手。运行时 `power_good_i` 的撤销要保持到 IO 域有一沿可清状态，或由常开管理域直接置 `rst_ni`。故障中断不发 `done_o`，需要 CRC 的会话收到 `crc_cancel_o` 并清除响应。读回 owner 须同步作废对应 transport epoch 和已读暂存。`crc_service_ready_i` 重新置位前确认上一会话已排清。

| `error_code_o` | 条件 |
| --- | --- |
| 1 | 保留模式，或样本数不在合法域 |
| 2 | 产品格式未绑定、所需 CRC 服务不就绪、残留 CRC 响应或 LVDS 上轮 pair 仍有效 |
| 3 | SPI 源 `last` 与固定字数冲突，或 LVDS pair 标记、字内连续性与超量出线不符合合同 |
| 4 | CRC 服务有效响应同时报告错误 |
| 5 | 会话内掉电，IO 有时钟记账 |
| 6 | 主机/上游中止，或在发 LVDS pair 遭到不允许的反压 |

实际目标 RX/TX、ODDR、DCS/DCLK 包脚时序及局部供电复位根据 [IO_PLAN.md](IO_PLAN.md) 的冻结条件走。连续 ADC raw lane 的已有映射继续见 [SPEC.md](SPEC.md)。
