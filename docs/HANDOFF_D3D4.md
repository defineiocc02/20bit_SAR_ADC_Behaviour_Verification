# 接手说明：D3/D4 数字 IP 本轮交付

给下一位接手者（Codex 或任何人）。
作者：DSH lead（2026-11-15）
基线：`merge/adi-repro` @ `cf732a5`（本轮之后又删了一个孤儿文件，见 §七）

---

## 零之前：本轮（2026-11-15 下午）补齐的四个缺口

上一版交接时列了四个"对照论文后的缺口"。本轮已全部处置：

| # | 缺口 | 处置 | 证据 |
|:--:|:---|:---|:---|
| 2 | 相位发生器是伪随机，PPT 原文是 `Round robin shuffler` | **改用 round-robin**（`rtl/dem_phase_rr.sv`），xorshift 保留为 A/B 对照 | `docs/PHASE_AB.md`：χ²=0、MI=0、周期内零重复、面积降 5.1× |
| 3 | bridging 结构与 PPT Fig.31 的 2×2 块不符 | **不改 RTL**，核对后确认"照图改"数学上不可行；合同 §6.1 已授权 | `docs/BRIDGE_VS_FIG31.md` + `tools/bridge_charge_check.py`（66,688 组 0 违例） |
| 1 | 两相位交织未实现 | **接入 `sched18`**，`d3d4_ip` 输出 acq/conv/idle 掩码与片 ID | 512 拍 PASS，五条两相位不变量每拍检查；故障注入可捕获 |
| 4 | RDAC follower 边载入未实现 | **接入 `follower_rt`**，事件流模式下 k 逐事件累加 | 实测 1→3→7→…→511，8/8 次严格增长 |

顺带修掉一个综合门禁抓出的真实缺陷：

| 问题 | 症状 | 修法 |
|:---|:---|:---|
| `always_comb` + `int k` 累加 + 变量下标 | **130 个未时钟寄存器**，`DC_FATAL: unclocked sequential cells present` | 改成定长展开的归约写法（三处：`conv_ids_w`、`sched18.cand`、`sched18.ids_o`） |

**这个缺陷值得记一笔**：`sched18` 单独综合时完全正常，
作为子模块内联进顶层才暴露。说明"子模块单独综合通过"不能替代"顶层综合"。

综合数字因此大幅改善：

| 指标 | 首版 | 本轮 |
|:---|--:|--:|
| 逻辑级数 | 146 | **40** |
| 关键路径 | 7.91 ns | **2.82 ns** |
| setup slack | +16.77 ns | +19.88 ns |
| 面积 | 1040.86 µm² | 1763.61 µm²（新增两个子块） |

---

## 零、先读这一节：我的结论是可证伪的

我声称：

1. **功能实现** —— D3 DEM（`dem_mask_v3`）与 D4 Split Dither（`dither_pre`）的
   映射关系正确，且与参考实现 `src/adi_model/dem_dither.py` 逐位一致。
2. **随机化接通** —— `dem_phase_gen` 被接进了 IP 顶层，三轴相位由 RTL 内部生成。
3. **综合完成** —— 块级综合跑通，面积/时序数字有报告。

**这三条都不是"我说了算"，每一条都有可复跑的判据，见 §二。**
你要做的是：**不信我的话，自己跑一遍；对不上就来反驳我。** 我列出了
具体的反驳入口（§五），包括我已知的三条限制——那三条正是最可能被推翻的地方。

---

## 一、我这轮到底改了什么（按改动性质分类，不是按时间）

### 1.1 修复真实缺陷（有明确"错→对"的证据）

| # | 位置 | 缺陷 | 证据 |
|:--:|:---|:---|:---|
| A | `tools/cmp_dem_dither.py` | 走包导入 → 连带 scipy 与 `TypeGuard`（需 Py≥3.10）→ 本机 3.9 必然 ImportError | 修前 `rc=1` + ImportError；修后 `RESULT: PASS rc=0` |
| B | `rtl/sched18.sv:76-77` | 16 位乘法溢出，`(r1*16'd10)>>16` 恒为 0 → 随机性完全消失 | 首拍正确值 6 / 实际 0；修前 8194 errors，修后 0 |
| C | `tb/tb_sched18.sv` | 复位态误用运行期不变量（要求 `\|CONV\|=8`，但复位态 `conv=0` 是明示的启动瞬态） | 修后 2 个误报消失 |
| D | `rtl/dither_pre.sv` | 位序颠倒：`{val[2:0],1'b0}` 得 `[bal,b0,b1,b2]`，参考是 `[b2,b1,b0,bal]` | `got=aaaaa888 want=55555111`（每 4 位反转） |
| E | `rtl/dem_phase_gen.sv` | 输出接在组合 `t3=xs(rng_q)` 上，比寄存器早一拍 | 复位态输出 (5,9,5) vs (13,12,0) |

