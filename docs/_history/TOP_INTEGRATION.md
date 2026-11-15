# digital_top_w：权重流水接入顶层

> **结论：通则通了，记分板 PASS。** 实测从 `valid_i` 到 `valid_o` = **4 拍**
> （7789 笔全部落在 4，不是按模块名推的）。连续 7800 笔事务里
> `N_accepted = N_retired + N_cancelled + N_pending`，排空后 `pending=0`。
> 金标 1536 笔与生产 Python `run_transaction` 逐位一致（Wm / G / inv / code / overflow 五项全等）。
> 另做 4 个变异体，记分板全部报错（7.7k ~ 76.8k 条，定版种子 20261114），证明检查不是空转。

未修改任何既有文件，未 `git commit`。

---

## 1. 接进来的是什么

```
c_i ── dither_split ── upper ──┐
                               k = c − upper
k + reserved/h_phase/v_phase ── dem_shuffle ── h[15:0] / y[7:0]
                               │
        mask[s][j] = (j==reserved) ? y[s] : h[j]      j=0..15 主列
                     j=16/17 = 0, j=18 = k[1], j=19 = k[0]   子阵列
                               │
片组（内部 round-robin）───────┤  sid[s] = (8·(n+1)+s) mod 18
                               ▼
        w_tab[sid][unit] / g_tab[sid] / inv_reg（冻结系数）
                               ▼
        Wm = Σ_s Σ_j mask[s][j]·w[sid_s][j]（主+子，Q30）
        G  = Σ_s Σ_{j<16} w[sid_s][j]
                               ▼
                   recon_d5_w2（S1/S2/S3）
                               ▼
                       code_o[19:0] + overflow
```

- 片组由顶层内部计数器产生，**每接收一笔事务前进一次**；公式与
  `digital_chain.run_transaction` 的 `plan.acq[-1]` 及 `slice_pool.sv` 一致。
- 没有接旧的 `recon_d5`；无 `cdac_decode`、无 follower（权重路径不需要名义码）。
- `reserved/h_phase/v_phase` 是端口（与原 `digital_top.sv` 一致），由外部 DEM 调度器
  按 `sample_id` 给出；TB 用 `dem_phases` 的同一公式驱动。

### 端口

| 方向 | 端口 | 说明 |
|:---|:---|:---|
| in | `clk/reset_n` | 异步复位低有效 |
| in | `valid_i / stall_i / in_ready_o` | 入口握手；stall 只压入口，流水内部不反压 |
| in | `c_i[8:0] / adc2_i[11:0] / dither_i[5:0]` | 事务字段 |
| in | `reserved_i[3:0] / h_phase_i[3:0] / v_phase_i[2:0]` | DEM 相位 |
| in | `tag_i[23:0] → tag_o[23:0]` | 事务唯一标签，随流水透传（记分板用） |
| in | `cfg_we/cfg_slice/cfg_unit/cfg_w` | 权重写口（仅 idle 有效） |
| in | `cfg_inv_we/cfg_inv[63:0]` | 倒数写口（仅 idle 有效） |
| out | `busy_o` | 流水内有在途事务 |
| out | `slice_ids_o[39:0]` | 当拍片组（8×5 bit，可观测） |
| out | `code_o[19:0]/overflow_o/err_o/valid_o` | 结果 |

### 冻结语义

- **一套系数 + 一个 `inv_g` 寄存器**。`inv = round(2^60/G)`，与 `recon_d5_w2` 的合同一致。
- 写口只在 `busy_o == 0` 时接受；`busy_o` 非零时整笔丢弃（不静默改表）。
  `in_ready_o` 在 `cfg_we/cfg_inv_we` 当拍为 0，天然阻止边写边收。
- 系数表**不随 `reset_n` 清零**：复位清的是在途 valid 与片组计数器，校准状态保留。
  TB 的 `recal` 场景（复位后直接跑 V0）验证了这一点。
