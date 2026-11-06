# SAR20 整体数字交付

| 顶层 | 交付范围 | 证据 |
| :--- | :--- | :--- |
| `sar20_digital_lms_top` | 18 片池的 8／8／2 调度，DEM，八片正向扰动，ADC2 原码经片上冻结 LMS 系数重构，CAAZ 配对 | [片上流水接口](LMS_PIPELINE.md) · [RTL 对拍](evidence/on_chip/result.json) · [GDS 与 TT 时序](evidence/physical_tt_42278312924.json) |
| `sar20_digital_top` | 同一调度、扰动和 CAAZ 路径，使用外部已重构 20 位输入 | [外接重构接口](SPEC.md) · [RTL 对拍](evidence/external/result.json) |

片上顶层对拍包括 2% 固定失配池的 4096 笔已归档 ADC2 原码，以及 3% 固定失配池加入确定性换相与 80 μV 共模偏移的 4096 笔代理捕获。两组都启用 Fig.21 DEM 和八片 dither，使用各自训练后冻结的权重。Verilator 逐笔比较调度、采样与回放位图、合法粗码及独立大整数 Q28 结果。旁路输出 4096 笔，CAAZ 输出 2048 对，2014 对使用了不同的两笔扰动字。每 16 拍交接一笔 ADC2，在途标签峰值为两笔，正常码自 ADC2 握手到输出登记为 23 拍。

片上顶层已交付 SKY130HD 的 GDS、网表和提取后时序报告。6.25 ns 周期下，TT 25 °C、1.80 V 的 setup 裕量为 **+0.0805565 ns**，hold 裕量为 **+0.0430949 ns**，两者违例端点均为 0。路由器报告的 DRC 和天线违规均为 0。验收 `REQ-TOP-04` 覆盖本次 TT 数字代理交付及独立验证状态记录。[GDS 和原始报告](../task2/physical/evidence/sar20_digital_lms_top_sky130hd_run_42278312924.zip) · [发布清单](../task2/physical/evidence/sar20_digital_lms_top_manifest_42278312924.json)

独立公开 DRC 和 LVS 的原始命令均在 300 秒超时，延长时限的独立复跑正在进行。多角 STA、系统 I/O 实际预算、活动功耗、电源完整性、整体形式验证及 180 nm BCD 签核仍待验收。实际模拟宏的电气绑定及 ADC2／RA 延迟也须单独验证。 [SKY130HD 物理记录](../task2/physical/README.md)

```bash
python -m pip install -r task1/requirements-calibration.txt
PYTHONPATH=.:src python task_integration/tools/run_checks.py --out /tmp/sar20-ext-recon-check
PYTHONPATH=.:src python task_integration/tools/run_lms_checks.py --out /tmp/sar20-lms-integration-check
```

需要已安装的 Verilator 和 clang++。输出目录须位于工作树外并且尚未创建。 [需求追踪](requirements_trace.csv)
