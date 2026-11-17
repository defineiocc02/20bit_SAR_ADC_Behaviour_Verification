# 独立复核待办清单

原复核基线为 `17cb80b`。本页按项记录复核、待办及后续裁定。总体进度见 [v21 状态](../../STATUS.md)，证据见 [审计索引](../README.md)。

## 0. 阅读须知

- 尚未关闭的 RTL 改动和 EDA 任务保留原复核的授权范围。
- 不得据当前结论启动 SRAM 化、算法重构、新一轮 PPA 试验，或重新开启自动 goal。
- 已通过的七个模块**不重跑**。
- 复核方的源级复算**不得写成 VCS 实测**。

## 1. 待办总表

| ID | 事项 | 类型 | 需授权 | 优先级 |
|:---|:---|:---|:---:|:---|
| **T-A** | `recon_v21` S3 的 `err3_q` 缺陷修正 | **RTL 改动** | ✅ | **高** |
| **T-B** | `place_opt` 门禁 + 降低利用率/扩大 core 后重试 | **重跑 EDA** | ✅ | 中 |
| T-C | 已完成的文本更正（DRC 口径、未实例化、面积、失败原因）| 文本 | ❌ | **已完成** |
| **T-D** | 补 foundry DRC / LVS / PG 布线 | **新流程** | ✅ | 待定 |
| T-E | ADC2 位宽及窗口裁定 | 合同 | — | **已裁定为 13 bit、0.75 V** |
| T-F | v21 顶层综合及同版网表 | 重跑 | ✅ | 待办 |

## 2. T-A：`recon_v21` S3 缺陷（最高优先级）

### 2.1 缺陷位置

`rtl/v21/recon_v21.sv` 第 366–378 行，S3 阶段：

```systemverilog
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        v3_q <= 1'b0; tag3_q <= 32'd0; err3_q <= 1'b0; x3_q <= 96'sd0;
    end else begin
        v3_q   <= v2_q;
        tag3_q <= tag2_q;
        err3_q <= err2_q | (|x3_q[95:31]);   // ← 第 373 行，两处缺陷
        if (v2_q)
            x3_q <= (n2_q * $signed({32'd0, i2_q})) >>> 36;
    end
end
```

### 2.2 两处缺陷

| # | 缺陷 | 说明 |
|:--:|:---|:---|
| ① | **合法负数高位全 1 被误判为溢出** | `|x3_q[95:31]` 对合法负 X（高位全 1）归约结果为 1，被当成溢出 |
| ② | **读的是上一拍的值，却配本笔 tag** | `x3_q` 在本拍才被赋新值，第 373 行读的是旧值（上一笔），却与 `tag2_q`（本笔）一同寄存 |

### 2.3 独立源级复算给出的定向序列

条件：`G=32*2^30`、`Wm=16*2^30`、`Wp=0`、`b=768`、`alpha=2^30`。

| 笔 | raw | 正确 X | 正确码 |
|:---|--:|--:|:---|
| A | 2046 | −96 | `0x7FFFF` |
| B | 2048 | 0 | `0x80000` |

**B 会读到 A 的负 X 高位，被误标为错并出零。**

> **本项为我方独立源级复算，尚未新跑 VCS，不能写成实测。**

### 2.4 建议修正方向（未执行）

按复核意见：

- 正确判据应**针对同笔候选 `X_d`**，检查它是否等于低 32 位的**符号扩展**；
- 该判定必须**与该笔 tag 一起寄存**；
- **不能**对旧 `x3_q` 直接取或。

参考改法（示意，非最终方案）：

```systemverilog
// 同拍先算候选 X_d，再判溢出，随 tag 一起寄存
logic signed [95:0] x_d;
logic               err_d;
assign x_d   = (n2_q * $signed({32'd0, i2_q})) >>> 36;
assign err_d = (x_d != {{64{x_d[31]}}, x_d[31:0]});   // 不等于低 32 位符号扩展
// S3 内：
//   err3_q <= err2_q | err_d;
//   tag3_q <= tag2_q;
//   x3_q   <= x_d;
```

### 2.5 边界（复核方明确要求）

- ❌ 不改 DEM / dither / bridge
- ❌ 不重写倒数算法
- ✅ 只改 S3 的溢出判定与寄存对齐

### 2.6 验收标准

