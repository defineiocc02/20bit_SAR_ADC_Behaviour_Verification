# 八片 dither 与 LMS 片上重构

`sar20_lms_recon` 接收同笔 ADC2 原码与八片实际底板掩码。配置端口在采样前顺序装入 396 个字，包含每片有效电荷权重和增益。转换时以实际转换主位、子阵列转换位加逻辑注入位（同时置位计两倍）计算补偿，输出 20 位有符号码后交给 CAAZ。训练可在片外进行，每颗芯片可独立装载系数。 [数值与时序合同](SPEC.md) · [整体流水](../task_integration/LMS_PIPELINE.md)

用 2%／3% 固定失配、dither OFF／ON 的 16,384 笔归档原码及实际掩码验证。RTL 与独立 Q28 整数参考全部相同。相对归档浮点行为，148 笔在输出阈值附近相差一位，最大绝对差为 1 LSB20。

| 失配 | Dither | −1 LSB20 | 相同 | +1 LSB20 |
| ---: | :--- | ---: | ---: | ---: |
| 2% | 关 | 23 | 4,071 | 2 |
| 2% | 开 | 49 | 4,044 | 3 |
| 3% | 关 | 22 | 4,063 | 11 |
| 3% | 开 | 11 | 4,058 | 27 |

额外测试覆盖全部 4,096 个 ADC2 中点、半开量程、负权重、同笔 ID、重复切片、错误配置、反压、清空和复位。正常 E0 入核后 E22 出结果。独立核接受间隔 13 拍的定向序列以及间隔 16 拍的连续归档样本。16 拍系统速率还依赖模拟宏按合同提交 ADC2 原码。 [实际观察收据](evidence/negative/result.json)

历史正注入版本的 `sar20_digital_lms_top` 已生成 SKY130HD GDS；当前负注入版本不能继承以下旧结果，须重新执行。采用同源 RTL 和 6.25 ns 约束，在 TT、25 °C、1.80 V 提取后复核中，setup 裕量为 +0.0805565 ns，hold 裕量为 +0.0430949 ns，违例端点为 0。G4 仍为 `BLOCKED`，多角 STA 和目标 180 nm BCD 验收待完成。 [运行清单](../task2/physical/evidence/sar20_digital_lms_top_manifest_42278312924.json) · [物理交付](../task2/physical/README.md)

原运行的独立公开 DRC 和 LVS 均达到 300 秒上限。对同一 GDS 延时复跑，公开 DRC 完成且违规为 0，采用上游修订公开规则 `5eebdc0` 的 LVS 顶层匹配。两项结果分别归档，整体物理状态仍为 `PARTIAL_SIGNOFF`。 [独立 DRC](../task2/physical/evidence/sar20_digital_lms_top_drc_manifest_42297095870.json) · [上游规则 LVS](../task2/physical/evidence/sar20_digital_lms_top_lvs_upstream_manifest_42296886745.json)

在仓库根目录使用 Python 3.12、NumPy 与 Verilator 重跑。命令同时导出两套可加载的 26 位顺序配置数据。

```bash
python -m task_reconstruction.tools.run_checks --out /tmp/task2-recon-check-001
```
