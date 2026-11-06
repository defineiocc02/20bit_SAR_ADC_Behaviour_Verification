# 模块一物理交付

此流程将 `module1_top` 映射、布局、布线并导出 GDS。当前使用公开的 Nangate45 / FreePDK45 工艺代理和 typical 库。SDC 把 100 ns 采样周期分成 16 个数字节拍，对应 6.25 ns 的时钟周期。I/O 延时、负载和不确定度是代理假设，目标宏与工艺确定后需要用实际接口和多角条件重新验证。

| 证据 | 取得方式 |
| --- | --- |
| 本机综合网表、面积和检查报告 | [综合收据](evidence/synthesis/synthesis.json)、[报告](evidence/synthesis/synthesis.rpt)、[网表和日志](evidence/synthesis/module1_mapped.zip) |
| GDS、网表、DEF、ODB、SDC、SPEF、布线后 setup / hold | [GitHub Actions](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/task1_physical.yml) 中成功运行的 `task1-nangate45-physical-*` 制品 |
| 物理工艺、工具版本和同一轮的文件身份 | 制品中的 `manifest.json`、`tools.txt`、`container.txt` |

本机复跑综合须有 Yosys 和公开库。Liberty 可从 [OpenROAD-flow-scripts 的 Nangate45 平台](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib)取得。输出目录须为空。

```bash
python3 task1/physical/run_synth.py --liberty /path/to/NangateOpenCellLibrary_typical.lib --out /tmp/task1-synth
```

Linux amd64 上有 Docker 时，可用与 Actions 相同的入口复跑后端。

```bash
bash task1/physical/run_proxy.sh /tmp/task1-proxy
```

脚本使用固定 ORFS 镜像，默认关闭依赖 AVX-512 的可选形式等价步骤。通过门槛是流程成功返回、同一轮得到可读 GDS、抽取的 SPEF 和有效的最大／最小延时路径。报告保留 setup、hold 的负裕量，供下一轮优化。目标工艺的多角时序、DRC/LVS、IR/EM 和形式等价另行验收。[ORFS 文档](https://openroad-flow-scripts.readthedocs.io/en/latest/) · [镜像兼容性记录](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/issues/4379)
