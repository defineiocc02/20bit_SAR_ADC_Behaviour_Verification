# profiles

具名目标、拓扑与实验参数的**数据来源**在 `src/adi_model/contracts/profiles.py`。
本目录只放说明，避免出现第二份会漂移的参数。

| profile_id | 用途 | 关键值 |
|:---|:---|:---|
| `adi_isscc2024_40m_6vpp` | ADI 主复现 | 40 MS/s、±3 V、18/8、20.5 pF、63+8、9b/20b |
| `engineering_10m_10vpp` | 工程 10 MS/s 变体 | 10 MS/s、±5 V、17+5、1 pF、12b ADC2 |
| `patent_fig19_7slice` | dither 专利七片机制例 | 7 片、6-bit 字；只用于机制试验 |

规则：
- 运行时必须显式 `load_profile(name)`；未知名字报错，不回退默认。
- `assert_caaz_free()` 保证主 profile 不带 CAAZ。
- `topology_id` 与 `profile_id` 必须同时落盘；不同几何/系数不得静默互换。
