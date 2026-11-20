# 重构流水同笔对齐：修复连续输入串样

时间：2026-11-16　基线：`6b2cc7b`
反例来源：独立 VCS 复核（`6bf4a2a` 冻结树，10 笔受理 / 8 笔码错）

## 1. 结果

| 场景 | 修复前 | **修复后** |
|:---|:---|:---|
| `stable_one_pulse` | 0 错 | **0 错** |
| `back_to_back`（A/B/C 连续） | 2 错 | **0 错** |
| `bubbles_change_bus` | 2 错 | **0 错** |
| `reciprocal_join_constant_n` | 3 错 | **0 错** |
| `alpha_change_after_valid` | 1 错 | **0 错** |
| **总计** | **8 / 10 笔码错** | **0 / 10** |

```
RESULT: RECON_STREAM_MATCH
```

原 `tb_i4_recon` 8213 项同时保持通过。

## 2. 三个根因（都可逐位复算）

### 2.1 `Wm/Wp` 用了实时端口

```systemverilog
// 修复前
n2a_q <= (z1_q * 64) + (6 * Wm_q30) - (3 * G_q30) - (6 * Wp_q30);
                                          ↑ 实时总线
```

反例 tag 202（连续三笔，G 分别 32/16/64 ×2³⁰）：
S2a 在 t9 读到的是 tag203 的 `Wm=33×2³⁰`、`G=64×2³⁰`：

```
N = 6×33×2³⁰ − 3×64×2³⁰ = 198×2³⁰ − 192×2³⁰ = 6×2³⁰ = 6442450944
```

**与实测错值 6442450944 逐位吻合。**

### 2.2 `G` 给倒数单元用错版本

`recip_rnd_v21` 的 `valid_i=v1_q`，其 `G` 必须与 `v1_q` 的时序链一致。
流水关系是：受理 → S1a（锁存）→ S1b（转发）→ S2a（消费），
两个消费者需要**相差一级**的副本：

| 消费者 | 用哪一版 | 理由 |
|:---|:---|:---|
| `recip_rnd_v21` | `G1a_q` | 与 `valid_i=v1_q` 同链 |
| S2a（`n2a_q`） | `G1b_q`/`Wm1b_q`/`Wp1b_q` | 晚一拍消费 |

### 2.3 `alpha_bypass` 用实时端口

```systemverilog
// 修复前
wire alpha_bypass = (alpha_q30 == 32'd1073741824);
```

反例 tag 501（连续 3 笔，末笔 alpha=2²⁹）：
S1b 在 t7 算 `z1_q` 时总线已是下一笔的 2³⁰，走了旁路：

```
Z = d1a = 3072          （应 6144）
N = 3072 × 64 = 196608  （应 393216）
X = 96  →  Q = 1  →  code = 0x80001   （应 0x80002）
```

**与实测错值 0x80001 逐位吻合。**

## 3. 最小修改

```systemverilog
// S1a：受理沿锁存本笔参数
alpha1a_q <= alpha_q30;
G1a_q     <= G_q30;
Wm1a_q    <= Wm_q30;
Wp1a_q    <= Wp_q30;

// S1b：转发一级，供 S2a
G1b_q  <= G1a_q;
Wm1b_q <= Wm1a_q;
Wp1b_q <= Wp1a_q;

// 三处消费者改用锁存值
n2a_q <= (z1_q*64) + (6*Wm1b_q) - (3*G1b_q) - (6*Wp1b_q);   // S2a 用 b 版
.valid_i(v1_q), .g_q30(G1a_q[43:0])                          // 倒数用 a 版
wire alpha_bypass = (alpha1a_q == 32'd1073741824);           // 用锁存值
```

**保留未动**：倒数算法（`recip_q60`/`recip_rnd_v21`）、流水级数（7 拍）、
输出延迟、所有算术函数。

## 4. 为什么原 8213 项没抓到

原 `tb_i4_recon` 对每组输入**保持相同总线 7 拍**，再在第 7 拍检查一次。
这恰好让实时总线"看起来"就是本笔的值，掩盖了取数点错误。

独立复核用的是**连续受理**激励（相邻拍不同笔），才暴露。

## 5. 复跑

```bash
# 原回归
D3D4_RUNNER=vm-yian D3D4_WORKDIR=/tmp/v21rw bash tools/verify_v21.sh

# 连续输入
ssh vm-yian 'cd /tmp/ip && python3 recon_oracle.py generate stream.vec'
# 编译 RTL + tb_recon_stream.sv，跑，再
python3 recon_oracle.py examine stream_trace.csv report.json
# 期望：RESULT: RECON_STREAM_MATCH
```
