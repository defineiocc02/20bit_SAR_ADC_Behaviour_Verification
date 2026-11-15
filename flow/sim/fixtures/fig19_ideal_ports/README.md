# FIG.19 ideal-ports fixture —— **63+8 时代的遗留向量集**

> ⚠️ **本目录的几何是 63+8，与当前主链（专利 17/5）不匹配。**
> 它保留作为 2026-09 那轮 RTL 对拍的历史证据，**不是当前模型的 golden 向量**。

## 它是什么

`US10505561B2` FIG.19 的 dither 端口定义在当年那版实现上的**名义向量集**：
binding、config、weights、prefix table、参考码流等一整套，全部按
`n_main=63, n_sub=8, adc2=12bit` 构造。

manifest 里已如实写明：

```
"63+8 canonical geometry"
"ADC2 window/12bit/uniform_midrise"
```

## 为什么不能改一个字段就复用

整套文件（约 44 KB 的 prefix table、17 KB 的 weights、16 KB 的 binding）
都依赖 63/8 的位宽与权重表。当前实现是 17/5（主阵列 15×2C + B4 1C + S4 1C；
子阵列装配 4 列），位宽与权重全变 ⇒ **必须整套重新生成**。

## 为什么没有重新生成

**本目录没有生成器脚本**（这批文件是当时一次性产生的）。
且本机没有 VCS 环境，无法端到端验证新向量是否正确。

在缺验证手段时造一套"看起来能用"的新 fixture，比保留旧的更危险——
旧的在 manifest 里写着自己的前提，新的会让人以为它代表当前实现。

## 当前还有什么依赖它

| 使用方 | 状态 |
|:---|:---|
| `tests/integration/test_source_bound_replay.py` | 需 VERILATOR，本机跳过 |
| `tools/run_fig19_rtl_calibration.py`（默认 `--fixture-dir`） | 需 VCS，本机不跑 |

两者在本机都不执行，所以不会因为这份遗留 fixture 产生虚假结论。

## 要用它跑当前模型需要做什么

1. 把 63/8 换成 17/5 的位宽与权重（`DefaultParameters` 是唯一真源）；
2. 重新生成 prefix table 与参考码流；
3. 用一套可执行的 RTL 对拍环境验证新向量与 RTL 一致。

在此之前，**不要引用本目录的数字作为当前模型的结果**。
