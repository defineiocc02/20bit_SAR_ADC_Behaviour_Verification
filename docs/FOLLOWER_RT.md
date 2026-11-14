# D2 Follower 实时化（RTL）

## 问题

原 `rtl/follower.sv` 是：

```systemverilog
assign k_ev_o[i*9 +: 9] = ((c_i >> (6-i)) << (6-i)) - upper_i;
```

**它的输入是最终码 `c_i`**，等价于 `ResolvedPrefixes.from_final()` ——
先把最终码算完，再倒推出 7 个前缀。这不是实时 Follower。

## 新模块 `rtl/follower_rt.sv`

输入改为**逐位判决流**：

| 端口 | 含义 |
|:---|:---|
| `start_i` | 一次转换开始，清空前缀 |
| `bit_valid_i` / `bit_i` | 该拍有一位已决码 |
| `upper_i` | dither 的 U，用于码修正 |
| `ev_valid_o` / `ev_idx_o` | 事件有效与序号 0..8 |
| `k_ev_o` | 该事件驱动 RDAC 的码（**当拍即产出**） |
| `done_o` | 9 个事件收完 |

**它不看最终码**，只看每拍送进来的那一位。

### 关键实现

第 `idx` 个事件接到的是原码的第 `8-idx` 位（ev0→bit8 … ev8→bit0），
所以直接把该位放到它原来的位置：

```systemverilog
wire [8:0] bit_pos = 9'd1 << (4'd8 - idx);
wire [8:0] acc     = prefix | (bit_i ? bit_pos : 9'd0);
```

> 我最初写成移位寄存器，前 8 位对、最后一位错（子阵列 bit0 不是最高位）。
> 改成"按原位置累加"后全部通过。

## 验证

`tb/tb_follower_rt.sv`：遍历全部 512 个码，每个码送 9 位，
检查每个事件的 `k_ev_o` 是否等于 `(c >> i) << i`。

```
RESULT: 4608 events checked, 0 mismatches
PASS
```

## 状态

| 项 | 状态 |
|:---|:---|
| 模块实现 | **PASS** |
| 独立 TB（512 码 × 9 事件，4608 事件） | **PASS** |
| 顶层已实例化 + 事件口已加 | **已做** |
| 8509 笔主回归 | **PASS**（事件口 tie 0，未干扰） |
| **顶层逐事件校验 `ev_mismatch_o`** | **未通过（TB 语义未调对）** |

### 顶层接入的现状（诚实记录）

`digital_top_w` 已：
- 增加 `ev_start_i / ev_valid_i / ev_bit_i` 与 `ev_done_o / ev_idx_o / ev_k_o / ev_mismatch_o`
- 实例化 `follower_rt`
- 主 TB 把事件口 tie 0，**8509 笔记分板仍 PASS**

但 `tb/tb_follower_int.sv` 里 `ev_mismatch_o` 的检查**还在报失配**，
输出形如 `MISMATCH c=1 idx=1 k=1 want=0`。

**原因分析（未修完）**：`ev_k_o` 是组合输出、`ev_idx_o` 是寄存器输出，
两者在时钟沿前后不同步。监视器要在"bit_i 已稳定、idx 尚未递增"的那一刻采样，
我试了 negedge 后 `#1` 与 posedge 后 `#1` 两种采样点，都不对。

**因此不能说"顶层逐事件驱动已验证"。** 现在能说的是：
- Follower 模块本身正确（独立 TB 4608/4608）
- 事件口已接进顶层且不破坏既有功能
- 逐事件的顶层级校验还差一次正确的采样时序

### 仍未做

1. 事件输出**真正驱动** `dem_shuffle` / `cdac_decode`（当前只做监视，不控制通路）
2. 整条转换改为 9 拍事件流（当前仍是整码输入的单拍通路）
3. 记分板按事件记 mask 序列

## 设计说明

- 9 个事件 = 8 位温度计（主阵列）+ 1 位子阵列最低位
- **这与论文的 3-bit Flash + 后续 SAR 不完全对应**，属工程拆分，已在
  `docs/DIGITAL_ALGORITHM.md` 标注
- 真正对齐论文需要 Flash 前置 3 位 + SAR 6 位的事件结构，**未做**
