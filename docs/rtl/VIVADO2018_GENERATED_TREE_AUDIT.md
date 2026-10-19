# Vivado 2018.3 生成树连接审计

2026-09-30 的真实 Vivado 综合发现：RTL 仿真通过的跨 `generate` 前向层级引用，在 Vivado 2018.3 中被报告为无驱动，并丢失 Flash 编码和加权归约的输入连接。这使原先两次全顶层综合结果失去功能有效的 PPA 基线资格。原始报告与失败网表保留；新结果必须通过映射后功能检查。

## 故障证据

旧代码每个生成节点声明局部 net，并读取后续节点：

```systemverilog
for (genvar i=1; i<2*LEAVES; i++) begin : g_node
  wire [ACC_W-1:0] count;
  if (i<LEAVES)
    assign count=g_node[2*i].count+g_node[2*i+1].count;
  // leaf / padding ...
end
assign sadc_code=g_node[1].count;
```

工具 `Synth 8-3848` 报 `g_node[1].count` 无驱动；原基线加权树的 `total/gain/on_sum/dither_sum` 根节点也收到同类警告。原全顶层 DCP 的 EDIF 中，全部 7 个 `flash_therm` 输入只有端口声明、没有 `portref` 扇出。`cal_weight_reduce` 只剩无输入的三位输出和 GND/VCC/CARRY4。不能把该现象解释为正常的层级优化。

小型复现 wrapper 包含 7 输入和 511 输入两种 Flash 编码器，以及 3 个物理 slice、每 slice 3 个 48 位权重、2 个活动 slice 的归约器。修改前 969 个输入中 959 个无端点扇出，网表仅剩 2 LUT。XSim 第一项实际输出为 `Z`，并非预期的数字 0；因此第一项就失败。RTL 仿真同一激励则通过，证明只做 RTL 仿真不能发现该映射问题。

## 修复及不变性

模块级显式 net 数组在展开生成块之前声明全部树边；每个生成节点只用常量索引访问这些边：

```systemverilog
wire [ACC_W-1:0] count_tree [1:2*LEAVES-1] /* verilator split_var */;
for (genvar i=1; i<2*LEAVES; i++) begin : g_node
  if (i<LEAVES)
    assign count_tree[i]=count_tree[2*i]+count_tree[2*i+1];
  // identical leaf / padding ...
end
assign sadc_code=count_tree[1];
```

同样替换 `cal_weight_reduce` 的 total、on、列求和、mask、signed rail 树。列求和读取 `total_tree[TREE_LEAVES+S*N_U+U]`，继续复用相同物理权重叶节点，不再跨生成作用域引用 `.g_leaf.extended`。

- 每个叶的输入、活动选择、补零和位宽保持原样。
- 每个父节点仍连接 `2*i` 与 `2*i+1`，加法顺序和树深度保持原样。
- `rail_tree` 的每个元素显式声明为有符号，未改变 66 位符号传播。
- 未添加寄存器、时钟、状态、端口或算法近似；组合延迟的周期数不变。
- `split_var` 是模拟器划分提示，使常量索引的独立元素分别分析；它不豁免 `UNOPTFLAT` 等错误，也不改变综合电路。生产严格 lint 保留原来的错误等级。

旧公式的公平比较版本从提交 `2de928066508e2cfd009df3e7c6b2936bec6837c` 提取，只应用同样的显式树边修复，保留四棵原始归约树；该版本放在外部 `baseline_fixed/`，不替换生产优化公式。

## 映射后功能见证

持久测试文件：

- `sim/tb/tree_mapping_dut.sv`：平坦端口 wrapper，避免 RTL/funcsim 接口差异。
- `sim/tb/tree_mapping_tb.sv`：独立标量 oracle，禁止访问 DUT 树节点；同一 TB 驱动 RTL 与 `write_verilog -mode funcsim` 网表。
- `synth/check_tree_mapping.tcl`：仅综合小型 wrapper，导出功能网表、DCP、资源表和逐输入端点扇出 CSV。它没有时序约束，不能用于频率结论。

默认共 12,904 项检查：

| 激励集合 | 检查数 |
|---|---:|
| 7 位 Flash 全部二进制向量 | 128 |
| 511 位 Flash 的 0–511 温度计码 | 512 |
| 6 种有效有序 slice 分配 × 16 main × 4 sub × 4 dither × 2 mode | 3,072 |
| 32×32 全地址编码 × 4 dither × 2 mode | 8,192 |
| 全 48 位权重随机模式、非温度计 Flash 输入和随机控制 | 1,000 |
| 合计 | 12,904 |

