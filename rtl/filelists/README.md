# v21 RTL 编译清单

当前集成顶层为 `adc_digital_ip_v21`。从仓库根使用时，普通路径和嵌套的 `-f` 均按仓库根解析。

| 清单 | 范围 |
| --- | --- |
| [files_adc_v21.f](files_adc_v21.f) | v21 顶层和综合依赖，含 `recon_v21 → recip_rnd_v21 → recip_q60`。 |
| [files_adc_v21_sim.f](files_adc_v21_sim.f) | 上述源、`adc2_node_macro` 与选定的 v21 测试台。正式回归的逐项输入由 [verify_v21.sh](../../tools/verify_v21.sh) 管理。 |

[files_d3d4.f](files_d3d4.f) 只编译 DEM 与扰动叶子及自己的测试台。DC、FC 与 Formality 的整核入口用[同一读取器](../../flow/synth/v21/design_inputs.tcl)按生产清单顺序展开。

[rtl_sources.f](../rtl_sources.f) 是全部 RTL 的覆盖清单，包含独立组件和测试宏。整链编译以 v21 专用清单为准。组件测试另由其测试台选择输入和源文件，原有顶层与旧数字链回归按 Git 中的版本追溯。

[RTL 导航](../README.md) · [工程合同](../../docs/CONTRACT_CURRENT.md) · [仿真入口](../../flow/sim/README.md)

## 单芯片入口

`make lint` 与 `make sim-top` 从同一清单装配。先读 `files_adc_v21.f`，再读[外围服务](../ad4080_periph/integration/files.f)、[记录读回](../ad4080_periph/integration/readback_files.f)和[单存储产品](../ad4080_periph/integration/product/rtl.f)，最后加入 [ad4080_chip.f](ad4080_chip.f) 的公共退役器与总顶层。产品清单的 `bound_dsp/` 由脚本从固定 Git 对象生成。参考 FIFO 在该绑定里只保留队列元数据。