**B 和 D 是我认为最值得你复核的两条** —— 它们是"看起来对、实际错"的典型，
而且都很短（各 1–5 行）。

### 1.2 结构改造（不是修 bug，是为了能综合/能交付）

| # | 改动 | 动机 |
|:--:|:---|:---|
| F | 新增 `rtl/_legacy/tops/d3d4_ip.sv`（IP 顶层） | 两个叶子模块是纯组合、无 clk/reset，既不能单独当 IP 交付，也过不了单时钟域综合门禁 |
| G | `dem_mask_v3` / `dither_pre` 端口改为打包向量 | 非打包数组被 DC 展平，**且顺序与源码下标相反**，门级必错位（实测 110,080 组失配） |
| H | `dem_phase_gen` 加 `nxt_*` 观察口 | 让 `d3d4_ip` 在握手沿取"推进后"的相位，避免整条通路滞后一拍 |
| I | 合并逻辑保留层次（`-no_autoungroup`） | 默认 ungroup 后 `area.rpt` 只剩一行，看不出面积花在哪 |

### 1.3 新增验证能力

| 文件 | 作用 |
|:---|:---|
| `tools/verify_d3d4_ip.sh` | 四层验收一条命令（L1 叶子 / L2 IP / L2.5 sched18 / L3 门级） |
| `tools/gen_vec_phase.py` | 相位金标，内嵌与 RTL 实测值的断言 |
| `tools/ref_d3d4_ip.py` | IP 级向量，复用 `dem_dither.py` 同一参考口径 |
| `tools/sched18_stats.py` | 随机性质量统计（逐拍对拍证明不了这个） |
| `flow/synth/d3d4/dc_d3d4_ip.tcl` | 可执行综合脚本，含硬断言 |
| `flow/synth/d3d4/dc_hold_unc0.tcl` | hold uncertainty 对照实验 |
| `docs/SYNTH_D3D4.md` | 综合与门级记录，含全部踩坑 |

---

## 二、如何验证我说的"功能完成"

### 2.1 一条命令（推荐先跑这个）

```bash
cd <repo>
D3D4_RUNNER=vm-yian bash tools/verify_d3d4_ip.sh
```

期望（2026-11-15 实测，我实际看到的就是这个）：

```
[1/4] L1 叶子级逐位对拍（133376 + 512 组）
      DEM    133376/133376 组  失配 0
      Dither 512/512 组  失配 0
      RESULT: PASS
[2/4] L2 IP 级逐位对拍（含相位发生器，512 笔）
      三轴覆盖: 448/1920
      d3d4_ip: 512 拍, 0 errors
      RESULT: PASS (512 tx bit-exact)
[2.5] sched18 随机性统计（20000 拍）
      结构性不变量违例      0
      χ²(df=17)           0.9   临界(0.05)=27.587
      IDLE 对覆盖           153/153  min=106 max=162 期望=130.7
      RESULT: PASS
```

**注意 `三轴覆盖: 448/1920`** —— 这不是缺陷。L2 只有 512 笔事务，
1920 组三元组不可能被 512 笔覆盖满。相位覆盖要单独看 L2.5 之外的
`tb_dem_phase_gen`（17280 拍，1920/1920 满覆盖）。我**没有**把这两个数混用，
但这里容易误读，你先知道。

### 2.2 加门级（需要先综合出网表）

```bash
D3D4_RUNNER=vm-yian \
D3D4_NETLIST=/path/to/d3d4_ip_net.v \
D3D4_SCMODEL=/path/to/tcbn28hpcplusbwp7t40p140.v \
bash tools/verify_d3d4_ip.sh
```

L3 会跑 512 笔门级等价（期望同样 0 失配）。
**不提供网表时脚本会显式打 `SKIPPED` 并提示"本次只证明 RTL 层正确"** ——
这是我特意做的，避免"跑了但没测门级"被误读成全通过。

