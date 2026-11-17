# RTL 编译清单

这些 `.f` 为不同顶层和测试台选择编译输入。从仓库根目录使用时，普通路径和嵌套的 `-f` 都按仓库根解析。远端入口由相应脚本准备源目录与激励。

| 清单 | 编译目标 | 用于 |
| --- | --- | --- |
| [files_adc_v21.f](files_adc_v21.f) | `adc_digital_ip_v21` 与综合依赖，含 `recon_v21 → recip_rnd_v21 → recip_q60` | v21 整核综合选源。仿真节点宏和测试台另行编入。 |
| [files_adc_v21_sim.f](files_adc_v21_sim.f) | 上述源、`adc2_node_macro` 与一组选定的 v21 测试台 | 手动编译某个 v21 场景，按工具选定测试顶层。正式回归入口是 [verify_v21.sh](../../../tools/verify_v21.sh)，脚本维护自己的逐项输入表。 |
| [files_d3d4.f](files_d3d4.f) | `tb_dem_dither`、`dem_mask_v3`、`dither_pre` | 叶子对拍，入口 `make -f flow/Makefile verify-d3d4`。 |
| [files_d3d4_ip.f](files_d3d4_ip.f) | 保留的独立顶层 `d3d4_ip` 及其依赖 | 旧 IP 综合选源，组合验收入口 `make -f flow/Makefile verify-d3d4-ip`。按 [PHASE_AB.md](../../../docs/PHASE_AB.md) 选择相位发生器。 |
| [files_topw.f](files_topw.f) | `digital_top_w` 与 `tb_digital_top_w` | 旧权重重构链回归，入口 [run_topw.sh](../../../tools/run_topw.sh)。 |
| [files.f](files.f) | `digital_top`、独立 core 的部分源码和早期叶子 | 早期混合清单，供追溯；按实际场景另选测试台。 |

v21 采用 `dem_mask_v3` 的 3+3+3 映射和 `dither_pre` 的采样预置，事务链使用 `c+U`。旧 TopW 的 `dem_shuffle`、`dither_split` 使用 4+3+2 映射与 `c−U`。独立 D3/D4 IP 的输出是开关命令，旧 TopW 可输出 20 位码。各自的验证结果应与对应顶层、映射和编译清单一起读取。 [RTL 导航](../../../rtl/README.md) · [工程合同](../../../docs/CONTRACT_CURRENT.md)

[rtl_sources.f](../../../rtl/rtl_sources.f) 是 RTL 树的源码清单。独立 `sar20_digital_core` 的源绑定实验还要提供输入包，由 [run_open_rtl.py](../../../tools/run_open_rtl.py) 为选定测试台组装。新增模块时，把实际依赖同步到对应编译清单和回归入口。 [仿真入口](../../sim/README.md)
