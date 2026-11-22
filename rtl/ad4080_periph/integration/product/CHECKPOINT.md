# 产品绑定检查点

- 从原固定 Git 归档复用了 12 个 DSP 源。仅临时三个接缝文件变化，数值、SPI 与事件源码哈希一致。
- 首轮 lint 定位到 MBIST 恢复组合环，从 `link_idle` 经记录请求、清除回到记录资格。注册清除并在清除沿后应答，正常 lint 和综合通过。
- 记录产品首轮在 `/tmp/ad4080-product.0CO8K4` 验收 SPI→DSP→四银行→46 个位对、16K 满/掉电及 MBIST。
- 原服务壳和裸/整记录读回入口在 `/tmp/ad4080-peripherals.Rf39dn`、`/tmp/ad4080-readback-link.TRuhGP` 复跑通过。保护仓仍干净，生成的 DSP 副本位于各自临时目录。
- 校准档 `/tmp/ad4080-product.iw4wn3` 用实际 ADCToolbox CLI 计划完成 SPI 写读和两条完整留出记录。常规档 `/tmp/ad4080-product.hLvezl` 保留原 46 位对黄金，并验满史跨环地址 `3FFF → 0000`。两档经 Verilator、Yosys 后核对完整哈希。回执为 [常规](evidence/verification.txt)、[校准](evidence/calibration.txt) 和 [CLI 计划](evidence/calibration_plan.json)。
