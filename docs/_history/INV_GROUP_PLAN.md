# 9 组倒数改造：改动范围与验收（未实施）

状态：**未实施**。本轮只做了范围勘察，未改任何 RTL/TB/向量。

## 为什么不能只改 RTL 一行

现有顶层测试是**围绕"各片行和相等"设计的**。`tools/gen_vec_topw.py` 第 9 行写着：

> 行和相同 ⇒ 任意 8 片活动组的 G 都相同，单个 inv 寄存器才成立

生成器第 91 行还会打印 `9 groups all G={G}` 并断言一致。
所以单改 RTL 会让 TB 与金标一起失效——必须三处同步改。

## 需要的改动

### 1. RTL `rtl/digital_top_w.sv`

| 位置 | 现状 | 改为 |
|:---|:---|:---|
| 端口 | `cfg_inv` | 增加 `cfg_inv_grp[3:0]` |
| L143 | `logic [63:0] inv_reg;` | `logic [63:0] inv_tab [0:8]; logic [8:0] inv_loaded;` |
| L153 | `inv_reg = 0;` | 循环清零表 + `inv_loaded = 0` |
| L175 | `inv_reg <= cfg_inv;` | `inv_tab[g] <= cfg_inv; inv_loaded[g] <= 1;` |
| E1 | — | 新增 `grp_a <= sample_cnt % 9;`（片组号，与 `slice_ids` 同一 n 推导） |
| L228 | `.inv_g_q60(inv_reg)` | `.inv_g_q60(inv_tab[grp_a])` |
| 错误 | — | `!inv_loaded[grp_a]` → 该事务置 err，**不得静默用旧值** |

### 2. 生成器 `tools/gen_vec_topw.py`

- 删除"行和相等"的构造与断言，改用 `ac867af` 的非均匀实例（片增益 N(0,1%)）
- 每版本产出 **9 个** inv（按活动片组），并校验每组 `inv = round(2^60/G_g)`
- 系数文件增加 `TYPE=2`（组倒数），格式 `2 VER GRP 0 VALUE`

### 3. TB `tb/tb_digital_top_w.sv`

- `INVM[ver]` → `INVM[ver][0:8]`
- 加载阶段写 9 组（`cfg_inv_grp = g`）
- 记分板 E2 改为比 `inv_tab[grp]`，gold 也按组取
- 新增两个负向用例：
  - **交换两组的倒数** → 必须失败
  - **漏载一组** → 必须报 err，且不能被旧值掩盖

## 验收（沿用本轮要求）

1. 9 组倒数全部验过
2. 生产 Python ↔ RTL 逐位一致（420 条模块回归 + 7800 笔顶层）
3. 连续事务 / stall / reset / 排空 / 系数重载
4. 交换两组倒数被抓
5. 漏载一组必报错

## 为什么本轮没做

这是 RTL + 生成器 + TB + 金标四处联动的一次改版，需要多轮 VCS 迭代才能收敛
（TB 550 行，向量 1444 行）。半改会让现有 7800 笔 PASS 一起失效，
比不改更糟。故本轮只登记范围，不留半成品。
