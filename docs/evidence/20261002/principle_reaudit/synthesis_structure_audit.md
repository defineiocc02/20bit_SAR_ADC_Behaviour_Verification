# 生产 RTL 的综合电路结构复核（2026-10-02）

审查仓库：`/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260919/calibration-closure`。当前 HEAD：`71935ab54238c7ed638f5fa66c5ffb58e10287bb`。逐文件阅读全文范围为 `rtl/rtl_sources.f` 的 24 个 SV 及两个参数头文件。生产几何是 18 个物理 slice、每个 63 main + 8 sub、8 个活动 slice。**顶层源码默认 P_RECON_STAGES=7；实际历史全核综合/布线采用 P5**。两者不能混称为同一个物理实现。

本文件没有修改生产 RTL、没有重新运行 Vivado/远程 EDA。配套 `count_synthesis_structure.py` 检查参数、关键源码锚点和 26 文件 SHA，计算手工复核过的结构公式，生成 `synthesis_structure_counts.json`。脚本是静态公式审计，不是 HDL elaborator；有限 token 检查与历史 `71e7d5a017246340b9fa1f71cbbe99e20b6503f6` 的 26 文件全部相同，不代替宏展开检查、形式等价或新版本物理签核。

## 1. 本轮结论与证据等级

1. **已测事实**：历史 P5/25 ns 全核综合是 108,842 LUT、66,492 FF、20 DSP、0 BRAM、0 latch。完成布线后的核心 setup +0.946 ns、hold +0.052 ns；全部 OOC 路径 hold −2.278 ns、65,048 失败端点。核心通过不等于外部接口通过，更不等于 ASIC 640 MHz。
2. **源码公式估算，且与测量吻合**：权重存储及辅助位有 62,316 位候选 FF，恰好对应历史 u_wstore 的 62,316 FF，占历史全核 FF 的 93.72%。这是当前面积与时钟负载的首要结构问题。
3. **源码结构事实**：缓存 row_total 只替换 total-weight 大树；真实开关加权 on_tree 仍是 1,278 个物理单元叶节点、补齐至 2,048 叶、11 层组合树。当前算法尚不是一个小型、低带宽 SRAM 校正核。
4. **已测事实**：综合网表最差路径是 divider；真实布线最差路径转为 context-ID → rails stage-A 寄存器，互连占 data delay 的 77.875%。后续综合优化不能只减少算术表达式或只缩短 divider。
5. **未实现候选**：context→stage-A 的受控发射/捕获可能支持精确多周期约束；fine-code→fine_r 仍为一拍。必须证明合法 CE/样本时序，再检查 setup 与 hold 的配对关系。当前没有用此候选宣称更高频率。

证据标签使用“源码结构/估算”“历史实测”“未知”。源码语句数量不等于门数、FF 位数不等于 ASIC 面积、层级统计不等于源模块独立成本、data delay 倒数不等于 Fmax。

## 2. 逐模块功能与可能生成的电路

下表的状态位是**源代码声明并被时序过程更新的位数**，未做跨模块常量传播、重复寄存器合并、位宽裁剪、寄存器复制。子模块状态不包含在父模块“自身”位数中；不能直接把整张表相加作为全核 FF。结构模式的兼容路径会裁剪。组合中 integer/临时 logic、树边不会因为声明为 logic 就变成 FF。