| 项 | 要求 |
|:---|:---|
| 定向序列 | A → `0x7FFFF`，B → `0x80000`，`out_err` 均为 0 |
| 回归 | `recon_v21` 相关既有回归全绿 |
| Formality | 若做后端，需重新 `SUCCEEDED` |
| 未跑 VCS 前 | 不得声称已修复 |

## 3. T-B：`place_opt` 门禁 + 重试

### 3.1 依据

`/tmp/fc_recip_rnd.log`：

```
第 4826 行: Error: place_opt bailed out due to unplaced cells. (OPT-062)
            未放置单元报错 608 条（u_recip/ctmi_*）
第 4851 行: set rc2 [catch {clock_opt} e2]     ← 未过门禁仍继续
第 4852 行: Starting 'clock_opt'
            随后 XFORM fatal: HFSINV_3244_5159/ZN
```

**先有 `OPT-062` 与未放置，再进 `clock_opt` 才 fatal。**

### 3.2 两项改动

| # | 改动 | 说明 |
|:--:|:---|:---|
| ① | **加阶段门禁** | `place_opt` 未过（`CHK_UNPLACED > 0`）则不继续 `clock_opt`；脚本里显式判断并中止 |
| ② | **降低利用率 / 扩大 core** | 给布线更多空间。**注意方向：是降低 `core_utilization`，不是提高**（上一版曾写反） |

### 3.3 边界

- 仅针对 `recip_rnd_v21`、`recip_q60` 两个模块
- 不碰已通过的七个模块

### 3.4 验收标准

| 项 | 要求 |
|:---|:---|
| 门禁生效 | `place_opt` 失败时脚本明确中止，不再进 `clock_opt` |
| `CHK_UNPLACED` | 0 |
| 布线 | 分区全部走完且违规收敛，或明确记录未收敛 |

## 4. T-C：已完成的文本更正

| commit | 内容 |
|:---|:---|
| `d7c9a30` | `DRC`→`router DRC`；「物理不可行」→「预算内未完成」；删 95.8%；D2/D3 改「模块级、未实例化」；补 `recip_q60` 与 `recip_rnd` 的阶段区分 |
| `17cb80b` | 面积算术和不可求和；标注 `recon` 为 alpha 旁路诊断值；「2.68×」不可引用 |

## 5. T-D：foundry DRC / LVS / PG（未做，需授权）

### 5.1 当前状态（已核实）

`sched18_final.def` 的 `SPECIALNETS` 中：

```
 - VDD
   ( PIN VDD_PORT )
   ( * VDD )          ← 通配连接，无 ROUTED 几何
   + USE POWER ;
 - VSS
   ( PIN VSS_PORT )
   ( * VSS )
   + USE GROUND ;
```

`NETS` 段中 VDD/VSS 出现次数：**0**。

### 5.2 结论

**7 个通过模块的 DEF 均无 PG 布线几何。**
因此 `router DRC = 0` **不等于** foundry DRC clean，也不等于电源已连。

### 5.3 要做的事（如获批）

| 项 | 说明 |
|:---|:---|
| PG 布线 | `create_pg_*` / `pg` 流程，生成真实电源网格 |
| foundry DRC | 需要真实 DRC runset（本工程没有）|
| LVS | 需要版图提取与网表比对 |

## 6. T-E：ADC2 工程合同已裁定

[当前合同](../../CONTRACT_CURRENT.md)定义 13 bit、`[-0.1875, +0.5625) V`，窗宽 0.75 V。`391c55a` 将默认 Python 参数和 `ADI_40M` profile 改为该口径，v21 节点宏及重构常量继续使用 13 位整数合同。可从 [Python 默认值](../../../src/adi_model/defaults.py)、[profile](../../../src/adi_model/contracts/profiles.py)和 [ADC2 节点宏](../../../rtl/v21/adc2_node_macro.sv)追溯。

在名义增益 `G=32`、差分满幅 6 V 的条件下，输入折算步长为 `(0.75 / 8192) / 32 = 0.50 LSB20`。旧报告中的 0.60 LSB20 来自 12 bit、0.45 V 的不同组合。上述计算是码宽与窗宽的合同换算。端到端噪声、模拟余量和吞吐仍按各自验证结果登记。

## 7. 不得据此推进的事项

| 事项 | 状态 |
|:---|:---|
| SRAM 化 D5 | ❌ 未获授权 |
| 算法重构 | ❌ 未获授权 |
| 新一轮 PPA 试验 | ❌ 未获授权 |
| 重新开启自动 goal | ❌ 未获授权 |
| 重跑已通过七模块 | ❌ 不必要 |
