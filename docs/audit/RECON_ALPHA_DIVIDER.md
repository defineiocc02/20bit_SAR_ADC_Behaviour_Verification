# `recon_v21` 综合瓶颈：alpha 除法器（已由实验确认）

时间：2026-11-16　基线：`0bc02af`

## 1. 结论

**`recon_v21` 单独综合卡在 alpha 除法器，强制旁路后 99 秒完成。**

| | alpha 可变（生产 RTL） | **强制旁路（诊断）** |
|:---|--:|--:|
| 综合耗时 | **> 10 min（超时终止）** | **99 s** |
| cells | — | 30,561 |
| 面积 | — | **26,128.47 µm²** |
| 最差 slack | — | **+3.02 ns** |
| `DW_div` 组件数 | **13** | **6** |
| 端点 | `z1_q_reg[63]/D`（10 分钟不动） | 通过 |

## 2. 实验方法（可复现）

```python
# 诊断用：只把 alpha_bypass 强制为 1，不改其它任何逻辑
src = open('/tmp/ip/rtl/v21/recon_v21.sv').read()
dst = src.replace(
    "wire alpha_bypass = (alpha1a_q == 32'd1073741824);",
    "wire alpha_bypass = 1'b1;   // [DIAG] forced bypass")
open('/tmp/recon_diag.sv','w').write(dst)
```

然后按 `flow/synth/v21/syn_module.tcl` 综合 `recon_diag.sv`。

**注意：这是诊断改动，不进入生产 RTL。**

## 3. 涉及的具体代码

```systemverilog
// rtl/v21/recon_v21.sv:283-287
if (alpha_bypass)
    z1_q <= d1a_q;
else
    z1_q <= rnd64(d1a_q * 64'sd1073741824, {32'd0, alpha1a_q});
```

`rnd64(n, d)` 是**可配置除数**的 64 位除法：

```systemverilog
function automatic logic signed [63:0] rnd64(input logic signed [63:0] n,
                                             input logic [63:0] d);
```

`alpha1a_q` 是 32 位动态值，所以 DC 必须综合一个完整的 64 位除法器。

## 4. 为什么顶层不受影响

顶层把 alpha 固定为常数：

```systemverilog
// rtl/v21/adc_digital_ip_v21.sv:616
.alpha_q30(32'd1073741824),   // alpha=1.0 旁路（首版）
```

于是 `alpha_bypass` 在顶层恒为真，DC 做常量传播后这个除法器是**死逻辑**。

**但 `recon` 单独综合时 alpha 是输入端口**，DC 必须完整展开 → 卡住。

这解释了一个此前困惑的现象：为什么顶层综合会慢（因为它要把 recon 与其它模块一起 ungroup 后重新分析），
而单独综合 recon 又卡在同一个端点。

## 5. 对后端工作的三个选项

| 选项 | 做法 | 代价 | 风险 |
|:---|:---|:---|:---|
| **A** | 保持现状，综合时把 alpha 当常量传入 | 无 | 失去可变 alpha 能力（但顶层首版本来就没用）|
| **B** | alpha 除法改多周期（每拍处理部分位） | 加流水级 | 改变时序、需重跑回归 |
| **C** | 用倒数 + 余数修正替代 alpha 除法（同 recip_q60 思路） | 面积+延迟 | 需新的误差证明 |

**选项 A 最小**：既然顶层首版 alpha 固定为 2^30，可以在 `recon` 里把
"alpha 可变"作为可选参数（`parameter ALPHA_FIXED`），综合时展开成常量路径。

## 6. 与 ADC2 位数裁定的关系

若 ADC2 从 13 bit 改 14 bit，§9.3 的常数（`-3145728`、`1536`、`768`）全变，
`recon` 的 S1a 会重写。**建议把 ADC2 裁定与 alpha 除法处理一起做，避免两次改动。**

## 7. 复跑

```bash
scp /tmp/mkdiag.py vm-yian:/tmp/ && ssh vm-yian 'python3 /tmp/mkdiag.py'
ssh vm-yian 'export PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost
  cat > /tmp/md.tcl <<EOT
set MOD recon_v21
set RTL {/tmp/ip/rtl/recip_q60.sv /tmp/ip/rtl/v21/recip_rnd_v21.sv /tmp/recon_diag.sv}
source /tmp/syn_module.tcl
EOT
  dc_shell -f /tmp/md.tcl > /tmp/md.log 2>&1'
```
