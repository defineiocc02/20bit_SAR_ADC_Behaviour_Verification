# 模块一 SKY130HD 物理交付

两顶层均在公开 SkyWater SKY130HD 完成布局布线、寄生提取和 GDS 导出。验收库为 `sky130_fd_sc_hd__tt_025C_1v80.lib`（TT、25 °C、1.80 V），时钟周期为 6.25 ns，每个输出负载为 5 fF。完整输入延时、输出延时和时钟不确定度见实际 [SDC](sky130_constraint.sdc)。

| 顶层 | Setup 最小裕量 (ns) | Setup WNS／TNS (ns) | Hold 最小裕量 (ns) | Hold WNS／TNS (ns) |
| --- | ---: | ---: | ---: | ---: |
| `module1_top` | −0.0469957 | −0.0469957／−0.0469957 | +0.103554 | 0／0 |
| `module1_bridge_top` | +0.0667231 | 0／0 | +0.0898763 | 0／0 |

`e691c1b` 的桥接版通过 6.25 ns 的 setup 和 hold；基础版 hold 通过，setup 剩一个违例端点。两版详细路由 DRC 和天线违规均为 0，VDD 和 VSS 网格连通。独立 DRC／LVS 为 `NOT_RUN`。 [GDS、网表、SPEF、SDC 和完整时序报告](evidence/module1_pair_sky130hd_run_42240955810.zip) · [源文件与产物哈希](evidence/sky130hd_pair_manifest_42240955810.json) · [运行日志](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42240955810)

从交付仓库根目录使用 Linux amd64 和 Docker 复跑，每个输出目录须为空。

```bash
bash task1/physical/sky130_run.sh /tmp/task1-sky130hd-base module1_top
bash task1/physical/sky130_run.sh /tmp/task1-sky130hd-bridge module1_bridge_top
```

[各轮 SKY130HD 对照](SKY130HD.md) · [等价优化与验证](SELECTOR_OPTIMIZATION.md) · [Nangate45 历史归档](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/b99977a906e9af1848467277530c98f01bee2a3c/task1/physical/README.md)