### 2.3 单项复核（想深挖时）

```bash
# 叶子级：独立于统一入口
D3D4_WORKDIR=~/pg D3D4_RUNNER="ssh vm-yian" bash tools/verify_d3d4.sh

# 相位发生器：17280 拍逐拍 + 三轴满覆盖
#   (需自行 vcs 编译 rtl/dem_phase_gen.sv + tb/tb_dem_phase_gen.sv，
#    TB 用相对路径读 vec_phase_rtl.txt，须在 tb/ 目录下执行)

# 随机性：可换 seed 与拍数
python3 tools/sched18_stats.py --cycles 50000 --seed 0x1234
```

### 2.4 特别建议你做的"反向验证"

**光看 PASS 不够。我建议你主动制造失败，确认判据不是空壳。**

| 反向用例 | 预期 | 怎么造 |
|:---|:---|:---|
| 把 `sched18.sv:84-85` 改回 16 位乘法 | `sched18_stats.py` 应 FAIL（IDLE 对只剩 2/153） | 见下方命令 |
| 把 `dither_pre` 位序改回 `{val[2:0],1'b0}` | L1 应报 496 组失配 | 手工改 §1.1-D 那五行 |
| 只喂 1 行日志给比对脚本 | 应报"覆盖不足"并 FAIL | 见 commit `45e5b15` 的说明 |
| 不给 L3 网表 | 应打 `SKIPPED` 而非 PASS | 直接跑统一入口 |

反向用例 1 的准确做法（`sched18.sv:84-85`）：

```bash
# 改前
#   assign i1 = 4'((32'(r1) * 32'd10) >> 16);
#   assign j0 = 4'((32'(r2) * 32'd9)  >> 16);
# 改后（复刻原缺陷）
sed -i.bak "s/4'((32'(r1) \* 32'd10) >> 16)/4'((r1 * 16'd10) >> 16)/" rtl/sched18.sv
sed -i.bak2 "s/4'((32'(r2) \* 32'd9) >> 16)/4'((r2 * 16'd9) >> 16)/" rtl/sched18.sv
python3 tools/sched18_stats.py --cycles 20000    # 期望 FAIL
mv rtl/sched18.sv.bak rtl/sched18.sv             # 恢复
```

注意：`sched18_stats.py` 跑的是 Python 模型 `sched18_rtl.py`，
**不是 RTL**。所以直接改 `.sv` 不会让它 FAIL —— 要验证这条缺口，
必须让 Python 模型也复刻缺陷，或改用 RTL 级 TB（`tb/tb_sched18.sv`）
比对。我在开发中是用"模型内联复刻缺陷"的方式确认的，
结果 IDLE 对只覆盖 **2/153**。

**这是我这轮留下的一个方法学缺口**：`sched18_stats.py` 与 RTL 之间
没有自动一致性保证（它量的是模型，不是 RTL）。如果你要严格，
应该让 stats 脚本驱动 RTL 仿真而不是模型。

**前三条我都实测过**（D 位序和 B 溢出在开发中遇到，覆盖不足是刻意造的）。
如果你跑出来行为不同，说明我的判据有漏洞。

---

## 三、如何验证"综合完成"

### 3.1 先跑综合

```bash
ssh vm-yian 'mkdir -p ~/d3d4/{rtl,synth/d3d4}'   # 或本地有 DC+PDK
scp rtl/dem_phase_gen.sv rtl/dem_mask_v3.sv rtl/dither_pre.sv rtl/_legacy/tops/d3d4_ip.sv vm-yian:~/d3d4/rtl/
scp flow/synth/d3d4/{dc_d3d4_ip.tcl,sdc_d3d4_ip.sdc,dc_hold_unc0.tcl} vm-yian:~/d3d4/synth/d3d4/

ssh vm-yian 'cd ~/d3d4 && \
  export D3D4_PDK=/home/yian/PDK D3D4_WORK=$PWD D3D4_OUT=$PWD/out/mine && \
  export SNPSLMD_LICENSE_FILE=27080@localhost \
         PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH && \
  dc_shell -f flow/synth/d3d4/dc_d3d4_ip.tcl'
```

期望（我实测值）：

