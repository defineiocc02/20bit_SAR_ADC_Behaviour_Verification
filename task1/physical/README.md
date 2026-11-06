# 模块一物理交付

此流程将 `module1_top` 映射、布局、布线并导出 GDS。当前使用公开的 Nangate45 / FreePDK45 工艺代理和 typical 库。SDC 把 100 ns 采样周期分成 16 个数字节拍，对应 6.25 ns 的时钟周期。I/O 延时、负载和不确定度是代理假设，目标宏与工艺确定后需要用实际接口和多角条件重新验证。

| 证据 | 取得方式 |
| --- | --- |
| 本机综合网表、面积和检查报告 | [综合收据](evidence/synthesis/synthesis.json)、[报告](evidence/synthesis/synthesis.rpt)、[网表和日志](evidence/synthesis/module1_mapped.zip) |
| GDS、网表、DEF、ODB、SDC、SPEF、布线后 setup / hold | [交付包](evidence/module1_nangate45_run_42219672742.zip)、[输入与产物摘要](evidence/nangate45_manifest.json)、[GitHub Actions 运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42219672742) |
| 物理工艺、工具版本和同一轮的文件身份 | 制品中的 `manifest.json`、`tools.txt`、`container.txt` |

## 本轮物理结果

源码提交为 `f14fe261f33ca26542d5362ce99b44ba8506d343`，顶层为 `module1_top`。同一 Nangate45 typical 运行完成映射、放置、CTS、布线、RC 提取和 GDS 导出。布线后 STA 读取该轮最终 ODB、SDC 和 SPEF。约束覆盖检查未报告问题。

| 6.25 ns 代理时钟 | 最小裕量 (ns) | WNS (ns) | TNS (ns) | 违例 |
| --- | ---: | ---: | ---: | ---: |
| Setup | +0.6122 | 0 | 0 | 0 |
| Hold | +0.0297 | 0 | 0 | 0 |

详细路由最终记录 0 条路由 DRC 和 0 个天线违规。最终 VDD、VSS 网格均通过连通检查。独立 DRC／LVS 为 `NOT_RUN`，目标工艺多角 STA 与 IR／EM 仍待验收。

GDS 为 7,614,478 字节，解析得到唯一顶层 `module1_top`，含 99 个结构和 56,850 条几何记录。交付包保留原始报告和流日志，30 个产物的大小与 SHA-256 已逐个核对。源提交、实际容器摘要和三份 RTL 的 SHA-256 见 [manifest](evidence/nangate45_manifest.json)。

本机复跑综合须有 Yosys 和公开库。Liberty 可从 [OpenROAD-flow-scripts 的 Nangate45 平台](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/a5ff7ef7dac4338e6e5fad7710b85fc6c8f3503c/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib)取得。输出目录须为空。

```bash
python3 task1/physical/run_synth.py --liberty /path/to/NangateOpenCellLibrary_typical.lib --out /tmp/task1-synth
```

Linux amd64 上有 Docker 时，可用与 Actions 相同的入口复跑后端。

```bash
bash task1/physical/run_proxy.sh /tmp/task1-proxy
```

脚本使用固定 ORFS 镜像，默认关闭依赖 AVX-512 的可选形式等价步骤。通过门槛是流程成功返回、同一轮得到可读 GDS、抽取的 SPEF 和有效的最大／最小延时路径。报告保留 setup、hold 的负裕量，供下一轮优化。目标工艺的多角时序、DRC/LVS、IR/EM 和形式等价另行验收。[ORFS 文档](https://openroad-flow-scripts.readthedocs.io/en/latest/) · [镜像兼容性记录](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/issues/4379)