| 模块及当前行号 | 功能和捕获点 | 源状态位/结构估算 | 综合注意点 |
|---|---|---|---|
| `adc2_dec:57–104` | 后端 code→Q32 bin center，ties-to-even，范围钳位；stage-A 接收组合结果 | 0 FF；一个有效 13×65 位乘法，78 位结果、79 位加法/检查 | 固定除以 2^13 是连线移位和舍入逻辑；量程乘法仍是变量×变量。历史 4 DSP |
| `analog_phase_ctrl:27–57` | 4 位相位计数，注册 TP/AZ/放大/两参考 enable | 4+5=9 声明状态位；历史 8 FF | `ra_amplify` 与 `ref_accurate` 同式可合并。模拟非交叠、level shifter 和驱动晶体管在宏外 |
| `cal_output_stage:34–79` | 完成沿提交 word/ID/flags；错误事件保留最后合法 word | 20+32+5+1+2+3=63 位；历史 63 FF | 只有寄存器、小量资格门。不能删异常状态来提高吞吐 |
| `cal_residue_mac:33–88` | stage-A 快照→受检分子/shifted/A1；全组合 | 0 FF；一个有效 64×64 位乘法；130/132/97/117/63 位中间量 | 高位零/符号扩展不等于物理 130×64 通用乘法。历史 16 DSP；移位常数为布线；宽加减与范围比较仍存在 |
| `cal_sample_context:32–68` | residue_capture 保存当前 residue 决策；quiet_sample 转交前代 fine packet | CW=709；2×709+4=1,422 位；历史 1,422 FF | 两份历史上下文是因果性所需；按冻结 ID/掩码/注入重构，不能读取新样本 live DEM |
| `cal_weight_reduce:51–185` | ID/开关掩码/实际权重→sum_W、gain、rails | 0 FF；见第 4 节五类树 | 64/66 位组合路径及大扇出；row cache 没有消除 on_tree |
| `calib_regs:80–157` | 三个标量及四控制位、完整性、原子 validate | 192+4+3+1+32=232 声明位；历史 204 FF | 常量错误码上 28 位可裁掉；`cfg_ready` 锁定权重/标量；signed min<max 检查不能改 unsigned |
| `ctrl_fsm:72–113` | 兼容模式 16 相位与样本编号 | 4+32=36 位 | P_STRUCTURAL=1 下使能固定为 0，死路径可裁；不能将其相位表解释为真实结构模式 |
| `dem_addr_gen:57–103` | sid→二维 8×8 缺角主阵列及完整 8-unit 子阵列的反向排列 | 0 FF；63 个 6 位主位置、8 个 3 位子位置的组合结果 | p/8、p%8 是生成期常量；mod8 是低位运算。1D mod63 环替换不等价 |
| `dem_state_gen:68–94` | 两 bank 独立 sid +=433 mod512 | 2×9=18 位；历史结构实例 18 FF | 常数 9 位加法；没有通用 32 位 LCG 乘法器；DEM 是运行时控制，复位默认关闭不代表综合常量关闭 |
| `dither_gen:36–75` | 32 位 LFSR、8 步展开、整数 PMF 映射 | 32 位；默认 D=2 对 raw 取 mod8 | 默认无拒绝尾区间；参数若换成非 2 幂 D，常数余数映射成本变化且需统计/消费契约回归 |
| `div_floor:53–159` | signed floor restoring divide，P_STAGES 位/拍 | P5 329、P6 331、P7 325 声明位；历史 P5 325 FF | P5 每拍 5 级 64 位减法/借位与选择；末拍还含 floor 修正。见第 6 节 |
| `rdac_drv:52–75` | load 沿将 8 组码散射到 18 个物理输出，其余清零 | 18+(18×63)+(18×8)+(18×4)=1,368 位；历史 1,368 FF | 动态 ID 写是译码/优先 mux，不是多端口 SRAM；重复 ID 后者覆盖，越界忽略 |
| `recon_core:154–158,234–278` | stage-A 快照→MAC-launch→divider-run→commit | 自身 32+3×64+2×64+66+5=423 声明位 | 不能把历史自身 295 FF 当成宽度证明；rebuilt/优化会移动与裁剪逻辑。ID、bad-slice 和 pending flag 必须对齐 |
| `sar_structural_ctrl:283–343` | 双 coarse SAR、fine SAR、bank/DEM/dither及截止时刻 | 自身 143 声明位；子模块另计 | `recon_start` 为 wire continuous assign（281），不是 always_ff 产生的注册脉冲；错误码高位可裁，跨源归属会变化 |
| `sar_trial_ctrl:24–75` | flash seed 后逐 bit compare-valid 决策 | coarse 每实例 2×9+4+3=25 位；fine 2×12+4+3=31 位 | fine resolved_valid 未使用可裁 1 位（历史 30）；动态 bit-index 写是小型 decoder/mux，稳定 valid 后每拍推进 |
| `slice_alloc:64–94` | 兼容静态 A/B，0..15 使用，16/17 不用 | 32+1=33 位；ID 为组合式 | 结构模式改用 slice_pool；不能混认完整 18-slice 随机调度 |
| `slice_pool_ctrl:18–81` | 上一 acquiring→converting，再从补集顺序挑 8；8 步 LFSR | 80 ID+36 mask+1 valid+32 ID+1 primed+32 random+5 cursor=187 声明位 | 18 步 count/candidate 优先扫描在**同一组合周期**展开，不是 18 拍；8 位 `%18` 为常数余数网络。历史 192 FF，不可凭声明位解释复制/归属差异 |
| `status_regs:70–105` | 四粘滞、两跟随状态，组合 status ABI | 6 位；历史 6 FF | err_code 不在此保存；status clear 与新错误的优先级和 output_stage 不同，必须按接口用法解读 |
| `swap_decode:61–109` | signed coarse+dither、clip、守边界的零和桥接、main/sub拆分 | 0 FF；常数×1、mod8、位切片可化简 | 签名/宽度转换曾是功能风险；桥接仅交换分布不改变 8 路总命令。不能将 int 临时宽度当成最终 32 位除法器 |
| `unit_therm:54–66` | 逻辑位置<计数→真实物理开关；dither轨 | 0 FF；8×63=504 主比较、8×8=64 子比较、4 dither 比较 | 比较器可能共享/重排；568 个逻辑比较不等于 568 LUT，也不能用 rebuilt 的 1 LUT 宣称基本免费 |
| `weight_store:66–145` | 物理系数、loaded bitmap、每行精确缓存、旧系数读回 | 声明 63,658 位；可证明裁剪后候选 62,316 位 | 同步 reset＋全表组合系数输出使 RAM inference 不适用；见第 3 节 |
| `sadc_enc:40–60` | flash thermometer popcount，平衡树 | 0 FF；结构实例 7 comparator/8 leaf/3 层；standalone 默认511/512/9层 | 全核有一个共享 3-bit flash 编码，不是511 comparator的9-bit flash；气泡/亚稳策略未由 popcount 解决 |
| `sar20_digital_core:173–229,381–488,546–551` | cfg 地址译码、窗口/序列、profile选择、顶层集成 | 同时声明两 profile 的自身 state 共 181 位，结构 profile 不全保留 | P_STRUCTURAL 是生成参数，兼容 mux可常量消去；原始两 profile 代码量不等于双核硬件复制；无 CDC 插入 |