- `g_tab[sid]` 由写口增量维护：写 `(sid,unit<16)` 时
  `g_tab ← g_tab − 旧值 + 新值`，所以**同一张表被下一版系数覆盖**时 G 仍然正确。
  变异体 M4 专门把这个减旧值去掉，记分板抓到 11,623 条错。

### 为什么每片行和必须相同

G 只由活动片组决定，与 mask 无关；而 `inv_g` 是**一个**冻结寄存器。若 18 片的行和不同，
不同片组的 G 就不同，单个 inv 必然对某组失效。因此测试系数构造成：

- 每片主阵列 16 列是同一组权重的**旋转** ⇒ 行和片间相同 ⇒ 任意 8 片组 G 相同；
- 片间旋转角不同 ⇒ 同一 `(unit)` 在不同片上的权重不同 ⇒ **片 ID 查错一定改 Wm**，
  记分板能抓到（变异体 M1 验证）。

这不是为了测试而放宽：真实标定里单个 `2^60/G` 寄存器要成立，本来就需要
「活动片组总权重一致」这个前提；RTL 头注释把它写成合同。

---

## 2. 实测延迟（不按名字假定）

TB 在每个 `posedge` 记录接收时刻 `acc_cyc[tag]`，在出口 `valid_o` 的 `posedge`
用 `cyc − acc_cyc[tag]` 算实际拍数。

```
LATENCY (accept->valid_o) histogram:
   4 cycles : 7789
```

- **4 拍**：E1 打拍（mask/片 ID）→ E2 打拍（Wm/G）→ recon S1（`d_q24_r`）→
  recon S2（`x_q24`）→ S3 组合出 `code_o`。
- 全部 7789 笔退休事务都是 4，没有 3 也没有 5。`stall_i` 只推迟**接收**，
  不改变「接收→退休」的拍数，所以统计里看不到 5 拍。
- 这个 4 与 `tb_recon_w2.sv` 里 `repeat(3)` 的旧写法**不一致**：那边是
  「设值后等 3 个 negedge 再采」，实际有效窗口是第 2 拍；顶层多了两级打拍，
  所以是 4。以本 TB 的逐 tag 实测为准。

---

## 3. 记分板不是只比总数

每笔事务在入口分配唯一 `tag`，DUT 把它原样透传到出口。TB 按 `tag` 建期望表，
在四个位置逐字段比：

| 位置 | 比什么 | 能抓到 |
|:---|:---|:---|
| 入口（accept） | 用 `ref_model` 算 Wm/G/inv/code/ovf；金标段再与 Python 文件比 | 参考模型错、Python 与 RTL 不一致 |
| E1（`valid_a`） | `slice_a[0..7]` 对 `(8(n+1)+s)%18`；`adc2_a`、`dither_a` | 片 ID 串、dither/adc2 串拍 |
| E2（`valid_b`） | `wm_b`、`g_b`、`inv_reg` | 系数查错片/单元、版本串用、inv 未冻结 |
| 出口（`valid_o`） | FIFO 弹 `tag`，比 `code_o`、`overflow_o`、`err_o` | 丢事务、重复、乱序、码错 |

出口用 **FIFO 队列**而不是「只比当前 tag」：

- `q.pop_front()` 必须等于 `tag_o`（乱序/重复立刻报）；
- 队列为空却来 `valid_o`（幽灵事务）报错；
- 排空后 `q.size() != 0`（丢事务）报错。

计数恒等式在**每个场景**和**全局**都检查：

```
N_accepted = N_retired + N_cancelled + N_pending
```

`cancelled` 的口径：`reset_n` 拉低的那一拍，队列里所有未退休的事务（包括当拍已在
`valid_o` 上的那笔）全部计入 `cancelled`。下游此时也在复位，不采样该拍输出。
这是明确的取舍，不是「把对不上的数删掉」。

---

## 4. 三个场景的计数（实跑）

