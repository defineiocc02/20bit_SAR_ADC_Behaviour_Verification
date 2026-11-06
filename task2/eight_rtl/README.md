# 八片 dither 数字顶层

[`dither8_top`](rtl/dither8_top.sv) 复用模块一的调度和桥接映射，输出 SADC 低路标签和八片 RDAC 的子阵列采样掩码。同笔转换用 `k=c+U` 设置 RDAC，按样本号找回原采样掩码供权重重构使用。顶层增加一个数字周期发布采样描述符，保持连续接受每沿一笔的能力。模拟采样必须留出描述符和开关建立时间。[接口与算术合同](SPEC.md) · [时序结构](microarchitecture.md)

实际 Verilator 对拍覆盖 4,681 沿、4,224 个采集描述符和 4,240 个合法转换命令。64 个扰动字全部覆盖，4,096 笔连续转换无气泡。48 个加码上溢、64 个错 ID、64 个调度 stall、2 个非法种子按合同处理。Yosys SAT 在同步复位起 7 拍内核验采样发布、两代归属、掩码位序和加码守卫。把加法替换成减法后找到反例。[对拍收据](evidence/result.json) · [形式收据](evidence/formal_result.json) · [需求追踪](requirements_trace.csv)

布线报告定位到转换组 ID 比较经过扰动选词和粗码加法后驱动 RDAC 掩码。候选由调度事件选择同一笔扰动字，并行执行粗码加法和 ID 校验。旧版按 ID 选词的表达式与候选在共享状态上通过 7 拍有界比较。物理证据按源提交匹配。[独立 20,000 拍对拍](independent_review/receipt.json) · [有界比较](evidence/context_equivalence.json) · [布线报告](../physical/README.md)

```sh
python -m pip install -r task1/requirements.txt
PYTHONPATH=.:src python task2/eight_rtl/tools/run_checks.py --out /tmp/dither8-checks
yosys -s task2/eight_rtl/tb/formal.ys
python task2/eight_rtl/tools/run_context_equivalence.py --out /tmp/dither8-context-equivalence
```

构建依赖现有 Verilator 和 clang++。`VERILATOR` 可指定工具命令。输出目录须在工作树外且事先不存在。结果 JSON 包含 RTL、验证输入和公开日志的 SHA-256。[验收门禁](gate_evidence.json) · [交接清单](handoff_manifest.json)