## 3. 权重存储、缓存及读口：面积问题的主因

### 3.1 位数可精确复算

每个合法权重满足 `0<W<2^47`，写入点又显式 mask（`weight_store:140–145`），因此48位输出接口中的 bit47 在 reset 后恒为0。生产数据有效位为：

- 系数数据：18×71×47 = **60,066 位**。
- 当前 epoch 写完 bitmap：18×71 = **1,278 位**。
- 每行和位宽：47+ceil(log2(71)) = **54 位**，18×54 = **972 位**。
- 合计：60,066+1,278+972 = **62,316 位**。

未经裁剪的声明量为 18×71×48 + 1,278 + 972 + 64 = 63,658 位。静态容量满足 32×128×(2^47−1)<2^59<2^60，故 production `STATIC_SUM_SAFE=1`（66–67），全局 sum_all 反馈从 accept 锥删掉。历史 `shared_old_trial_synth/vivado.log:65–68` 同时记录 `ROW_BITS=54`、3D RAM pattern不支持、sum_all 被删除；`out/utilization_hier.rpt:49` 记录 62,316 FF。它是实际存储实现佐证，不是“猜测综合器也许会做”。

### 3.2 不是一份普通系数 ROM

`w_q` 是可重写的寄存器库；转换期间冻结，但综合时不能按某一次仿真加载的系数常量化。`cal_weight_reduce:176–179` 对所有物理叶节点静态连线、并行 gating/相加。每次接受转换前，最多 8×71=568 个有效权重参与 on/tree，总权重按18个缓存行选择；采样 dither还并行使用4个物理列。当前向归约器暴露全表60,066数据位，访问不是“每拍读一个48-bit word”。

