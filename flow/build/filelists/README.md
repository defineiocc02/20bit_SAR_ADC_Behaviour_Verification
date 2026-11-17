# v21 RTL 编译清单

当前集成顶层为 `adc_digital_ip_v21`。从仓库根使用时，普通路径和嵌套的 `-f` 均按仓库根解析。

| 清单 | 范围 |
| --- | --- |
| [files_adc_v21.f](files_adc_v21.f) | v21 顶层和综合依赖，含 `recon_v21 → recip_rnd_v21 → recip_q60`。 |
| [files_adc_v21_sim.f](files_adc_v21_sim.f) | 上述源、`adc2_node_macro` 与选定的 v21 测试台。正式回归的逐项输入由 [verify_v21.sh](../../../tools/verify_v21.sh) 管理。 |

[rtl_sources.f](../../../rtl/rtl_sources.f) 是源码覆盖清单。整链编译以 v21 专用清单为准。组件测试另由其测试台选择输入和源文件，原有顶层与旧数字链回归按 Git 中的版本追溯。

[RTL 导航](../../../rtl/README.md) · [工程合同](../../../docs/CONTRACT_CURRENT.md) · [仿真入口](../../sim/README.md)