oracle 逐物理 slice 计算活动集合，逐单元计算 total、gain、rail 和 invalid。重复 slice 的开关采用 OR 并单次计入权重，保持现有非法配置下已定义的组合输出；invalid 必须另行匹配。随机权重同时覆盖装载器合法域以外的无符号编码，不据此放宽配置合法性。

每项在比较前写入 CSV，包含实际值、参考值、输入、invalid 和 pass。失配时先 flush/close 再 `$fatal`，保留失败行。可用 `+mapping_trace=FILE.csv` 指定文件；`+reducer_only` 隔离加权树负控，避免旧 Flash 的 Z 在前面遮挡归约失配。默认完整测试仍同时比较全部输出。

## 运行与验收

RTL 测试通过 `tools/run_open_rtl.py --tops tree_mapping_tb` 运行。映射检查示例（使用已配置厂商工具的终端，`NEW_OUT` 必须尚不存在）：

```text
vivado -mode batch -source synth/check_tree_mapping.tcl -tclargs RTL_ROOT NEW_OUT
```

随后在 `NEW_OUT` 中用 `xvlog --sv` 编译 `mapped.v`、原 TB 和安装目录的 `data/verilog/src/glbl.v`；用 `xelab tree_mapping_tb glbl -L unisims_ver -s tree_mapping` 创建 snapshot，再执行 `xsim tree_mapping -R`。Windows 不要把 shell stdout 重定向到工具自己打开的 `xvlog.log` 或 `xelab.log`；使用不同的 `.console.log` 文件名。

必须同时满足：综合没有无驱动树警告；969 个输入均有端点扇出；映射后完整测试得到 `TREE_MAPPING_COMPLETE checks=12904`；逐行 CSV 中没有失配；旧引用版负控确实失败。`synth_design completed successfully` 和 0 DRC error 不能替代这些条件。

映射结果证明对应小尺寸组合模块的功能连接恢复，不等于全生产网表的形式等价、布局布线收敛或 ASIC PPA 签核。小型测试的 LUT 差异只能在该尺寸下解释。最终全顶层版本、参数和校准时序需要独立冻结源码并继续检查。

原始资料与最终机器可读证据位于工作区 `outputs/sar_adc_vivado_20260930/`：基线审查为 `baseline_review.md`、`baseline_metrics.json`；修复测试位于 `tree_fix/`。完整 CSV 和 mapped netlist 保留在外部工件，Git 仅保存测试、精简证据及 SHA-256。

## 实跑结果与证据索引

最终 CSV 版 TB 已在三种 RTL 上分别完成 12,904 项检查，并对真实 Vivado 功能网表执行 XSim 2018.3：

| 版本 | RTL 仿真 | 映射后结果 | 有端点扇出的输入 |
|---|---:|---|---:|
| 跨生成块前向引用旧版 | 12,904 PASS | Flash 第 1 项失败；独立归约第 1 项失败 | 10/969 |
| 显式 net 数组、列共享公式 | 12,904 PASS | 12,904 PASS，CSV 无失配 | 969/969 |
| 显式 net 数组、原四树公式 | 12,904 PASS | 12,904 PASS，CSV 无失配 | 969/969 |

两种修复版的完整映射后 CSV 逐字节相同，SHA-256 均为 `64accf5fcae0e656766d74231cc6e003ecaa2563d1b0e40eef858ff80a5f60f7`。旧版 Flash 的真实失败行是实际值 `Z/Z`、参考值 `0/0`；独立归约负控在有效 IDs 0、1 下得到实际 total/gain/rails 全为 0，而三个参考值均为 912（0x390）。后者通过 `+reducer_only` 暂时排除 Flash 比较，避免一个已知故障遮挡另一个故障；默认完整检查没有排除任何输出。

列共享修复版和原公式修复版还分别通过 7 参数组合的未改动原始公式参考 miter，各 1,430,848 项；生产参数覆盖全部 43,758 组 18 选 8 活动集合。三个严格 lint 入口通过，`review_leaf_tb` 完成 91,142 项。此处不将较早的结构 ADC 日志冒充最终组合版本的回归；最终完整源码冻结后的证据另行归档。

精简原始日志、两个负控 CSV、完整输入扇出 CSV、257 条保留原始序号的正向 CSV 摘录、源码 SHA-256 与外部完整工件索引见 [tree_mapping 证据目录](../evidence/20260930/tree_mapping/README.md)。最终判定文件为该目录 `mapped_final_manifest.json`，状态为 `PASS_WITH_NEGATIVE_CONTROLS`。早期 Windows 启动和网络取回失败的 manifest 保留原状，不能用其状态替代最终功能判定。
