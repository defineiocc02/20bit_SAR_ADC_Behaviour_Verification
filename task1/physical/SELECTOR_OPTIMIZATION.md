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

## 第二轮等价优化

并行选择器的首次 SKY130HD 抽取后结果表明，两顶层的最差 setup 都转移到转换组 ID 合法性判断，后续路径仍经过采集组的轮转和排序。第二轮将合法性判断改为八路 one-hot 并集、计数与 mask 比较，并将扫描游标同沿缓存为 18 位 one-hot。前向轮转、物理 ID 输出和回转均使用固定连线和并行归约。 [实测时序](SKY130HD.md)

Yosys 0.69 对调度器的可达域等价通过 704 个证明点。另分别验证游标范围与缓存不变量、轮转／回转／ID 组合等价、同步复位后任意两沿，以及合法性计数的逐槽归纳。错位轮转和错误计数两个变异均被捕获。两顶层再次与独立参考零差异逐拍对拍，各覆盖 4,096 个稳定槽，13 项定向 Python 测试通过。完整证明和 RTL 运行日志可解压复验。 [第二轮证明 ZIP](evidence/selector_guard_equivalence.zip) · [输入与日志 SHA-256](evidence/selector_guard_equivalence.json)

同一 Yosys 通用门级流程的桥接整顶层拓扑最长路径由 70 降到 35 级。6.25 ns、TT 25 °C／1.80 V 与 I/O 预算保持，第二轮物理结论以同版抽取后 STA 为准。

独立复核对实际候选的同步复位、onehot 缓存保持与守卫逐槽归纳分别运行 SAT，两项故意错误的变异产生反例。独立整块守卫 miter 在 48 秒限时内未完成；其结论以分解证明和 RTL 对拍为依据，不标记为整块 miter 通过。[独立复核脚本与日志](evidence/guard_independent.zip) · [身份清单](evidence/guard_independent.json)

## 第三轮物理序位优化

第二轮抽取后，剩余最差路径在采集 ID 的序位分配。基础版从 `eligible_i[13]` 出发，桥接版从 `shuffle_i` 出发。第三轮直接在物理编号上计算环形前驱集合和 rank，固定和随机模式并行计算，最后选择输出。时序状态、LFSR、游标缓存、容量条件和接受／拒绝控制沿用同一逻辑。 [第二轮实测时序](SKY130HD.md)

Yosys SAT 对全部 18 个游标和物理位置验证环形前驱关系，捕获绕回逻辑与模式 mux 的变异。用从两版 RTL 原样提取的选片组合逻辑实际运行 Verilator，对所有 18 位资格位图和 19 种模式／游标组合共 4,980,736 项穷举比较，输出 ID 和 mask 均为零差异。两顶层再次完成各 4,096 个稳态槽的独立逐拍对拍，13 项定向 Python 测试通过。各项证明状态和具体源码字节见清单。 [第三轮证明与日志](evidence/direct_rank_equivalence.zip) · [哈希清单](evidence/direct_rank_equivalence.json)