若希望用 SRAM/BRAM，必须先设计明确的 bank/端口/时序，而非添加 ram_style 属性：

1. 在新样本已知后，把 ID、DEM/开关掩码及 dither 冻结，确定每个 bank 每拍需要多少系数。
2. 证明读取、局部相加、跨 slice 汇总和 MAC 可以在规定 capture edge前完成；加入上下文 ID和异常对齐。
3. 为同步读延迟、read-during-write、loaded bitmap、clear保留旧值的语义提供实现。
4. FPGA比较采用真实 BRAM 配置和完整布线；ASIC比较采用真实 SRAM macro面积、端口、延迟、功耗模型。

单口47-bit存储读568项至少568拍；18 bank各单口即使理想并行仍需71拍；这两个朴素方案都不能直接满足16拍帧。考虑更宽行、预取、多 bank或预计算需要重新安排吞吐，不能以“容量只有约60kb”推出低成本。

### 3.3 配置读回与行替换仍有组合代价

`old_by_slice[s] = w_q[s][u_idx]`（118）形成18组71:1旧系数 selector，每组有效47位。全局 cfg readback 再做18:1行选择（102）。理想二叉 mux树分别最多7层、5层；这是**拓扑估算**，映射 LUT/MUXF/共享逻辑后的深度需报告。

每行增量缓存更新是 `row_q[s] - old_by_slice[s] + wr_data`（120–126），18份54位串联 subtract/add。虽然每拍只写一个 row，源码18个本地算术锥仍都存在。共享为一份算术可省表达式，然而重新引入跨行反馈 mux和布线，历史已在同类路径上付出严重延迟；优先以布局局部性与真实STA比较，不能自动宣称共享更优。

reset 直接覆盖60,066数据位、bitmap和缓存；在ASIC需估算 reset/CE高扇出 buffer、扫描与时钟负载。若改成只reset bitmap以缩减data reset树，必须证明 reset后读回ABI、缓存替换的旧数据、初次完整加载和可重复clear语义；这是行为变更，不能混入注释整理。

## 4. 归约树、掩码选择和 fanout

| 组合子结构 | 叶/补零叶 | 拓扑深度 | 源 branch表达式数 | 仅删除补零后“两非零输入”合并数 |
|---|---:|---:|---:|---:|
| on_tree | 1,278/2,048 | 11 | 2,047 | 1,277 |
| production total_tree | 1,278/2,048 | 原拓扑11 | branch全部绑定0 | 0；叶仍复用于dither列 |
| cached row_tree | 18/32 | 5 | 31 | 17 |
| 4个 dither column tree | 每个18/32 | 每个5 | 4×31 | 4×17=68 |
| mask_tree | 4/4 | 2 | 3 | 3 |
| signed rail_tree | 4/4 | 2 | 3 | 3 |

还有4份dither条件取负、一个sampling gain减法，以及`rails = total - 2*on + signed_dither`的两项加减（185）。它们都是组合运算。**11层“树层数”与 Vivado 路径的40或110“primitive逻辑级数”单位不同，不能相减或互换。** 常数0、共同掩码、未使用高位及共享逻辑可进一步化简；上述合并数仍不是 mapped加法器数量或门数。

