# 有效修复基线的结构面积审计（只读）

结论：不能把 `u_context=82745 LUT` 解读成上下文保存电路太大，也不能把 `u_wstore=42597 LUT` 全算给写入存储器。DCP 的重建层级把归约树、读回和 RDAC 散射的组合逻辑迁入了这些名字下面。当前最明确、最小的无接口改动机会，是显式恒定所有合法权重的第 47 位；网表仍为这些恒零状态保留了 1278 个 FDRE。本文未改 RTL、未运行任何 EDA，不声称已经取得面积或频率改善。

## 1. 来源与计数边界

来源为 `repair_baseline/synth/artifacts/vivado/20260929T190955.260429Z_p7_recovered/out/`，器件为 xc7vx690tffg1761-2、Vivado 2018.3，完整设计 171246 LUT、66817 FF、20 DSP、0 BRAM。综合 Tcl 使用 `-flatten_hierarchy rebuilt`。

直接流式读取 `post_synth.dcp` 中的 `sar20_digital_core.edf`（370648226 字节），没有重写 DCP。EDIF 内 FF primitive 总数为 66817，与利用率报告一致；raw LUT1..LUT6 primitive 共 277037，不能替代物理 LUT 数。两个小 LUT 函数可能配对进入同一个 FPGA LUT；`SOFT_HLUTNM` 的去重组数也只是提示，并不严格等于报告。例如 context 的 raw LUT=147152、soft group=82954，而正式报告是82745。

`XLNX_LINE_FILE` 属性的低12位对应 EDIF `metax FILE` 表，高位对应源行。该解释用实际行号交叉核验。属性说明综合来源，不能精确划分共享后逻辑锥的所有责任；下表只将它用于识别被迁移的算术/选择网络。完整原始计数、源表、逐实例证据及哈希见 `baseline_structure_audit.json`。

| 报告层级 | 正式 LUT / FF | DCP 中实际发现的来源 |
|---|---:|---|
| u_structure/u_context | 82745 / 1427 | 大量 `rails_s`、`op3_w`、`gain_s` LUT 与8119个CARRY4；模块重建输入甚至含原RTL不存在的 `gain_s[59]_i_147[0]` |
| u_wstore | 42597 / 62622 | 归约总和树、cfg_rdata异步读回以及权重/bitmap寄存器；存在4118个CARRY4，不能归因于配置写口 |
| u_structure/u_pool | 7611 / 192 | 大量 `main_sw` / `sub_sw` 散射译码，主要源标记指向rdac_drv |

## 2. context 不是83k LUT的寄存器包

`rtl/core/cal_sample_context.sv:25–44` 只保存两份上下文及valid/bad标志。生产参数下 CW=32+8×(5+63+8)+4+1+64=709，源级状态宽度为2×709+4=1422。映射报告1427 FF属于综合结果，不能仅凭5个差值认定状态错误；综合复制与优化可能改变原语数。

此层级147152个raw LUT中，仅70个源标记直接指向context文件；主要标记为旧归约器的on叶门控（89行，53852 raw）、on树（81行，41325）、total树（79行，23530）。名字与CARRY4数量独立支持“算术被迁入”的判断。**不应为了减少这个层级的LUT而删除上下文、把细量化与当前样本混用或减少必要快照。**

## 3. weight_store：真实FF代价和读回网络

`weight_store.sv:56,111–122` 将全部18×71个48位系数作为并行端口供归约读取，因此网表有61344个权重FF和1278个written位，精确相加62622。0 BRAM并非单独的综合失误：当前接口同时暴露所有1278个系数，简单换成单/双端口BRAM不能满足读带宽。要使用BRAM必须重新分配计算/读取节拍，本轮不应把它当局部替换。

当前写语句120行仅关联49个raw LUT，不能据42597报告猜测写口存在42k宽mux。该层级内20112个raw LUT标记指向 `sar20_digital_core.sv:637` 的 `w_q[rd_slice][rd_unit]` 异步读回；另30916个raw LUT标记指向旧归约器total树79行。配置读回确实是48位、1278选1的大选择网络，但不经A/B不能保证one-hot、改写case或分级选择一定更小。增加读回寄存器会改变当前零周期读接口，不能称为无接口变化。

