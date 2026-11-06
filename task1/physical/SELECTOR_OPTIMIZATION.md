# 切片选择器等价优化

SKY130HD 基线的最差 setup 路径从 LFSR 状态出发，经顺序扫描的容量判断回到状态更新使能。当前候选先对物理资格位计数，直接判断是否足够 8 片。轮转后的每个候选用并行 rank 计算有序 slot，LFSR 的扫描游标与 LFSR 状态在同一沿更新。端口、采样请求延迟和 6.25 ns 约束沿用基线。 [基线路径与数据](SKY130HD.md)

Yosys 0.69 直接证明基线与最终候选的 303 个等价点，无未证明点。分段核对串行扫描与并行 rank、原余数与同沿缓存时，也分别证明了 298 和 898 个等价点。另以 SAT 证明同步复位后任意两次请求沿的所有端口一致。Verilator 5.030 对原顶层与桥接顶层的实际 RTL 独立对拍均为零差异，各覆盖 4,096 个稳定槽，含 seed、clear、disable、stall 和连续请求。 [证明、源码与对拍 ZIP](evidence/selector_equivalence.zip) · [SHA-256 清单](evidence/selector_equivalence.json)

相同 Yosys 通用门级流程 `synth -noabc` 的调度器拓扑最长路径由 126 降到 33 级，通用单元数由 8,846 降到 3,553。SKY130HD 结果以该源码的新布线和提取后 STA 为准。

解压证明 ZIP 后，在解压目录依次复跑：

```bash
yosys -Q -T -s proof_cursor.ys
yosys -Q -T -s proof_reset.ys
```

独立复核还证明缓存游标在同步复位后成立且每一步保持，并使用错误旧状态的变异找到反例；穷举全部 4,718,592 个资格掩码／起点的选择顺序一致。独立全端口 miter 复跑超时，全端口结论仍以原归档 303／303 等价点为准。 [独立游标归纳证明](evidence/cursor_induction_independent.zip) · [清单](evidence/cursor_induction_independent.json)