物理 masks先经18×8=144个5位ID匹配；对每个匹配路由71位逻辑开关到该物理行，逻辑选择关系有18×8×71=10,224个crosspoint。`invalid_slice`额外有8个范围检查和8×7/2=28个重复ID比较（79–85）。这些是源码关系计数。一个ID条件控制整行71个开关，physical_on某位再控制47有效数据位，因此 narrow ID/mask具有显著fanout。

历史最差 routed path是：

`u_structure/u_context/fine_slices_reg[1][3]/C` → `u_recon/rails_s_reg[65]/D`。

原始报告 `shared_old_trial_route_25ns/out/internal_max_paths.rpt:14–33`：data=23.914 ns，logic=5.291 ns，route=18.623 ns，40 primitivelevels（21 CARRY4+19 LUT）。其中同一path的IDnet fanout=81（47），匹配后net fanout=72、route=2.434 ns（50），`physical_on34_out[50]` net fanout=47、route=4.375 ns（59）。这些是具体路径证据，并非仅“总线宽所以应该慢”的推测。

建议按顺序比较：① 每个物理slice的mask decode、系数FF与局部相加摆放在同一局部区域；② slice-local reduction→少量跨区总线；③ 仅对实际fanout cone做受控复制；④ 以P5/P6/P7预算研究归约/MAC流水。全网KEEP/DONT_TOUCH可能保留糟糕共享和布线，应只在证实必要的位置使用。不能为了减少本表加法表达式而重新建立8份18:1×48bit coefficient crossbar。

## 5. ADC2解码与残差 MAC：两种宽乘法不能混为一谈

ADC2 乘法是 `(2*code+1)*(max-min)`：code=12bit，奇数中心=13bit，signed差值=65bit。结果78bit保存完整符号。默认乘积的有效输入是13×65；源码显式扩为78位保证算术合法，不代表物理上必须78×78。之后除2^13是固定右移/低13位余数/半值比较/偶数规则，**没有运行时divider**。

MAC 中 `total_s*inj_r`是64位无符号×64位有符号，先零扩total避免混合signed规则；声明130bit乘积，再扩132bit。op1/op2是常数左移。`num`两项132bit减法，`s1`97bit加法，shifted117bit资格检查构成后续路径。转换范围检查失败时仍完成并报告当前ID/flags、保留合法word；删高位“因为正常激励用不到”会破坏异常契约。

历史有效映射是adc2 4 DSP、MAC16 DSP（hier:29,32），并非20个独立逻辑乘法。`vivado.log:462–463`保留缺少乘法后pipeline的工具建议；它表明映射DSP内部流水可能未启用，不能按推荐“4层/16层”机械添加拍数。正确做法是查看同一个netlist中的 DSP端口/register属性、阶段关键路径，再在固定帧预算内选择切点；新配置必须重新核算负数floor、溢出资格及样本关联。

对合法默认8-active几何，选中总数最多568，`sum_W <568*2^47 <2^57`。这是数学上界；当前模块仍以64bit接口及错误范围契约实现。综合器不能默认利用“8个ID永远不重复”等运行时不变量；孤立模块也允许更大维度。任何进一步缩窄都需要证明全部允许配置和异常状态，不能只引用有效样本上界。

## 6. P5/P6/P7 除法与固定吞吐

| Profile | P_STAGES级串联/拍 | 63位numerator循环数 | W_PAD | 声明state位 | 接受start到dout_valid完整间隔 | 16拍输出预算余量 |
|---|---:|---:|---:|---:|---:|---:|
| P5（已测物理） | 5 | 13 | 65 | 329 | 15 | 1 |
| P6（结构候选） | 6 | 11 | 66 | 331 | 13 | 3 |
| P7（源码默认） | 7 | 9 | 63 | 325 | 11 | 5 |