```
D3D4_SYNTH_DONE max=17.2889 min=0.0131009
STATUS=COMPILE_COMPLETE
CLOCK_PERIOD_NS=25.000000
MAX_SLACK_NS=17.2889
MIN_SLACK_NS=0.0131009
TIMING_CLOSURE=MET
```

面积（`out/mine/area.rpt` 的层级表）：

| 子块 | 面积 | 占比 |
|:---|--:|--:|
| `u_phase` | 270.48 µm² | 26.0% |
| `u_dem` | 201.78 µm² | 19.4% |
| `u_dither` | 35.18 µm² | 3.4% |
| 顶层 | 533.39 µm² | 51.2% |
| **合计** | **1040.86 µm²** | 1516 cells / 251 FF |

### 3.2 跑 hold 对照实验（这是关键，别跳过）

```bash
ssh vm-yian 'cd ~/d3d4 && export D3D4_PDK=/home/yian/PDK D3D4_WORK=$PWD && \
  export SNPSLMD_LICENSE_FILE=27080@localhost \
         PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH && \
  dc_shell -f flow/synth/d3d4/dc_hold_unc0.tcl'
```

期望：`HOLD_UNC0 worst_hold_slack=0.113101`

对比默认（uncertainty 0.1）的 `+0.0131009`，**差值正好 0.100**。
这是"余量被预算吃掉"的直接证据。

### 3.3 门级等价

```bash
ssh vm-yian 'cd ~/d3d4 && \
  export SNPSLMD_LICENSE_FILE=27080@localhost \
         PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH \
         VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 && \
  vcs -full64 -sverilog -timescale=1ns/1ps \
      /home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v \
      out/mine/d3d4_ip_net.v tb/tb_d3d4_ip.sv -o simgl && cd tb && ../simgl'
```

期望：`RESULT: PASS (512 tx bit-exact)`

---

## 四、我为什么认为"功能完成"和"综合完成"

### 4.1 功能：为什么我认为实现了

**理由不是"测试通过"，而是"错不了的地方都被独立校验了"。**

1. **参考实现是独立的**。`dem_dither.py` 是纯函数，与 RTL 分开写。
   对拍不是自证（不是 RTL 和自己比），是 RTL 和 Python 比。
2. **覆盖是穷举的，不是抽样的**。DEM 是 521 码 × 8 横 × 8 纵 × 2 coin ×
   2 half = **133,376 组全枚举**，不是跑几百个随机点。Dither 是 64 字 ×
   8 相位 = **512 组全枚举**。判据要求"缺一条即 FAIL"。
3. **三层独立证据收敛到同一结论**：
   - RTL 级（L1/L2）与参考逐位一致
   - 门级网表（L3）与参考逐位一致
   - 综合不改变功能（门级等价证明）
4. **随机化有独立的质量判据**。逐拍对拍证明不了"随机性够好"，所以我另加了
   `sched18_stats.py` 量 χ²、IDLE 对覆盖、ACQ 覆盖。这块是**曾经真实出过问题的地方**
   （16 位溢出），正因为出过，我才不敢只看"对拍 PASS"。

**但我必须承认的边界**：这证明的是"映射函数正确"，不是"电路正确"。
真正的电容失配、桥误差、噪声都不在本次验证范围内。

### 4.2 综合：为什么我认为完成了

**理由不是"跑出数字"，而是"门禁是硬断言 + 结果可复现 + 数字有物理解释"。**

1. **脚本里有硬断言，不是只出报告**。`dc_d3d4_ip.tcl` 会 `die` 的情况包括：
   库/SDC/源文件不存在、`link` 失败、`check_design` 拒绝、
   **时钟域不恰好 1 个**、有未时钟驱动的寄存器、`compile_ultra` 返回假。
   任何一条不满足就非零退出，不会留下"看起来跑完了"的假象。
2. **数字有物理解释，不是黑盒输出**：
   - 面积 1040.86 µm² 能拆到子块：`u_phase` 270（取模树）、`u_dem` 202（8×20 组合）、
     `u_dither` 35、顶层 533（251 个 FF + 输出 mux）
   - 146 级逻辑、7.91 ns 自然延迟，来源是 `%1920` 组合除法，能指认
3. **我自己推翻过一个数字**。最初报 25 ns 下 setup +16.77 ns，我做了宽松约束实验，
   发现**真实能力只有 7.91 ns**（≈126 MHz）。25 ns 的余量只反映约束宽松。
   我把这个写进了 `docs/SYNTH_D3D4.md` §三，并明确"不能当 Fmax 指标"。