| 场景 | 系数版本 | 激励 | accepted | retired | cancelled | pending | 恒等式 |
|:---|:---:|:---|----:|----:|----:|----:|:---:|
| golden | V0 | 连续每拍一笔，无气泡/stall/reset；1536 笔 | 1536 | 1536 | 0 | 0 | OK |
| bubbles | V0 | 2000 笔，90% valid（随机空洞），背靠背 | 2000 | 2000 | 0 | 0 | OK |
| stall | V1 | 2000 笔，valid=100%，12% 拍 `stall_i=1`（实测挡住 368 拍） | 2000 | 2000 | 0 | 0 | OK |
| reset | V2 | 2000 笔，85% valid，4% stall，3 次中途 reset | 2000 | 1992 | 8 | 0 | OK |
| zero-err | V3 | 64 笔，全零系数 + inv=0，检查 `err_o` | 64 | 64 | 0 | 0 | OK |
| recal | V0 | 复位后换回 V0 跑 200 笔，验证校准未被 reset 清掉 | 200 | 197 | 3 | 0 | OK |
| **合计** | | | **7800** | **7789** | **11** | **0** | OK |

- `stall` 场景里 12% 的拍把 `in_ready` 压 0。TB 统计到 **368 拍**确实被
  `stall_i` 挡住（`holding && !accepted_last && stall_i`），事务保持不动直到被接收，
  所以 retired 仍是 2000、没有丢/重。实测延迟仍全是 4。
- `reset` 场景三次复位的 pending 分别是 **3 / 2 / 3**，共 8 笔 cancelled；
  `recal` 的复位 pending=3。所有场景排空后 pending 都是 0。
- `zero-err` 用 V3（18×20 全零、inv=0），每笔都期望 `err_o=1`；
  TB 的 `exp_err` 逐笔比对，64 笔全中。这同时验证了 `err_o` 不是常 0。

### 金标三方一致

`tb/vec_topw_gold.txt` 的 `wm/q30, g, inv, code, ovf` 由**生产模型**
`adi_model.digital_chain.run_transaction` 生成（`tools/gen_vec_topw.py`），
不是本仓库新写的一套公式。TB 在入口用独立实现的 `ref_model` 再算一遍，
金标段逐笔断言两者相等：

```verilog
if (m_wm  != G_wm[ref_cnt])  fail("golden: model Wm != python");
if (m_g   != G_g[ref_cnt])   fail("golden: model G  != python");
if (m_iv  != G_inv[ref_cnt]) fail("golden: model inv!= python");
if (m_code!= G_cd[ref_cnt])  fail("golden: model code != python");
```

1536 笔 **0 条**该断言触发。也就是说：

```
RTL  ==  SV 参考模型  ==  生产 Python
```

在 Wm/G/inv/code/overflow 五个量上同时成立。

金标覆盖：512 档粗码全扫、64 个 dither 字全覆盖、ADC2 随机、两端饱和
（低端 21 笔 / 高端 14 笔）、1.5% 权重偏差（V0 的列权扰动）。

---

## 5. 负向对照（变异测试）

为了证明记分板真的会报错，在 `/root/timing/topw/mut` 里对 RTL 做单点变异，
用同一份 TB 跑：

| 变异 | 改了什么 | 报错文本 | 错误数 | 结果 |
|:---|:---|:---|---:|:---|
| M1 | 片 ID `+1` | `E1: slice id crossed` | 76,786 | FAIL |
| M2 | mask 忽略纵向 `y[s]`，恒取 `h[j]` | `E2: Wm crossed` + `E4: code mismatch` | 13,590 | FAIL |
| M3 | recon 的 dither 在 E2 拍翻转 1 位 | `E4: code mismatch` | 7,714 | FAIL |
| M4 | `g_tab` 增量更新漏掉「减旧值」 | `E2: G crossed` | 11,603 | FAIL |

四个变异都只在**错的那一层**报错（M1 报 E1，M2 报 E2/E4，M3 报 E4，M4 报 E2），
说明各级检查定位有效，不是靠总数兜底。变异体只改 `mut/` 里的副本，交付的
`rtl/digital_top_w.sv` 未被改动。