每级对63bit shifted remainder与dv做64bit减法，以借位判断比较；选择减后余数或原值，串到下一级。只有一份组内硬件每拍复用，不是63份除法器；同样也不是`P_STAGES`个独立无依赖减法器。最后一拍还进行负数非零余数的 +1 / negation floor修正。

P5声明state329bit中有padding/未使用高位可裁；历史P5实际325 FF。拓扑stage增大时周期数减小，但单拍组合链更长；**P7有5拍输出预算余量不等于P7更易满足高频率**。新增一个归约stage也必须对busy、commit同沿可接受下一样本、sample ID/flags、cfg_ready下降取消进行回归。

历史post-synth `readings.json.worst_setup_path`：`rem_reg[0]/C`→`q_reg[60]/D`，data14.312ns，logic8.404ns、估算route5.908ns、110 primitivelevels。它是**估算互连**路径；后续route的最差endpoint变化说明placement/route主导。不能用`1/14.312ns`或`1/23.914ns`报告Fmax；周期余量还包含clockskew、uncertainty、setup/hold及其他路径。

## 7. 控制、动态索引与参数风险复核

### 7.1 本轮未发现下列默认profile处存在新的确定功能bug

- weight读：idx_ok同时守两维，非法地址内部安全读[0][0]、外部返回0（95–103）；写accept同时守wr_en、clear、cfg_ready、地址和数值（109–111）。默认STATIC_SUM_SAFE裁全局和合法；row缓存每次替换仍减去旧值，clear不把旧数值误当0。
- weight tree：所有边为模块级显式net且genvar索引静态（cal_weight_reduce:60–66,164–183），不再通过尚未生成的层级节点引用；这正是旧Vivado失联节点问题的针对性修复。
- divider：d高位非零通过dv_large保留宽分母含义（80–84），没有直接截断错商；末拍用q_next而不是旧quo（145–151），负数floor包含remainder条件。
- signed：ADC差值多一位、乘法显式signed/零扩，MAC的total不被解释为负数，clip比较双方signed（recon:199–202）。参数/定点契约的更改仍需独立算术参考。
- SAR bit_index在start初始化为未决MSB，每个valid decrement至0后busy清；默认9/3与12/0不越界。非法内部SEU状态没有额外恢复协议，不应把reset后正常轨迹的安全性表述为抗SEU证明。
- allocator：cursor初值8、shuffle取byte%18、关闭取0，因此正常可达cursor<18；扫描candidate在加k后至多减18一次，从而0..17；count<8才写next_ids[count]。这是依赖状态不变量的安全索引，若未来开放cursor写入端口必须新增边界检查。

### 7.2 几个应作为工程风险保留的结构

1. `slice_pool_ctrl:41–47`的18步计数/优先写关系可能形成长组合选择链；`integer count/candidate`源码32bit并不保证映射32bit，但不能假设工具已推成最佳prefix网络。需抽取实际cells/路径，再比较窄类型与并行rank计算；分配器只推进一次/帧，正确CE依据可能允许阶段调整。
2. `rdac_drv`重复ID采用后者覆盖，归约器重复ID采用OR合并并标错，两者并非同样容错策略；默认pool应保证唯一性。对非法ID保护的有效性应分别测试，不能只测mask popcount。
3. flash popcount未做thermometer合法性/亚稳态处理；sync cmp-valid是明确边界假设。真实模拟宏需满足setup/hold和deadline，不因数字仿真理想cmp-model返回而自动达标。
4. `P_RECON_STAGES`顶层允许5..63是功能/帧下界守卫，不是该范围每一值都有合理组合delay。P63会单拍级联63级减法，是极不利高频结构；工程配置应限定已验证候选。
5. cfg、rst_n、cfg_clear_valid、cfg_ready/enable大量参与同步reset和CE；没有时钟门控或CDC。相位enable的注册可避免多bitcounter carry译码直接驱动模拟开关，但registered数字enable自身仍需外部宏/缓冲/非交叠签核。

