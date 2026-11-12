# `adi_model.metrics` —— 静态、频谱与噪声指标

## 0. 这个模块做什么

只读 `SimResult` 计算指标，**不修改记录、不重训、不剔除坏样本**。

## 1. 指标口径（容易混，必须分清）

| 指标 | 本仓库定义 | 不能声称 |
|:---|:---|:---|
| `inl_max_lsb20` | 256 电平的平均传输误差，端点拟合后取残差 | 不是逐码 INL |
| `dnl_bin_lsb20_max` | 斜坡步长分箱差分（每步 16 384 LSB20） | **不是** 20 位码密度 DNL |
| `sndr_db` / `snr_db` | 前者含谐波，后者扣掉 | 不要只引一个 |
| `nsd_v_rthz_dr` | 由 DR 反推的噪声密度 | 不是直接测的 |

`dnl_bin_note` 字段每次都写明"not 20b code-density DNL"。

## 2. 单一实现

`metrics_lsb20.py` 曾是 `metrics.py` 的独立副本（17/17 同名，14 个逐字节相同）。
现已收成薄转发，公式只剩一处。等价性由 `tools/check_metrics_equivalence.py`
七入口摘要前后对照证明。

## 3. 统计边界

* 单 seed、单制造实例、256 电平 × 32 次；
* **没有**置信区间，不把单点标准误当峰值误差上界；
* 状态平均与逐码线性度分开报告。

## 4. 缺口

| 项 | 状态 |
|:---|:---|
| 码密度 DNL | 未实现 |
| INL 逐码 | 未实现（只有平均传输） |
| 统计收敛 | 未做多 seed 稳健性 |

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_fixed_point.py -q
PYTHONPATH=src python3 tools/check_metrics_equivalence.py --json /tmp/m.json
```
