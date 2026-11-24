# RTL 编译清单（多版本索引）

从仓库根使用时，普通路径和嵌套的 `-f` 均按仓库根解析。以下按版本严格隔离清单，避免跨版本混编：

| 版本层 | 清单 | 范围 |
| --- | --- | --- |
| **`v21` 单时钟参考基线** | [files_adc_v21.f](files_adc_v21.f) | 单时钟 `adc_digital_ip_v21` 顶层与 10 个综合叶子（含 `recon_v21 → recip_rnd_v21 → recip_q60`），供算法图册、`verify_v21.sh` 与 `ad4080_chip_top` 使用。 |
| **`v21` 单时钟仿真扩展** | [files_adc_v21_sim.f](files_adc_v21_sim.f) | 继承 `files_adc_v21.f`，追加 `adc2_node_macro` 供行为闭环测试台使用。 |
| **`v21-dualrate-40msps` 双域 40 MSPS 核** | [files_adc_v21_40msps.f](files_adc_v21_40msps.f) | 复用同一批 D1–D5 叶子，替换顶层为双域 `adc_digital_ip_v21_40msps`（`clk_fs=40 MHz` + 自定时 `ev_clk_i` 突发），供 `tb_40msps_dualrate.sv` 与 Synopsys DC/FM/PT 签核脚本使用。 |
| **`ad4080-silicon-shell-r1` 外围流片外壳** | [files_ad4080_silicon_shell.f](files_ad4080_silicon_shell.f) | 4 项外围可综合流片边界模块（可编程 CRC-24、OTP 宏+`ROM_CRC_ERR`、4×4096×20 SRAM 封装、LVDS ODDR/DCO PHY），独立于冻结 `ad4080_periph/`。 |
| **单芯片参考装配** | [ad4080_chip.f](ad4080_chip.f) | 公共退役器与 `ad4080_chip_top`（配合 `files_adc_v21.f` 与冻结外围清单装配）。 |

[files_d3d4.f](files_d3d4.f) 只编译 DEM 与扰动叶子及自己的测试台。DC、FC 与 Formality 的整核入口用[同一读取器](../../flow/synth/v21/design_inputs.tcl)按生产清单顺序展开。

[rtl_sources.f](../rtl_sources.f) 是全部 RTL 的覆盖清单，包含独立组件和测试宏。整链编译以 v21 专用清单为准。组件测试另由其测试台选择输入和源文件，原有顶层与旧数字链回归按 Git 中的版本追溯。

[RTL 导航](../README.md) · [工程合同](../../docs/CONTRACT_CURRENT.md) · [仿真入口](../../flow/sim/README.md)

## 单芯片入口

`make lint` 与 `make sim-top` 从同一清单装配。先读 `files_adc_v21.f`，再读[外围服务](../ad4080_periph/integration/files.f)、[记录读回](../ad4080_periph/integration/readback_files.f)和[单存储产品](../ad4080_periph/integration/product/rtl.f)，最后加入 [ad4080_chip.f](ad4080_chip.f) 的公共退役器与总顶层。产品清单的 `bound_dsp/` 由脚本从固定 Git 对象生成。参考 FIFO 在该绑定里只保留队列元数据。
