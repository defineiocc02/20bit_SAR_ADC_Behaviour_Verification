# SAR20 整体数字交付

| 顶层 | 交付范围 | 证据 |
| :--- | :--- | :--- |
| `sar20_digital_lms_top` | 18 片池的 8／8／2 调度，DEM，八片正向扰动，ADC2 原码经片上冻结 LMS 系数重构，CAAZ 配对 | [片上流水接口](LMS_PIPELINE.md) · [RTL 对拍](evidence/on_chip/result.json) |
| `sar20_digital_top` | 同一调度、扰动和 CAAZ 路径，使用外部已重构 20 位输入 | [外接重构接口](SPEC.md) · [RTL 对拍](evidence/external/result.json) |

片上顶层对拍包括 2% 固定失配池的 4096 笔已归档 ADC2 原码，以及 3% 固定失配池加入确定性换相与 80 μV 共模偏移的 4096 笔代理捕获。两组都启用 Fig.21 DEM 和八片 dither，使用各自训练后冻结的权重。Verilator 逐笔比较调度、采样与回放位图、合法粗码及独立大整数 Q28 结果。旁路输出 4096 笔，CAAZ 输出 2048 对，2014 对使用了不同的两笔扰动字。每 16 拍交接一笔 ADC2，在途标签峰值为两笔，正常码自 ADC2 握手到输出登记为 23 拍。

正常功能对拍用同步宏代理。目标工艺、真实模拟宏的电气绑定、实际外部 ADC2/RA 延迟与物理签核按验收单独记录。 [SKY130HD 物理记录](../task2/physical/README.md)

```bash
python -m pip install -r task1/requirements-calibration.txt
PYTHONPATH=.:src python task_integration/tools/run_checks.py --out /tmp/sar20-ext-recon-check
PYTHONPATH=.:src python task_integration/tools/run_lms_checks.py --out /tmp/sar20-lms-integration-check
```

需要已安装的 Verilator 和 clang++。输出目录须位于工作树外并且尚未创建。 [需求追踪](requirements_trace.csv)
