# 模块一 SKY130HD 物理交付

基础版和桥接版均在公开 SkyWater SKY130HD 完成布局布线、寄生提取和 GDS 导出，并在 **6.25 ns** 约束下通过布线后 setup 和 hold。执行源提交为 `f9ee2cda9e3b06833d0499ef0b721f52b69d2e34`。 [GDS、网表、DEF、ODB、SPEF、SDC 和原始时序报告](evidence/module1_pair_sky130hd_run_42250003484.zip) · [源文件和产物哈希](evidence/sky130hd_pair_manifest_42250003484.json) · [实际运行](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/42250003484)

| 顶层 | Setup 最小裕量 (ns) | Hold 最小裕量 (ns) | Setup／hold WNS (ns) | Setup／hold TNS (ns) | 违例端点 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `module1_top` | +0.00140243 | +0.0804195 | 0／0 | 0／0 | 0 |
| `module1_bridge_top` | +0.25808 | +0.156082 | 0／0 | 0／0 | 0 |

基础版 setup 只剩约 1.40 ps 的正裕量。本表采用 `sky130_fd_sc_hd__tt_025C_1v80.lib`（TT、25 °C、1.80 V），为同一典型工艺角下的提取后检查。详细路由 DRC 和天线违规均为 0，最终 VDD 和 VSS 网格连通。独立 DRC 和 LVS 状态为 `NOT_RUN`。

物理复验保留原 RTL、6.25 ns 时钟、I/O 延时和 5 fF 输出负载。相对上一轮唯一物理参数变化为 ORFS 修复预留裕量 `SETUP_SLACK_MARGIN` 从 0 ns 调为 0.15 ns，实际验收继续读取未改动的 SDC。 [SDC](sky130_constraint.sdc) · [各轮 SKY130HD 对照](SKY130HD.md) · [等价优化与验证](SELECTOR_OPTIMIZATION.md)

从交付仓库根目录使用 Linux amd64 和 Docker 复跑。每个输出目录须为空。

```bash
bash task1/physical/sky130_run.sh /tmp/task1-sky130hd-base module1_top
bash task1/physical/sky130_run.sh /tmp/task1-sky130hd-bridge module1_bridge_top
```

[Nangate45 历史归档](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/b99977a906e9af1848467277530c98f01bee2a3c/task1/physical/README.md)