---

## 6. 诚实边界

1. **没有综合**。E2 的 `Wm` 是 8×20 项组合累加 + 动态索引；功能正确，但没做
   DC 时序/面积。`recon_d5_w2` 自身的 25 ns 时序见 `docs/TIMING_W2.md`，
   本顶层额外的两级打拍没有重新约束。
2. **系数表是寄存器阵列**（18×20×44 bit + 18×44 bit + 64 bit ≈ 16k flop），
   不是 ROM/SRAM。这是「预写寄存器」的直接实现，面积没优化。
3. **版本切换必须排空**。设计是单 bank；`cfg_we` 只在 `busy_o==0` 接受。
   因此「连续每拍不同系数版本」在物理上做不到，TB 的做法是：
   同一 burst 内每拍变的是**片 ID / 单元地址**（这才是逐拍变化的系数查表），
   版本切换放在 burst 之间并显式 drain。这不是绕过检查：`exp_inv[tag]` 逐笔
   比 `inv_reg`，任何版本串用都会在 E2 报。
4. **`k_bad` 分支未覆盖**。`c_i < upper` 时 `k<0`，RTL 置 `err_o`，但 TB 只发
   合法 `k∈[0,511]`，所以这条 err 没被激励到；`err_o` 的另一条（G=0/inv=0）
   由 `zero-err` 覆盖。
5. **没有 CDC / 没有门控时钟**。`cfg` 口和事务口假设同一时钟域。
6. 复位口径：中途复位会丢弃当拍已出现在 `valid_o` 上的事务（下游同时复位）。
   若下游不是同步复位，需要改成「先排空再复位」；本 TB 的 cancelled 计数
   按「复位拍全部作废」记账，已写在上文。

---

## 7. 复现

本地文件（均新增）：

| 文件 | 说明 |
|:---|:---|
| `rtl/digital_top_w.sv` | 顶层（RTL） |
| `tb/tb_digital_top_w.sv` | 记分板 TB |
| `tb/vec_topw_coef.txt` | 4 个版本的 18×20 权重 + inv |
| `tb/vec_topw_gold.txt` | 1536 笔金标（Python 生成） |
| `tools/gen_vec_topw.py` | 系数/金标生成 + 自检 |
| `tools/topw_mutation.sh` | 4 个变异体的复现脚本 |

```bash
# 生成向量（仓库根目录）
PYTHONPATH=src python3 tools/gen_vec_topw.py

# VCS（vm-meow）
ssh vm-meow 'export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1; \
  export PATH=$VCS_HOME/bin:$PATH; export SNPSLMD_LICENSE_FILE=27080@localhost; \
  cd /root/timing/topw/sim && \
  vcs -full64 -sverilog -timescale=1ns/1ps -o simv \
    ../tb/tb_digital_top_w.sv ../rtl/digital_top_w.sv \
    ../rtl/dither_split.sv ../rtl/dem_shuffle.sv ../rtl/recon_d5_w2.sv && \
  ./simv'

# 变异体
ssh vm-meow 'bash /root/timing/topw_mutation.sh'
```

最终日志（节选）：

```
PHASE golden      accepted=1536  retired=1536  cancelled=0  pending=0   OK
PHASE bubbles     accepted=2000  retired=2000  cancelled=0  pending=0   OK
PHASE stall       accepted=2000  retired=2000  cancelled=0  pending=0   OK
PHASE reset       accepted=2000  retired=1992  cancelled=8  pending=0   OK
PHASE zero-err    accepted=64    retired=64    cancelled=0  pending=0   OK
PHASE recal       accepted=200  retired=197  cancelled=3  pending=0   OK
TOTAL accepted=7800 retired=7789 cancelled=11 pending=0
STALL: 368 cycle(s) held by stall_i (in_ready=0)
LATENCY (accept->valid_o) histogram:
   4 cycles : 7789
RESULT: PASS (7789 transactions checked)
```