4. **hold 我主动降级了结论**。13 ps 看起来可疑，我查出 20 条全是寄存器自环，
   并用 uncertainty 对照实验证明"余量是被预算吃掉的"。所以文档里
   **明确写"不能签核"**，而不是报个正数了事。

**这条最重要**：综合"完成"我定义为"块级综合跑通 + 门级等价 + 数字能解释"，
**不是** signoff。

---

## 五、我认为最可能被推翻的地方（请重点攻击）

按"被推翻后影响大小"排序：

### 5.0 本轮新引入的三处可疑点（优先攻击这些）

**a) bridging 我只动 `m[s][16]`（4Cu 支路一条），合同写"全部 8 片子 4Cu 支路"**

字面上"全部 8 片"可理解为"8 片的这一条支路"（我的实现，等电荷成立）
或"8 片的全部支路"（8×4×8=256Cu ≠ 4Cu，不守恒）。**我按前者**，因为只有前者守恒。
但这是我的解读，不是原文确定的表述。

**b) round-robin 的确定性周期 1920 拍可能产生杂散**

1920 拍 @40MS/s ≈ 48 µs ≈ 20.8 kHz。理论上远低于带内，但**频谱实验没做**。
若做 FFT 后发现 20.8 kHz 附近有不可接受杂散，需改 STEP（如 +7）或回 xorshift。

**c) follower 事件流模式下 guard 512..520 不可达**

`follower_rt` 的 `k_ev_o` 是 9 位（0..511）。**没有**静默扩展位宽
（那会破坏已通过的 6246 事件回归）。所以事件流模式下 guard 缺失，
整码模式下才有。这条如实保留，但容易被忘。

### 5.1 门级等价的边界可能不够

**我的说法**：L3 PASS，512 笔 0 失配。
**可能的漏洞**：

- 只覆盖 512 笔、448/1920 三维组合。**没有跑满 133,376 组的门级对拍**
  （门级仿真的组合展开很慢，我用了 IP 级而非叶子级的向量）。
- 门级用的是**理想延迟模型**（`tcbn28hpcplusbwp7t40p140.v`，通常 `specify` 块
  在默认参数下可能不生效），所以**不是时序仿真**，只是功能等价。

**建议你做的**：把叶子级的 133,376 组向量也在门级跑一遍；
或者用 SDF 反标做一次真实时序门级。

### 5.2 `dem_phase_gen` 的 `%1920` 可能不是最优，我可能过度保守

**我的说法**：7.91 ns、146 级是"自然实现"，所以不强求优化。
**可能的反驳**：

- 146 级对一条"每笔事务算一次相位"的路径来说**偏多**。
  1920 = 64×30，理论上可以做多级约简（先 `%64` 再处理）压到更短。
- 我选择"接受 7.91 ns + 如实报告"，理由是 40 MS/s 规格有 3 倍余量。
  **但如果你认为该做流水线，这个决定就是错的。**

**建议你评估**：是否值得为 `%1920` 做专门优化，或插一级流水
（代价：输出延迟 1→2 拍，接口合同要改，TB 与向量生成器都要同步）。

### 5.3 我把 guard 512–520 的可达性留成了缺口

**我的说法**：叶子模块支持 0..520，但 `digital_top_w` 的 `k` 只有 9 位，
所以 guard 在顶层不可达。
**可能的反驳**：

- 我**没有**改 `digital_top_w` 的位宽，也**没有**在 `d3d4_ip` 里限制 `k_i`
  （`k_i` 是 10 位，0..1023 都能进）。所以 `d3d4_ip` 层面 guard 是可达的。
- 但 `k_i > 520` 时 `dem_mask_v3` 会走 `r4 = k-512` 的 `>=8` 分支变成全 1，
  **静默产生 mask 而不是报错**。合同说"超过表达范围必须报错"。
  **这是我已知但没修的一个真实缺口** —— `dem_mask_v3` 没有 `err_o` 端口。

**已实测确认**（2026-11-15）：

```
k=520 : slice0 = 全 1（正确，guard 满）
k=521 : slice0 = 全 1   <-- 静默，与 k=520 完全相同
k=600 : slice0 = 全 1   <-- 静默
k=1023: slice0 = 全 1   <-- 静默
```