### 已证实的恒零高位冗余

- `weight_store.sv:67,96,108`：只有0<wr_data<2^47才accept。
- `113–114`：reset后所有权重0。
- `117–122`：clear只清bitmap；唯一修改权重的路径是accept写。
- 因而从reset起归纳得到：每个 `w_q[s][u][47]` 恒为0；高位为1的外部请求仍被拒绝。
- DCP却仍实例化1278个 `w_q_reg[s][u][47]` FDRE，见 `baseline_weight_msb_witness.json`；首个例子为EDIF第4757998行。

最小候选是仅将接受写的数据显式写为 `{1'b0, wr_data[46:0]}`，保持48位端口、合法范围、错误标志、reset/clear/重写与cfg_ready锁定语义。不减少Q30精度，因为第47位本来就不属于合法数值。预计可以消除1278个不必要状态位；最终实际FF/LUT变化需综合确认，未测不能写成已节省。验证需覆盖合法最大值2^47−1、高位越界拒绝、clear保留、重复写、配置锁定和两次reset，并核验网表没有这些MSB FDRE、完整重构结果逐位一致。

## 4. u_pool 与 RDAC 散射

该层级8508 raw LUT中只有785个源标记指向slice_pool本身；6653个指向 `rdac_drv.sv:63`，另789/183指向其清零分支。`main_sw`前缀有6644 raw LUT，说明多数开销是18个物理目的slice的开关路由，而不是PRNG或取模。

`slice_pool_ctrl.sv:25–33` 确有18次顺序扫描、动态mask取位、count优先选择，不能假定它免费；但是目前证据不支持先重写随机分配算法来解决7611 LUT。

可比较的RDAC候选是按18个固定目的slice生成组合next值，对8个源ID逐一匹配并保持最后匹配覆盖，然后在原load沿写入寄存器。这样明确共享每个目的slice的ID译码，避免变量目的packed写入。必须保留 `rdac_drv.sv:22–24` 的重复ID最后者覆盖语义及越界ID忽略，不能未经论证改为OR。测试范围需含重复ID、无效ID、全边界、load保持/reset及真实structural协议；面积收益须映射后再说。

## 5. 归约器的下一轮组合候选及风险

当前列共享版本仍有 `weight=active[S]?w_rom[S][U]:0` 的逐单位叶门控（当前生产文件127行）。可测试恒等式：

`sum_W = Σ_s (active[s] ? Σ_u w[s][u] : 0)`

即先做物理行内总和，再做18个行级门控；总和分支上的门控从1278×48位移到18×SUM_BITS位。另一方面physical_on由相同slice匹配产生，必然蕴含active，故on分支可直接采用 `physical_on ? raw_weight :0`。dither列仍需72个被选择的权重输入，不可一并删除。该候选保持接口和时序状态，适用位宽仍受现有SUM_BITS守卫约束；重复/非法ID也必须按现有语义逐位验证。

这不是无条件频率优化。朴素71项行树加18项总树为7+5=12层，原1278项全局树为11层；active上下文控制路径可能缩短，而权重寄存器路径可能多一层。必须同样约束比较两类路径、LUT、FF及完整miter/独立oracle/映射后功能，不能只看源码更短或门控位数。

## 6. 时间目标不能用面积归属替代

该有效基线最差setup路径为 `u_recon/u_div/dv_reg[0]/C -> u_recon/u_div/q_reg[60]/D`，WNS −17.426 ns，数据路径18.941 ns，135级逻辑，其中117个CARRY4。它属于除法器，不在上述最大LUT层级。当前优化版已另有除法修复，需等待其有效同约束结果才能比较；本文不重复解释为新的已完成优化。此处是post-synthesis估计，也不能替代内部BUFG实际布线后的setup/hold审查。

建议顺序：先完成当前有效baseline/optimized比较；最小独立实验先做恒零MSB显式化，其次才比较行级总和门控或固定目的RDAC。每项单独保留功能与映射证据，避免同时重写存储、时序与分配协议使归因失效。
