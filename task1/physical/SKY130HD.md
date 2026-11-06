# 模块一 SKY130HD 后端基线

`module1_top` 已在公开 SkyWater SKY130HD 上完成映射、布局、CTS、布线、RC 提取和 GDS 导出。源码提交为 `b99977a906e9af1848467277530c98f01bee2a3c`。布线后 STA 使用同一轮 ODB、SDC 与 SPEF，并复核了时序汇总和最大、最小路径。库为 `sky130_fd_sc_hd__tt_025C_1v80.lib`，即 TT、25 °C、1.80 V。 [交付包](evidence/module1_sky130hd_run_42222781821.zip) · [哈希清单](evidence/sky130hd_baseline_manifest.json) · [Actions 运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42222781821)

时钟周期 6.25 ns，按 100 ns 采样槽内 16 个数字节拍建模。输入延时为 0.20 至 0.80 ns，输入转换时间为 0.10 ns。输出延时为 −0.20 至 0.80 ns，每个输出负载为 5 fF。setup、hold 时钟不确定度分别为 0.10、0.05 ns。 [实际 SDC](sky130_constraint.sdc)

| 抽取后 STA | 最小裕量 (ns) | WNS (ns) | TNS (ns) | 违例端点 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| Setup | −7.33082 | −7.33082 | −1562.11 | 217 | FAIL |
| Hold | +0.132227 | 0 | 0 | 0 | PASS |

最差 setup 路径从切片调度器的 `lfsr_state_o[31]` 到 `lfsr_state_o[3]`。当前的扫描起点进入候选选择、容量判断和 LFSR 更新使能，导致该路径穿过串行选择逻辑。6.25 ns 下 setup 仍需优化。详细路由终值为 0 条路由 DRC 和 0 个天线违规，最终 VDD、VSS 网格均通过连通检查。独立 DRC 和 LVS 状态为 `NOT_RUN`。

GDS 字节数为 10,709,372，SHA-256 为 `4a37630a8c002e57ef8248108bd128a6c59ac9fc1225f74e1e0b94e61601cdd8`。交付 ZIP 保留原始 GDS、DEF、ODB、SPEF、网表、SDC、报告、日志和许可证。30 个产物与 10 个输入均按运行清单逐项复核。

Linux amd64 的 Docker 环境可用同一入口复跑。

```bash
bash task1/physical/sky130_run.sh /tmp/task1-sky130hd module1_top
```

## 桥接候选的初次布线

`module1_bridge_top` 采用同一工艺角和 I/O 约束，源码提交为 `1d60f09d70f1112c7e03fe4581df2681cae94717`。该轮仍使用原串行选择器，独立 GDS 和抽取后报告已归档。 [桥接交付包](evidence/module1_bridge_sky130hd_run_42225745199.zip) · [哈希清单](evidence/sky130hd_bridge_initial_manifest.json) · [Actions 运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42225745199/job/127918666302)

| 6.25 ns 抽取后 STA | 最小裕量 (ns) | WNS (ns) | TNS (ns) | 违例端点 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| Setup | −8.00048 | −8.00048 | −1761.04 | 352 | FAIL |
| Hold | +0.0949691 | 0 | 0 | 0 | PASS |

详细路由最终记录 0 条路由 DRC、0 个天线违规，VDD、VSS 网格连通。独立 DRC 和 LVS 为 `NOT_RUN`。桥接 GDS 为 11,717,960 字节，SHA-256 为 `3fcd73c9fcd5adac7ce7941f9420063bab444c885f80ad465b6e8bee1d6ba552`。30 个输出和 12 个输入均已核对。

## 并行选择器的第一次布线复验

`7425f2468233bfc6f02658c33aec7d7054fe3c20` 使用已证明等价的并行选择器。两个顶层各自重新完成实际布局布线、GDS 与 STA，仍按 6.25 ns、TT 25 °C／1.80 V 和相同 I/O 预算验收。 [两版 GDS／STA](evidence/module1_pair_sky130hd_run_42228369073.zip) · [哈希清单](evidence/sky130hd_selector_pair_manifest.json) · [Actions 运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42228369073)

| 顶层 | Setup WNS (ns) | Setup TNS (ns) | Setup 违例端点 | Hold 最小裕量 (ns) | Hold TNS (ns) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `module1_top` | −2.46329 | −21.9159 | 59 | +0.160036 | 0 |
| `module1_bridge_top` | −1.56883 | −6.58757 | 47 | +0.114057 | 0 |

两版 hold 通过，setup 仍有违例。最差路径均转移到转换片 ID 的合法性判断，再到 `conv_valid_o`。详细路由 DRC 和天线违规均为 0，最终 VDD／VSS 网格连通。独立 DRC 和 LVS 为 `NOT_RUN`。

## 并行合法性与 one-hot 游标的布线复验

`84410a4898929fa5f57960cfd325696d39b02771` 的两版布局和约束与前述验收口径一致，使用该轮最终 ODB、SDC、SPEF 重新分析 setup 和 hold。 [两版 GDS／STA](evidence/module1_pair_sky130hd_run_42232783534.zip) · [哈希清单](evidence/sky130hd_pair_manifest_42232783534.json) · [Actions 运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42232783534)

| 顶层 | Setup WNS (ns) | Setup TNS (ns) | Setup 违例端点 | Hold 最小裕量 (ns) | Hold TNS (ns) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `module1_top` | −0.546307 | −25.0623 | 58 | +0.0839664 | 0 |
| `module1_bridge_top` | −0.672571 | −28.8566 | 65 | +0.102683 | 0 |

两版 setup 仍有违例，hold 通过。最差 setup 已转移到采集组 ID 选择，基础版从 `eligible_i[13]` 出发，桥接版从 `shuffle_i` 出发。详细路由 DRC 和天线违规均为 0，最终 VDD／VSS 网格连通。独立 DRC 和 LVS 为 `NOT_RUN`。
