# 最终指标（驱动单元正确 + LUT 已修）

时间：2026-11-16　基线：`769d1c3`
网表：`/tmp/dcwork4/adc_digital_ip_v21_syn.v`（DC W-2024.09-SP3）
这是第一个**驱动单元正确**且**LUT 已修**的版本。

## 1. 综合自证（全部通过）

```
STEP_ELAB_OK
CHK driving_cell_exists=1 (BUFFD2BWP7T40P140)   ← 驱动单元在库里存在
STEP_COMPILE_OK
CHK total=2176 clocked=2176
CHK unclocked=0
STEP_DONE
```

| 自证项 | 值 | 要求 |
|:---|--:|:---|
| `driving_cell_exists` | **1** | > 0 |
| `VER-281`（initial 被忽略） | **0** | = 0 |
| `u0_r_reg` 在网表 | **30** | > 0 |
| `VER-954 / LINK-3 / LINK-5` | **0** | = 0 |
| 未时钟寄存器 | **0 / 2,176** | = 0 |

## 2. 面积

| 指标 | 值 |
|:---|--:|
| Number of cells | **165,479** |
| Combinational area | 104,890.38 µm² |
| Noncombinational area | 4,060.73 µm² |
| **Total cell area** | **108,980.41 µm²** |

## 3. 时序（25 ns，SS 0.81 V −40 °C）

```
slack = +3.02 ns (MET)
Startpoint: u_recon/u_recip_rnd/u_recip/u0_r_reg[29]/CP
Endpoint:   u_recon/u_recip_rnd/u_recip/inv_q60_reg[0]
```

**关键路径是 Newton 迭代**（LUT 输出寄存器 → 倒数结果），
这是设计最重的组合段。余量 **12.1%**。

## 4. 三个版本的对照（说明为什么前两个作废）

| 版本 | 驱动单元 | LUT | cells | 面积 | slack |
|:---|:---|:---|--:|--:|--:|
| `dcwork`（gate-equiv） | **删掉了该行**（无驱动） | 已修 | 162,571 | 106,574.71 | +2.72 |
| `dcwork2`（我的 `a237626`） | `BUF_X2`（**不存在**，静默失败） | 已修 | 165,374 | 108,951.11 | +3.02 |
| **`dcwork4`（最终）** | **`BUFFD2BWP7T40P140`（正确）** | 已修 | **165,479** | **108,980.41** | **+3.02** |

三者都满足 25 ns，但只有 `dcwork4` 的输入驱动是完整的。

**面积差异很小**（108,951 → 108,980，+29 µm²）—— 符合预期：
驱动单元只设置输入端口的电气属性，不改变内部逻辑结构。

## 5. 与原型面积对比

| 对象 | 面积 | 口径 |
|:---|--:|:---|
| 原型数字区 | 60,000 µm² | **照片图估的版图区域** |
| 本工程完整顶层 | **108,980 µm²** | **标准单元综合面积** |

数值比 **1.82×**。

**不能**解读为"差 1.82 倍"：
- 口径不同（版图区域 vs 标准单元面积，后者不含布线/宏/IO）
- 原型红框范围未披露
- 本结果未做面积优化（`-map_effort medium`，无 `-area_effort`、无 retiming）

## 6. 验证证据（分层，不互相替代）

| 层 | 内容 | 结果 |
|:---|:---|:---|
| **RTL 回归** | 8 项（含 `tb_recip_q60` 43766 笔、`tb_analog_if` 54 项） | **全绿** |
| **门级仿真等价** | 两笔完整事务，1890 拍 × 19 字段 | **差异 0** |
| **交易门禁** | accepted/captured/output | RTL 与门级均 5/2/2（各 ≥2） |
| **A/B 码值** | A `0xFFF00`、B `0x80000` | 两侧一致 |
| **形式化（i_q60）** | `i_q60_reg` failing | **19 → 0** |
| **形式化（wsum）** | `Wm_q30`/`Wp_q30` | **20 failing，未解决** |

## 7. 未解决

| 项 | 状态 |
|:---|:---|
| `Wm_q30_reg[0..3]` / `Wp_q30_reg` 16 bit | **Formality 20 failing**，需 `analyze_points` |
| 形式化整核等价 | 未通过（20 failing） |
| 布局布线 / 后布线 STA / DRC / LVS / GDS | 未做 |
| 40 MS/s 吞吐 | STA 不能证明（需事务级分析或门级长激励） |
| 三类时间预算 | 已有实测（`TIMING_BUDGET_FINAL.md`），但需按本版网表复核 |
| hold 分析 | 未做（只做 setup） |
| 多 PVT 角 | 只做 SS 0.81 V −40 °C |

## 8. 复跑

```bash
ssh vm-yian 'rm -rf /tmp/dcwork4 && mkdir -p /tmp/dcwork4 && \
  sed "s|/tmp/dcwork|/tmp/dcwork4|g" /tmp/dc_synth_v21.tcl > /tmp/dc_synth4.tcl && \
  PATH=/opt/synopsys/syn/W-2024.09-SP3/bin:$PATH \
  SNPSLMD_LICENSE_FILE=27080@localhost \
  dc_shell -f /tmp/dc_synth4.tcl > /tmp/dcwork4/dc.log 2>&1'

# 五项自证（缺一不可）
grep -E "^CHK driving_cell_exists=" /tmp/dcwork4/dc.log    # 必须 > 0
grep -c "VER-281"  /tmp/dcwork4/dc.log                     # 必须 0
grep -c "u0_r_reg" /tmp/dcwork4/adc_digital_ip_v21_syn.v   # 必须 > 0
grep -cE "VER-954|LINK-3|LINK-5" /tmp/dcwork4/dc.log       # 必须 0
grep -E "Total cell area" /tmp/dcwork4/area.rpt
```
