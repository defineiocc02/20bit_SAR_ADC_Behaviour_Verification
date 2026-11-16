# 已作废的历史版本

**本目录里的任何结论都不得引用。**

这里的文件记录的是开发过程中的中间状态，其中的数字大多已被后续工作推翻。
保留它们只为追溯"某个结论是怎么来的、又是怎么被推翻的"。

## 目录内容

| 文件 | 为什么作废 |
|:---|:---|
| `TIMING_BUDGET_FINAL_superseded.md` | 含三代网表的逐代演进数字（含 108,951 / 97,555 µm²）。当前版本无同版网表，所有 PPA 数字不成立 |
| `GATE_EQUIV_superseded.md` | 基于错误网表的"1464 拍差异 0"结论；且 1464 是日志行数，仅 90 观察沿、2 笔输出 |
| `I5_PORTS_superseded.md` | 端口表按旧的 14,400 根权重端口写；权重表已改为内部寄存器堆 + 52 位配置口 |
| `DRIVING_CELL_BUG_superseded.md` | 记录的问题真实（`BUF_X2` 不存在、`UID-993` 静默失败），但引用的面积数字已作废 |
| `AREA_VS_PPT.md` | 面积对比基于作废网表 |
| `PPA_DIV_VS_RECIP.md` | PPA 对比基于作废网表 |
| `FINAL_D1_D6_superseded.md` | 已被 `../STATUS.md` 取代（那份是 `054cfed`，本份是更新后的单一权威版） |
| `CHECKPOINT_I5_SYNTH_superseded.md` | 旧综合状态，已被 `../SYNTH_STALL.md` 取代 |
| `CHECKPOINT_I5_SYNTH_v2_superseded.md` | 同上 |
| `CHECKPOINT_I5_FC_superseded.md` | Fusion Compiler 尝试记录，未成功 |

## 现行文档

回到 [`../STATUS.md`](../STATUS.md)（唯一权威状态）。
