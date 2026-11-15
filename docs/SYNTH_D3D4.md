# D3/D4 数字 IP：综合与门级等价记录

生成时间：2026-11-15
基线：`merge/adi-repro`，本文件随 `feat(d3d4-ip)` 一并提交
工具：Design Compiler W-2024.09-SP3 + VCS W-2024.09-SP1
工艺：TSMC 28HPC+ `tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs`（SS corner, 0.81V, -40°C）
主机：`vm-yian`（192.168.38.129，经 `windows-codex` 跳板）

---

## 一、综合对象

新增顶层 `rtl/d3d4_ip.sv`，把三个叶子模块接成一条带寄存器的数据通路：

```
k_i ──────────────────────────┐
dither_i ─ dither_pre ────────┤
                              ├─ 输出寄存器 ─ mask_o / pre_o / U / f
valid_i ─ dem_phase_gen ──────┘   （同沿更新）
```

**为什么必须加这一层**：`dem_mask_v3` 与 `dither_pre` 原本是纯组合模块，
没有 `clk`、没有 `reset`，相位与 coin 全从端口喂。后果有三：

1. 不能作为 IP 单独交付；
2. 过不了任何"单时钟域"的综合门禁（`synth/run_dc.tcl` 要求恰好一个时钟域）；
3. **D3 的"三维随机化"在叶子层根本不存在** —— 相位是外部给的。

接进 `dem_phase_gen` 之后，随机化才第一次真正落在 RTL 里。

---

## 二、综合结果（保留层次，`compile_ultra -no_autoungroup`）

| 项 | 值 |
|:---|:---|
| 时钟周期 | 25 ns（40 MS/s，按 ADC 规格） |
| 端口数 | 231 |
| 单元数 | 1516（组合 1262，时序 251） |
| 总面积 | **1040.86 µm²** |
| 组合面积 | 571.63 µm² |
| 时序面积 | 469.22 µm² |
| setup slack | **+17.29 ns** |
| hold slack | **+0.013 ns** |
| 时序收敛 | MET（约束相对值，非签核） |

### 面积分解（这是保留层次的主要收益）

| 子块 | 面积 | 占比 | 说明 |
|:---|--:|--:|:---|
| `u_phase`（dem_phase_gen） | **270.48 µm²** | 26.0% | xorshift + `%1920` 取模树 |
| `u_dem`（dem_mask_v3） | **201.78 µm²** | 19.4% | 8×20 组合 + bridge 交换 |
| `u_dither`（dither_pre） | **35.18 µm²** | 3.4% | 8×4 组合 |
| 顶层自身（输出寄存器等） | 533.4 µm² | 51.2% | 251 个触发器 + 输出 mux |

原本用默认 `compile_ultra`（自动 ungroup）时只剩一行 `d3d4_ip 978.6 µm²`，
看不出面积花在哪。保留层次后多 62 µm²（1040.9 vs 978.6），换来可读性，
且 setup slack 反而从 +16.77 升到 +17.29。

---

## 三、真实能力：7.91 ns，不是 25 ns

**这是本文件最重要的一条，避免被误读。**

| 约束周期 | setup slack | 含义 |
|:---|--:|:---|
| 100 ns（极宽松） | +92.07 ns | 综合器的**自然实现**：146 级逻辑，**7.91 ns** |
| 25 ns（规格） | +17.29 ns | 约束宽松，反映"规格有余量"，不是性能 |
| 5 ns | ≈ 0 | 综合器被迫重优化，已到能力边界 |

结论：

> **本 IP 的真实组合延迟约 7.91 ns（≈126 MHz），相对 25 ns 的 40 MS/s 规格有约 3 倍余量。**
> `status.txt` 里的 `MAX_SLACK=17.2889` 是"25 ns 减去 7.91 ns 再扣预算"的约束相对值，
> **不能当作 Fmax 指标**。

