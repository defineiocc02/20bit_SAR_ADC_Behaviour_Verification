# 八片 dither 数字顶层

[`dither8_top`](rtl/dither8_top.sv) 复用模块一的调度和桥接映射，输出 SADC 低路标签和八片 RDAC 的子阵列采样掩码。同笔转换按负注入 `k=c−U`（C9）设置 RDAC，`c<U` 下溢拒绝，按样本号找回原采样掩码供权重重构使用。顶层增加一个数字周期发布采样描述符，保持连续接受每沿一笔的能力。模拟采样必须留出描述符和开关建立时间。[接口与算术合同](SPEC.md) · [时序结构](microarchitecture.md)

当前负注入版的独立 20,000 拍对拍覆盖 10,638 个采集描述符、4,222 个有效命令和 271 次下溢；完整轨迹与断言检查见 [本版数字验收](../../task_integration/evidence/negative/README.md)。七拍形式检查由本版 CI 重新执行，下面的旧形式及物理收据只证明各自归档版本。

布线报告定位到转换组 ID 比较经过扰动选词和粗码加法后驱动 RDAC 掩码。候选由调度事件选择同一笔扰动字，并行执行粗码加法和 ID 校验。旧版按 ID 选词的表达式与候选在共享状态上通过 7 拍有界比较。物理证据按源提交匹配。[独立 20,000 拍对拍](independent_review/receipt.json) · [有界比较](evidence/context_equivalence.json) · [布线报告](../physical/README.md)

`REQ-D8-05` 已通过。`dither8_top` 用 `62e3e3a` 的同版 RTL 生成 SKY130HD GDS。在 6.25 ns、TT 25 °C、1.80 V 条件下，提取后 setup 裕量为 +0.154860 ns，hold 裕量为 +0.0631387 ns，公开 DRC 零违规。同一份 GDS 的旧版和上游修订版 LVS 规则分别报告 `FAIL` 和 `PASS`。早期映射 setup 裕量为 −0.676904 ns，布线后在该角修复。技能 G4 仍为 `BLOCKED`，待补多角 STA、实际系统的 I/O 时序预算与活动功耗证据。[代理后端收据](evidence/physical_receipt.json)

```sh
python -m pip install -r task1/requirements.txt
PYTHONPATH=.:src python task2/eight_rtl/tools/run_checks.py --out /tmp/dither8-checks
yosys -s task2/eight_rtl/tb/formal.ys
python task2/eight_rtl/tools/run_context_equivalence.py --out /tmp/dither8-context-equivalence
```

构建依赖现有 Verilator 和 clang++。`VERILATOR` 可指定工具命令。输出目录须在工作树外且事先不存在。结果 JSON 包含 RTL、验证输入和公开日志的 SHA-256。[验收门禁](gate_evidence.json) · [交接清单](handoff_manifest.json)