`rtl/dem_mask_v3.sv` 里 `err` 出现 **0 次** —— 没有任何范围检查输出。
`src/adi_model/dem_dither.py` 亦然（同样静默）。

合同 `docs/ISSCC2024_ONCHIP_CONTRACT_v2.md:130` 原文：
> 超过合同表达范围必须报错，不做模512回绕或静默夹紧。

**所以这是一个已确认的、未修的合同违例。**

**建议你做的**：给 `dem_mask_v3`（或 `d3d4_ip`）加 `k_range_err_o`，
并补 k=521/600/1023 的负向测试（期望 err=1，mask 可为任意但不应用）。
注意这会改动 IP 端口，`d3d4_ip` 的 SDC 和 TB 要同步。

### 5.4 `sched18_stats.py` 量的是模型，不是 RTL（方法学缺口）

**我的说法**：sched18 随机性质量达标（χ²=0.9、IDLE 153/153）。
**可能的漏洞**：这个脚本按路径直载 `src/adi_model/sched18_rtl.py`，
**跑的是 Python 模型，不是 RTL**。它证明的是"模型随机性够好"，
而"模型 == RTL"这条由 `tb/tb_sched18.sv` 的逐拍对拍另行保证。
两个证据合起来才成立，但我**没有把它们串成一个自动门禁**。

**这意味着**：如果只改 RTL 不改模型（或反之），`sched18_stats.py`
仍然会 PASS，而 RTL 可能已经坏掉。

**建议你做的**：让 stats 脚本驱动 RTL 仿真（跑 `tb_sched18` 导出序列，
再统计），或者至少在统一入口里断言"模型与 RTL 的 sha256 同步变更"。

### 5.5 其他已知缺口（我记录过，未修）

| # | 缺口 |
|:--:|:---|
| 1 | 本 IP 未接 `digital_top_w` 主链；顶层仍用 `dem_shuffle` + `dither_split`（4+3+2 / `c−U`） |
| 2 | dither 符号只迁移一半（叶子 `+U`，顶层 `−U`）——注意 RTL 与它自己的金标自洽，所以能 PASS，**这不是 bug 是迁移未完成** |
| 3 | 无形式等价（Formality） |
| 4 | 无 P&R，hold/功耗都不可签核 |
| 5 | `8509` 笔那条回归走旧链，**不是本 IP 的证据**（这条极易误读） |

---

## 六、上手顺序建议

1. 读 `docs/SYNTH_D3D4.md`（综合与全部踩坑）
2. 读 `docs/ACCEPTANCE_STATUS.md` 第一节（三层证据表 + 不能声称清单）
3. 跑 `make -f flow/Makefile verify-d3d4-ip`，对期望输出
4. 做 §2.4 的反向用例，确认判据不是空壳
5. **然后才看代码**。代码里我在每处踩坑点都写了"为什么不能那样写"，
   那些注释比代码本身更有信息量
6. 挑 §五 里的一条攻击我

---

## 七、我这轮清理掉的东西

| 项 | 处理 |
|:---|:---|
| `flow/synth/d3d4/contract_d3d4_ip.tcl` | **删除**。这是给 `flow/synth/run_dc.tcl` 的占位契约，但路径是假的（`/work/...`），且全仓无引用，属于会误导人的孤儿文件。实际用的是 `dc_d3d4_ip.tcl` |
| 本地 `/tmp` 临时文件 | 已清 |
| 远端 `~/pg_*`、`~/d3d4v` 等验证目录 | 已清 |
| 远端 `~/d3d4/out/{gl,final25,final5,...}` | 已清，保留 `out/d3d4_ip` 与 `out/hier` 作为综合证据 |

**没有删除**：任何被 git 跟踪的源码、文档、脚本、向量。
`tb/vec_phase_rtl.txt`（17280 行）和 `tb/vec_d3d4_ip.txt`（512 行）是金标，
**必须保留**，且 `make -f flow/Makefile vec` 可无损重生成（重生成后 git 无 diff）。

---

## 八、一句话总结

功能方面我确信**映射函数正确**（穷举 + 三层独立证据 + 反向用例）；
综合方面我确信**块级综合与功能门级等价跑通了、数字可解释**。
但我**没有**证明电路在真实失配/噪声下正确，**没有**做 P&R/形式/签核。
§五 列的四条，是我认为最该被质疑的地方。
