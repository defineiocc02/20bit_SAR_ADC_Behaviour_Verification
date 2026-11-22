# 数字 IP 验证排查

正常运行的 `metadata.json.profile` 为 `adc_digital_ip_v21`。选择一笔 tag，依次核对 `word → pre_slots → event_k → post_slots → adc2_code → code`。生产 Python 和实际 RTL 的逐字段命令见[快速上手](QUICKSTART.md)。

## 没有有效输出

先查 `out_valid` 和 `rdac_over`。外部输入越量程可能使 `k=c+U` 超过 520，未形成最终转换快照，这一行仍保留。若 `out_valid=1` 且 `out_err=1`，检查这笔实际加载的 G、Wm、Wp、偏置和有符号截断。ADC2 越窗时原码回零，`adc2_over` 单列，读取者要连同该标志解释出码。[重构边界](../../src/adi_model/digital_conversion.py)

## 样本或开关对不上

`sample_id` 从 0 起，硬件 `tag` 从 1 起，`qid` 按受理顺序 0、1 交替。先核对字源是否强制、片池是否重播种，以及来自上游的 h、v、half、coin。在 `acquisition_ids` 中找物理片，再按同一逻辑槽读四位 `pre_slots` 与二十位 `post_slots`。末级出码相同也可能掩盖两个等权单元接反。[片选与掩码图](../algorithms/digital.md#dem)

`event_count` 范围内，`event_k` 列出每次比较对应的实际写命令。拒绝 512 时为 −1，其后从 bit5 继续。沿前值被新事务提前覆盖、宏返回时机或反压错误，需要用硬件引脚轨迹定位。[顶层时序对照](../audit/ISSUE_10.md)

## 校准与研究数据

给模拟侧的 `device_weights` 以 Ceff/Cf 为单位；数字侧 `weights_q30` 是 18×20 的整数表。核对“同片换表”时，两组原码和物理开关应保持一致。模拟器系数表哈希和加载表哈希分别保存在 metadata 中。[权重配置](../v21/CONFIG_CONTRACT.md)

Fig.11 研究记录另有 `raw.main_masks`、`raw.residue` 和 `static_domain_reasons`，按其图注中的电荷模型和工况追溯。[Fig.11 模型](../../experiments/fig11.py) · [已发布行为图](../figures/behavioral/README.md) · [原文与当前工程的差异](../audit/AUDIT_ALGO_VS_SOURCE.md)
