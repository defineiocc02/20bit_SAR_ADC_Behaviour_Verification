# AD4080 与 ADI FPGA 收端互操作

本包的结果 transport 与边沿字映射，经 DA/DB/DCO 串行位流，接入了冻结版本的原版 ADI `ad408x_phy`、`ad_serdes_in` 和 `ad_pack`。四组 Verilator 仿真在故意改变首帧相位后均找到非零位移，锁定 `0xAC5D6`，并按顺序恢复 14 个带符号样本。原发送映射保持原样。[实跑结果](evidence.txt) · [ADI HDL 固定提交](https://github.com/analogdevicesinc/hdl/commit/f518dd124fc005c6f9cd80b7947b10a638c37b37)

| 接收方式 | DCO 模拟周期 | 首帧启动等待 | ADI 锁定位移 | 锁定后固定码检查 | 正常样本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 单 lane | 2.5 ns | 8 个 pair 拍 | 7 位 | 109 | 14 |
| 单 lane | 2.5 ns | 9 个 pair 拍 | 5 位 | 111 | 14 |
| 双 lane | 5.0 ns | 8 个 pair 拍 | 2 位 | 114 | 14 |
| 双 lane | 5.0 ns | 9 个 pair 拍 | 18 位 | 99 | 14 |

每组发送 150 个连续无空拍字，含 128 个训练字、14 个样本和 8 个尾段缓冲字。`shift` 为本轮原版 ADI 逻辑锁定时的 `shift_cnt`，受分频起点和测试激励相位影响。锁定后对正常样本做 32 位二补码逐字比较，涵盖从 `0` 开始的递增字、`0x7FFFF`、`0x80000`、`0xFFFFF` 及若干非对称码。测试选择的 DCO 周期对应 40 MSPS 的标称两种 lane 预算，仿真结果限逻辑接线、固定码对齐及字序。

## 参考板与管脚方向

ADI 的 EVAL-AD4080-FMCZ 参考工程使用 ZedBoard 的 FMC-LPC，要求 ZedBoard 的 VADJ 为 2.5 V。官方项目将单 lane LVDS DDR 列为已测试并支持，双 lane 列为 HDL 支持、尚未板测。配置 SPI 由参考板使用。转换结果的 CMOS SPI、FIFO 记录或 Manchester 接收超出本参考工程的 LVDS 验证范围。[冻结项目说明](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/docs/projects/ad4080_fmc_evb/index.rst#L4-L31) · [ADI 发布的项目页](https://analogdevicesinc.github.io/hdl/projects/ad4080_fmc_evb/index.html)

| 信号对 | 固定来源里的 ZedBoard 对应 | 数据和时钟解释 |
| --- | --- | --- |
| `DA+/-` | FPGA P17/P18，FMC H7/H8 | 主 lane，进入 `data_a_in_p/n`。单线用 DA，双线时每沿 DA 提供这一组的高编号位 |
| `DB+/-` | FPGA N22/P22，FMC G9/G10 | 进入 `data_b_in_p/n`。双线时每沿 DB 提供这一组的低编号位 |
| `DCO+/-` | FPGA L18/L19，FMC H4/H5，XDC 默认 2.5 ns | 从 ADC 返回至 `dclk_in_p/n`，7 Series `IBUFGDS→BUFIO` 供 DDR 原语，同时 `BUFR÷2` 给解串/解包逻辑 |
| `CLK+/-` 同源板级参考 | FPGA `clk_p/n` 的 N19/N20 为监视入口 | 参考板 `system_top` 通过差分接收建立 `fpga_ref_clk`，BD 将它接进时钟监视。PHY 采样用 ADC 的 DCO |
| `CNV+/-` | `cnv_in_p/n` 按系统/BD 端口转入 PHY，所读 Zed XDC 未给专用球位 | 在此提交的 `ad408x_phy` 中声明后未参与帧对齐。测试把 CNV 保持静止，以便实际证明固定码位滑移路径 |

`sync_n` 连到参考板 `ad9508_sync`。在选定 PHY 中，它清除 DCO 的分频并定序释放解串复位。测试用理想的 DCO 起停满足该行为，发送 DA/DB 的下降沿位在前，再发上升沿位。连线相位是理想采样偏置，实际 ADC 的 CNV/CLK 相位以及 DCO 到数据的建立保持仍由芯片、封装和 PCB 联合验证。[冻结 ZedBoard 管脚表](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/projects/ad4080_fmc_evb/zed/system_constr.xdc#L35-L88) · [系统顶层](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/projects/ad4080_fmc_evb/zed/system_top.v#L173-L193) · [BD](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/projects/ad4080_fmc_evb/common/ad4080_fmc_evb_bd.tcl#L74-L108)

## 分帧、码制与软件侧接缝

原版 `ad_serdes_in` 每 lane 取四个相邻 DDR 边沿，ADI PHY 将双 lane 同一边沿按 DA、DB 交错。原版 `ad_pack` 在 `DCO/2` 域把每拍 4/8 位拼成 20 位。旧字和当前字组成跨字滑动窗。ADI 软件位滑移扫描候选偏移，直到匹配 `20'hAC5D6`，其结果按 `DEVICE_CODE=0` 作为 20 位二补码再扩成 32 位。样本的 `adc_valid` 由装包和过滤就绪决定。训练期的字可能有效出现，主机还需按 `SYNC_STATUS` 和采集状态选择对外发布。测试台使用 `sync_status` 才开始核对正常字。[冻结 ADI 接收源码](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/library/axi_ad408x/ad408x_phy.v) · [ADI 核说明](https://analogdevicesinc.github.io/hdl/library/axi_ad408x/index.html)

| 控制位置 | 与本测试的联系 |
| --- | --- |
| AD4080 `0x15[4] INTF_CHK_EN`、`[2] SPI_LVDS_LANES` | 先开固定训练码，传输模式 `0/1` 对应单/双 LVDS。这轮直接提交 RTL 的检查和 lane 控制，走实际 TX 映射 |
| ADI ADC Common `0x44[12:8] NUM_LANES` | 写实际 lane 数 `1` 或 `2`。本次 `NUM_LANES=2` 允许物理接两对，仿真逐次选择运行时值 |
| ADI ADC Common `0x44[3] ADC_SYNC`、`0x68[0] SYNC_STATUS` | 触发 PHY `bitslip_enable`，确认 `sync_status` 后再退出固定码。本轮在 PHY 控制端施加和检测相应脉冲 |
| ADI ADC Common `0x4C[3:2] DEVICE_CODE`、`[1] SELF_SYNC`、`[0] FILTER_ENABLE` | `DEVICE_CODE=0` 走 20 位和 `AC5D6`，接收 32 位符号扩展。参照 `axi_ad408x` 连线和本提交的 PHY 使用点。`self_sync` 在该 PHY 仅为声明端口，扫描实际由 `bitslip_enable` 驱动。测试旁路数字滤波 |

由 `<up_adc_common>` 字索引换算上述 FPGA 字节地址。ADI 的 `AXI` 和 `DMA` 顶层仅核对源码连线，动态验证直接使用其接到 PHY 的控制端，不运行 FPGA 工程综合、布线、Linux 驱动或真实板卡。[AD4080 配置表 48](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf#page=84) · [冻结 common 寄存器源](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/library/common/up_adc_common.v#L240-L280) · [AXI 包装](https://github.com/analogdevicesinc/hdl/blob/f518dd124fc005c6f9cd80b7947b10a638c37b37/library/axi_ad408x/axi_ad408x.v#L276-L312)

## 可复跑门禁

入口为 [run_verify.sh](run_verify.sh)，调用原样导出的接收 RTL，逐文件验证 [SHA-256](adi_upstream.sha256)。下列三种来源任选一种。`VERILATOR` 设为已可用入口或沿用 PATH。非系统发行版还应按其自身文档配置 Python、`VERILATOR_ROOT` 等运行环境。`ADI_RX_SCRATCH` 可指定交付树外的已有目录。

```sh
ADI_HDL_CHECKOUT=/path/to/adi-hdl ./io/adi_receiver/run_verify.sh
ADI_HDL_SOURCE_DIR=/path/to/verified-source-export ./io/adi_receiver/run_verify.sh
ADI_HDL_FETCH=1 ./io/adi_receiver/run_verify.sh
```

Git 路径读取固定提交对象，对指定检出不做修改。固定线上路径只取清单文件并校验散列，离线模式适用于已冻结的来源目录。本轮 Git HTTPS 取 ref 遇超时，随后通过官方 GitHub API 获取提交和受影响 blobs，核对官方对象散列，再以本地原样导出复跑成功。整组成功标记为 `PASS ADI receiver interop: 4 runs, commit f518dd124fc005c6f9cd80b7947b10a638c37b37`。

交付树提供本项目测试台、理想数字原语、脚本、散列与记录。ADI 源码和原许可文件保留在交付树外的 scratch。原语模型仅替代 `IBUFDS/IBUFGDS`、`BUFIO/BUFR`、`IDELAYE2/IDELAYCTRL`、`ISERDESE2`，保持 `BITSLIP` 硬原语禁用，运行原版 ADI 拼字和软位滑移。宏的模拟相位、延时校准、眼图、抖动、FPGA 实际走线和电压兼容须以厂商模型与台架数据闭合。原版 HDL 与测试模型的 Verilator 诊断留在外部构建日志中，作为功能仿真验收，编译诊断分类见 [实跑结果](evidence.txt)。