若需要更高频率：在 `xorshift32` 与 `%1920` 之间插一级流水，输出延迟从 1 拍变 2 拍，
接口合同需同步修改（`d3d4_ip` 目前是单拍输出）。

---

## 四、hold 余量为什么只有 13 ps（必须分开看）

### 4.1 事实：20 条 hold 路径全部是寄存器自环

```
u_phase/rng_q_reg[i] -> u_phase/rng_q_reg[i]   (12 条)
u_phase/res_q_reg[i] -> u_phase/res_q_reg[i]   (6 条)
u_phase/hp_q_reg[i]  -> u_phase/hp_q_reg[i]    (2 条)
```

**没有一条跨寄存器路径。** 结构来源很明确：`dem_phase_gen` 的写法是

```systemverilog
end else if (sample_adv) begin
    rng_q <= nxt;
```

综合成 `rng_q/D = sample_adv ? nxt : rng_q`，即"不推进时保持自己"。
这就是一个 Q→(mux)→D 的零距离自环。

最差一条（`rng_q_reg[31]`）的实际路径：

```
Q                0.09 ns
CKND0 (inv)      0.02
AOI22 (mux 逻辑) 0.02
data arrival     0.13 ns
----
clock uncertainty 0.10
library hold      0.01
data required     0.11 ns
slack             0.02 ns
```

### 4.2 关键：这 13 ps 主要是 uncertainty 吃掉的

把 `set_clock_uncertainty -hold 0.1` 去掉后重跑（脚本：`synth/d3d4/dc_hold_unc0.tcl`）：

| 条件 | hold slack | setup slack |
|:---|--:|--:|
| 带 hold uncertainty = 0.1 ns | **+0.013 ns** | +17.29 ns |
| hold uncertainty = 0 ns | **+0.113 ns** | +17.29 ns |

两者差值 0.100 ns，**正好等于 uncertainty 本身**。这说明余量是被预算吃掉的，
不是路径本身的问题。setup 完全不受影响（hold uncertainty 只作用于 min 分析）。

也就是说：**0.1 ns 的 uncertainty 占掉了 0.113 ns 里的绝大部分。** 剩下的
13 ps 不是电路"快到期了"，而是"预算被设定成几乎等于延迟"。

### 4.3 自环余量小是不是问题？

**是结构必然，不是缺陷，但也不能签核。**

- 自环路径的延迟只有"一个触发器 Q + 一个 mux"，物理上就是 0.1 ns 量级。
  任何带使能的寄存器都会出现这种路径，hold 余量天然接近 0。
- 真正的风险不在自环，而在**跨寄存器路径**。本次设计里没有跨寄存器的
  短路径违例 —— 因为 D3/D4 的所有组合逻辑（DEM 8×20、dither 8×4）
  都在两个触发器之间，延迟远大于 hold 要求。
- **但 ZeroWireload 让结论失效**：连线延迟按 0 算。真实版图里
  Q→mux 的走线可能带来几十 ps 额外延迟，那时 13 ps 可能变成负数。

### 4.4 因此不能声称什么

| 说法 | 是否成立 |
|:---|:---|
| "hold 收敛了" | ❌ 不能声称。零线载假设下的小正数不构成收敛结论 |
| "hold 会违例" | ❌ 也不能这么说。自环延迟极短，实际 P&R 往往靠时钟树偏斜自然满足 |
| "hold 需要修" | ⚠️ 未知。要等 P&R 后有时钟树与真实线载才能判断 |

**正确处理**：在 P&R 之前不签核 hold；若 P&R 后出现自环 hold 违例，
标准修法是给该寄存器插延迟单元（`insert_buffer`）或降低 hold uncertainty
（如果时钟树偏斜确实很小）。

## 五、门级等价

网表：`out/hier/d3d4_ip_net.v`（`change_names -rules verilog` 后导出）
门级模型：`tcbn28hpcplusbwp7t40p140.v`（PDK 自带）

