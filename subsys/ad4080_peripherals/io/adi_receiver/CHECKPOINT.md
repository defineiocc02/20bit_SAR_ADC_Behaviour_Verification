# ADI 收端互操作检查点

- 源版本冻结于 `f518dd124fc005c6f9cd80b7947b10a638c37b37`。ZedBoard 项目、包脚、复位和寄存器映射已静态核对。首轮执行路径只包含原版 ADI `ad408x_phy`、`ad_serdes_in` 和 `ad_pack` 及本包理想 7 Series 原语。
- 已跑 `run_verify.sh` 的原样离线源导出入口。单、双 lane 各两个首帧相位均实跑成功。官方源码仅在交付树外，上游散列、原理想测试台、边沿字与 transport 实际测试散列见 `handoff.json`。
- 位序与符号码正确，现有 mapper/transport 生产 RTL 无修改。全板时序、目标 FPGA 厂商宏模型、实际硬连线以及 ADC ASIC 电气行为须独立验证。
