# 八切片扰动及整体数字顶层的 SKY130HD 实现

`dither8_top` 实现八切片的扰动开关控制、调度、DEM 掩码和同笔去扰元数据。`sar20_digital_top` 再接入模块三的 CAAZ 配对与控制。ADC 前级、采样开关、残差放大器、ADC2 及外部重构／LMS 权重由数模联合验证提供。物理运行只覆盖所列数字 RTL。[八切片合同](../eight_rtl/SPEC.md) · [整体顶层源码](../../task_integration/rtl/sar20_digital_top.sv)

后端使用固定镜像的 Linux amd64 OpenROAD/Yosys/KLayout。两顶层沿用模块一的 6.25 ns 单时钟 SDC、I/O 延时、5 fF 负载，以及 SKY130HD TT、25 °C、1.80 V 工艺角。布局初值为 450 × 450 µm，核心框为 (20, 20) 至 (430, 430) µm。P&R 每顶层使用 4 线程，CI 设 40 分钟上限。[SDC](../../task1/physical/sky130_constraint.sdc) · [运行计划](plan.json)

在 Linux amd64 Docker 环境中，从干净的目标提交运行。输出目录需要为空。

```sh
bash task2/physical/sky130_run.sh /tmp/task2-dither8-sky130hd dither8_top
bash task2/physical/sky130_run.sh /tmp/task-integrated-sky130hd sar20_digital_top
```

物理脚本先对照提交冻结 RTL、配置与 SDC，然后运行综合、预布线 STA、布局、CTS、路由、提取及 GDS。每个布局出口检查电源网实际图形、通孔和每个标准单元电源引脚，抽取前另查填充后的数据库。最终 setup／hold 从同一份 ODB、SDC 和 SPEF 重开复核。独立 DRC／LVS 采用镜像所带的公开 deck，脚本保留退出码与报告，按实际运行结果判定。交付包记录 GDS 顶层、同一 GDS 交给 PV 的摘要、输入文件和全部产物的 SHA-256。

每个运行以 `moscow.json`、`PROVENANCE.json` 和 `release_manifest.json` 记录当前证据范围。目标 180 nm BCD、独立多角、流片方规则、EM/IR 与模拟验证分别验收。