## 8. 多周期 STA 候选：依据必须是准确捕获关系

**先核wire/NBA，不按“每帧16拍”猜约束。** `sar_structural_ctrl:53`的recon_start是output wire，281为continuous phase15 assign；顶层`launch_recon`（386）也是wire，实例直连（603），`recon_core:250–258`在当前posedge直接捕获。因此当前实现不存在“phase15 always_ff生成start、下一phase0再接受”那一拍延后。

上下文：`cal_sample_context:64–67`在旧phase0 quiet_sample沿将OLD residue转到fine_slices/main/sub/rails/sampling/injection；到旧phase15的launch沿stage-A捕获，正常运行相隔**15个完整clock间隔**。`sar_structural_ctrl:334–336`在phase14捕获fine_code，到phase15的fine_r捕获仅**1个间隔**。两类路径必须分别建模。ID/flags元信息与数值路径的捕获也要逐项核对，不能用wildcard从整个u_context放宽到整个u_recon。

Reset/disable会清掉fine_valid/phase并取消重构；重新enable后首笔只有当前residue已捕获、下一quiet转交、fine_done有效时才launch。缺cmp-valid将fine_ok/coarse_ok置0、取消该帧，不会凭空在较早phase接受。

控制独立复核运行了已有`structural_protocol_tb`的只读外部wrapper：120帧、57次成功launch、63次drop、1,920协议检查；新增1,926沿CSV对全部57次成功事务断言context-age=15T、fine-code-age=1T，通过。`control_minimal_witness/structural_capture_trace.csv`和`structural_run.log`保存实际pre/post phase、quiet、context ID、连续wire launch及StageA影子捕获。此wrapper没有例化weight/recon真实数据通路，不能将影子捕获等同post-synthesis捕获证据；reset/disable末尾取消可见，但重新enable及完整cfg-clear未覆盖。**还没有完成对所有合法输入序列、cfg_clear/re-enable、first-valid、recon-busy/cancel、实现CE和ECO后网表的形式证明**。

实施候选应在以下证据齐备后才写约束：

1. 对允许的reset/enable/config序列证明接受edge固定phase15、context最近更新至少15周期前，内部其他数据路径未形成更早更新/bypass。
2. 冻结实际发射寄存器和捕获寄存器完整列表；核查综合后发生合并、移位、复制的cell名称，不凭source层级wildcard。
3. 为每个MCP精确推导setup/hold边沿（含-start/-end及配对hold）；不要只添加setup例外以消除WNS。
4. `report_exceptions`检查命中数、覆盖、冲突；min/max完整报告审查剩余1拍的MAC、divider、fine_code、cfg及宏边界路径。
5. 功能等价/控制断言、SDF或相应时序仿真、全场景post-route STA后才能验收；记录候选前后差异。

历史当前证据采用默认1拍核心约束，因此25ns核心通过是保守结果；候选并未执行、并未提供新的Fmax或ASIC640MHz结论。控制子审查的只读wrapper有限trace作为补充证据，与本节的未覆盖边界一起阅读。

## 9. 资源与时序报告的正确解读

历史综合采用`synth_design -flatten_hierarchy rebuilt`（`synth/run_vivado_ooc.tcl:42–43`），会跨源模块重排逻辑。`utilization_hier.rpt:39`把40,580LUT归到u_context；但对应source只有两份packet寄存器与捕获选择。时序路径也把归约LUT命名到u_context（internal_max:48–59）。所以：

- 不能说“context模块本身需要40k LUT”。
- 不能说“dem_addr_gen/unit_therm各1 LUT，相关译码几乎免费”。
- 不能把父/子行相加，二次计数。
- 两个源码相同coarse实例45与2,167LUT的不同也不代表第二bank有不同SAR算法；实际迁移/常量共享/归属需cell与cone分析。