```
d3d4_ip: 512 拍, 0 errors
RESULT: PASS (512 tx bit-exact)
```

覆盖：512 笔事务，三轴覆盖 448/1920（受 512 笔采样量限制，非缺陷）。

### 端口打包是必须的（踩坑记录）

原版 `dem_mask_v3.mask_o` 是 `logic [19:0] mask_o [0:7]`（非打包数组）。
DC 2024.09 **能综合它**，但会把端口自动展平成 `[159:0]`，且
**展平顺序与源码下标相反**（slice 0 落在最高位段）。

第一次做门级仿真时按正序拼接，得到 110,080 组失配；
改成 `20*(7-s)` 后归零。为了避免这个隐含约定，现在两个叶子模块都改为
**显式打包向量**，位序只在源码一处定义：

```
mask_o[20*s + j]   s=片 0..7，j=单元 0..19
pre_o[4*s + b]     s=片 0..7，b=开关位 0..3
```

`pre_o` 的 4 位内部顺序是 `[b2,b1,b0,bal]`（MSB-first），
必须与 `dem_dither.pre()` 返回的 list 下标一致（`i ↔ 位 (3-i)`）。
这里也踩过一次：RTL 原写法 `{val[2:0], 1'b0}` 得到的是 `[bal,b0,b1,b2]`，
整条位序颠倒，症状是 `got=aaaaa888 want=55555111`（每 4 位反转）。

---

## 六、复现方式

```bash
# 1) 拷源码到执行机
ssh vm-yian 'mkdir -p ~/d3d4/{rtl,synth/d3d4,gl}'
scp rtl/dem_phase_gen.sv rtl/dem_mask_v3.sv rtl/dither_pre.sv rtl/d3d4_ip.sv vm-yian:~/d3d4/rtl/
scp synth/d3d4/{dc_d3d4_ip.tcl,sdc_d3d4_ip.sdc} vm-yian:~/d3d4/synth/d3d4/

# 2) 综合
ssh vm-yian 'cd ~/d3d4 && \
  export D3D4_PDK=/home/yian/PDK D3D4_WORK=$PWD D3D4_OUT=$PWD/out/hier && \
  export SNPSLMD_LICENSE_FILE=27080@localhost \
         PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH && \
  dc_shell -f synth/d3d4/dc_d3d4_ip.tcl'

# 3) 门级等价
ssh vm-yian 'cd ~/d3d4 && \
  export SNPSLMD_LICENSE_FILE=27080@localhost \
         PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 && \
  vcs -full64 -sverilog -timescale=1ns/1ps \
      /home/yian/PDK/tsmc28/digital_technology/TSMCHOME/digital/Front_End/verilog/tcbn28hpcplusbwp7t40p140_110a/tcbn28hpcplusbwp7t40p140.v \
      out/hier/d3d4_ip_net.v tb_d3d4_ip.sv -o simgl && ./simgl'
```

---

## 七、明确不做 / 不能声称

| 项 | 状态 | 原因 |
|:---|:---|:---|
| 综合 | ✅ 已做 | 本文档 |
| 门级等价 | ✅ 已做 | 512 笔 0 失配 |
| 形式等价（Formality） | ❌ 未做 | 未跑；门级仿真已覆盖功能，但不等价于形式证明 |
| P&R / CTS / 布线 | ❌ 未做 | 无 ICC2/Innovus 配置，无完整 LEF |
| 真实 hold 签核 | ❌ 不能声称 | ZeroWireload，无时钟树 |
| DRC / LVS | ❌ 未做 | 无版图 |
| 功耗签核 | ❌ 不能声称 | `report_power` 无色切换率输入，只作量级参考 |
| 40 MS/s 系统级 | ❌ 未验证 | 本 IP 只测吞吐，未接入完整 ADC 闭环 |

**CLAIM = IMPLEMENTATION_ONLY + BLOCK_LEVEL_SYNTH**，不是 signoff。
