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
| T-A | 重构负数误报与跨笔错码 | RTL 修复 | 已执行 | **本地 Verilator 定向回归通过** |
| **T-B** | `place_opt` 门禁 + 降低利用率/扩大 core 后重试 | **重跑 EDA** | ✅ | 中 |
| T-C | 已完成的文本更正（DRC 口径、未实例化、面积、失败原因）| 文本 | ❌ | **已完成** |
| **T-D** | 补 foundry DRC / LVS / PG 布线 | **新流程** | ✅ | 待定 |
| T-E | ADC2 位宽及窗口裁定 | 合同 | — | **已裁定为 13 bit、0.75 V** |
| T-F | v21 顶层综合及同版网表 | 重跑 | ✅ | 待办 |

<a id="recon-s3"></a>
## 2. 重构负数后的中点码

当前生产模块从本笔的 N 和 I 求出 96 位有符号 X。仅当高 64 位等于第 31 位的符号扩展时，X 可以无损截成 32 位。数据、错误标志和标签随同一个有效沿寄存，空拍只清有效位。[生产 RTL](../../../rtl/v21/recon_v21.sv)

2026-11-19 在本地用 Verilator 运行相同激励。缺陷基线取自 Git 提交 `9e4f1fd`，工作树保留修正后的生产实现。

| 试验 | 基线实测 | 修改后实测 |
| --- | --- | --- |
| 负数后紧接中点，依次输入 raw 2046、2048 | tag 602 应返回 `0x80000`，实际返回零并置错 | A 返回 `0x7FFFF`，B 返回 `0x80000`，两笔 err 均为 0 |
| 负数至中点之间插入 1 或 10 个空拍 | 两次中点均误报 | tag 702、704 均返回 `0x80000` |
| 7 组共 19 笔端口事务 | 5 笔的码和 err 同时错，均按原 tag 退休 | 19 笔逐级整数、出码、饱和、错误、标签和延迟全部符合期望 |
| 原有 8,209 笔冻结向量 | 3 笔错码并误报 err，共 6 处差异 | 8,209 笔全部退休，8,213 项检查通过 |

中点用例的 G=32、Wm=16、Wp=0（权重采用 Q30），α=1，b 的 Q24 整数为 768。两个正确的 X 分别是 −96 和 0。从受理沿到返回沿相差 6 个周期，共经过 7 个寄存级。测试台另在 N/I 汇合处施加 6 个内部探针，核对 −2³¹ 和 2³¹−1、两个紧邻越界值、上游真错及随后的正常码。探针全部通过。[输入与独立整数参考](../../../tools/recon_oracle.py) · [逐拍测试台](../../../tb/v21/tb_recon_stream.sv) · [冻结码测试台](../../../tb/v21/tb_i4_recon.sv)

在仓库根目录运行以下命令。Verilator 和 clang++ 须在 PATH 上，也可用 `VERILATOR` 指定本机可执行文件。输出目录包含源码快照、原始轨迹与逐标签报告。

~~~bash
recon_run="$(mktemp -d)"
bash tools/pyenv.sh --run tools/run_open_rtl.py --simulator verilator \
  --tops tb_i4_recon tb_recon_stream --out "$recon_run/results"
cat "$recon_run/results/tb_recon_stream/compare.log"
~~~

随后按 [Issue 10 的原始两笔](../ISSUE_10.md)补入第八组，现行入口共检查 21 笔，上表保留首次七组对照的实测口径。

本项实测覆盖重构模块的 RTL 端口与所列内部边界。VCS 全套回归和修改后的网表等价未在本次运行。

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