可用事实是全顶层总数、原始cell清单、端点到端点路径、布线状态、寄存器/时钟端点数和具有明确身份的DSP mapping。PPA比较必须采用同工具/器件/库、profile、约束、seed、活动窗口及完整时序可行性；当前结果是基线，不足以宣称对论文芯片/其他实现有面积功耗优势。

已有cached P5与shared-old P5历史记录也显示了真实取舍：`vivado_cached_p5/readings.json`为106,205 LUT、66,520 FF；当前结构历史测量为108,842 LUT、66,492 FF，即LUT **+2,637（+2.483%）**、FF **−28**、DSP均20。cached25ns完整route的core setup −0.280ns、hold +0.062ns；shared-old25ns为+0.946ns、+0.052ns。可报告该次布线核心setup由失败变通过，同时LUT增加；不能将“修复时序”包装成“全面积/功耗优化”。这是有身份的不同source单次记录，不代替多个seed的受控统计，也不能将旧实验源文件换成当前HEAD重新贴标签。

`run_vivado_ooc.tcl:56–68`的TIMING_MET判据仅查询max/setup WNS，历史post-synth WHS仍−0.264ns。因此`SYNTH_COMPLETE_TIMING_MET`只代表脚本的setup判断，不能报告“综合setup/hold双通过”。最终route明确同时保留REGREG_TIMING_MET=1、all-path TIMING_MET=0，避免状态名误导。

full-mapped历史438个word/flag输出、3,900配置读回、7,680协议检查、3次cancel能证明受测scenario下功能一致；它没有SDF，没有覆盖非零flags，没有检查内部launch latency。不能把它用于证明宏settling或本轮MCP等价。

## 10. 下一步排序与验收（不给虚假的门数/频率承诺）

| 优先级 | 问题/候选 | 应交证据 | 可接受结论边界 |
|---|---|---|---|
| P1 | 外部OOC hold未闭合、ASIC缺库/宏/RC/MMMC | 58真实port min/max budget、board/宏时序、正式corner集、全路径STA | 真实接口及ASIC依据具备前，不称全核签核 |
| P1 | 62,316FF寄存器库与宽读带宽 | bank/端口/拍预算、SRAM/BRAM模型、reset/cache/readback回归、等价与同约束实现 | 减FF且闭合吞吐/接口后才报告面积收益 |
| P1 | ID/mask→on-tree→rails routed关键路径 | cone/cell明细、fanout报告、跨区线长/拥塞、局部摆放或树候选的完整route min/max | 23.914ns不是ASIC延迟；仅报告同平台实测改进 |
| P2 | 准确context MCP | source边沿＋有限trace＋全序列控制证明、精确命中端点、setup/hold成对、ECO后STA | 例外仅覆盖被证明路径，one-cycle fine/MAC/div仍必须闭合 |
| P2 | divider P5/P6/P7及少量pipeline候选 | 同时测critical path、FF/LUT/DSP、latency/ID/errors、16拍输出cadence | 不按latency短推测clock快；不从data delay倒数给Fmax |
| P2 | allocator优先扫描/输出scatter fanout | 可达索引证明、priority semantics、组合cells/路径、受控候选 | 修改后保持8/18因果分配及非法ID策略 |
| P2 | 同步reset/CE/clock power | 实际库扇出与transition、电容、buffer/clocktree、活动注释功耗 | 62k FF数本身不等于ASIC area/power |
| P3 | rebuilt source归属与setup-only脚本状态 | 叶级cells/cone审计、明确setup/hold标签 | 不把层级标签当独立模块cost |

这轮审查没有以“没有看到新默认profile功能bug”替代电路实现验收。已修复的源码连接、写入守卫与signed契约可以成立，同时当前存储带宽、组合归约、multiplier流水、divider链、配置读回和模拟接口仍是需要真实综合/STA/布线证据关闭的工程问题。
