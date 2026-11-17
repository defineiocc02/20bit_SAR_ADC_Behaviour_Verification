# 调试指南

Python 的排查从当次输出 `metadata.json`、`codes.npz` 和 `assumptions.json` 开始。集成 RTL 的接口和位宽以 [v21 工程合同](../CONTRACT_CURRENT.md) 为准。

---

## 1. 一条输出都没有

先看 `raw.nominal_diagnostic_available` 与 `nominal_diagnostic_reasons`：
为 `False` 时 `out` 全是 NaN，这是**设计如此**，不是崩溃。

```bash
PYTHONPATH=src python3 -m adi_model simulate --samples 8 --output /tmp/d
python3 -c "import json;d=json.load(open('/tmp/d/metadata.json'));print(d.get('unmodeled'))"
```

常见原因：开了参考/RA/ADC2 动态（`pipeline_engine.py` 里 `diagnostic_reasons` 明确拒绝标量诊断）。

---

## 2. 样本丢失、重复或乱序

先查 `raw.plan_valid` 与 `raw.sample_id`。调度器要求 `sample_id` 连续：

```python
import numpy as np
bad = np.flatnonzero((np.diff(raw.sample_id) & 0xFFFF_FFFF) != 1)
```

`plan_valid=False` 的样本是**保留但无效**，不是被丢弃。计数守恒要对
`N_accepted = N_retired + N_cancelled + N_pending`，不能只数输出。

---

## 3. DEM mask 和预期不一致

逐笔核对 `raw.main_masks`、`raw.sub_masks`、`raw.k` 和 `raw.conv_slice_ids`。Python 默认模型用 `fig11_masks` 把 9 位码映射到 17 个主单元和 5 个子单元。主段码 `k >> 4` 优先使用 2C 单元，再以 1C 单元补余数。检查物理片使用率时按 `conv_slice_ids` 汇总，掩码数组的第二轴是每拍的活动槽位。[Python 掩码实现](../../src/adi_model/control/dem3d.py)

检查 v21 时，按实际 `k`、横纵相位和 guard 规则读 RTL 掩码。[工程合同](../CONTRACT_CURRENT.md) · [v21 掩码 RTL](../../rtl/dem_mask_v3.sv)

---

## 4. dither 去扰后还有残余

```bash
PYTHONPATH=src:flow python3 -m pytest -q \
  flow/tests/unit/test_dither_charge_port.py flow/tests/unit/test_dither_port.py
```

* 理想器件下 64 个字的 `out` 极差应 ≤ 1 LSB20；若大，先查 `total_v` 有没有被扣两次；
* `ra_sat` 计数非零说明余差被顶出 RA 摆幅——dither 幅度超出该配置的允许范围。

---

## 5. 校准矩阵秩不足

```
fit_unit_weights -> CalibrationUnidentifiableError
```

看三个数：`rank`、`condition`、训练样本覆盖的码数。
本仓库已确认：**逐单元权值在残差域不可辨识**（全节点残差 991 µV），
这不是 bug，是参数化问题；改用逐码表才有 10.5 LSB20 的效果。

---

## 6. RA / ADC2 越界

`raw.ra_sat` 标记残差放大器限幅，`raw.adc2_over` 标记 ADC2 节点越窗。定位到样本 ID 后，查看该笔的 `raw.residue`、`raw.vra` 和 `raw.adc2_input_voltage`。默认 ADC2 为 13 位，窗口 `[-0.1875, +0.5625) V`，节点步长为 `0.75 V / 8192`。`static_domain_reasons` 记录这次静态训练资格的判据。跨 Python 和 RTL 核对时，确认 C7 检查通过。[ADC2 工程合同](../CONTRACT_CURRENT.md)

---

## 7. 图与 README 数字对不上

```bash
python3 tools/check_engineering.py
```

C4 用 [figures.json](../figures/20261117/figures.json) 核对发布的 PNG/SVG SHA-256。C7 核对 ADC2 合同、Python 默认值、RTL 输入位宽、测试节点量化器及重构码箱中心。比较图与表的结果时，另外核对每项的采样数、输入网格、偏移与随机种子。图册中的校准图使用公共网格，消融矩阵的相应行使用留出偏移。[逐图说明](../figures/20261117/README.md)

---

## 8. Python 与 RTL 第一处差异

用同一 `tag` 对齐采集、残差返回和退休。先核对该笔的 `pre/post`、权重和、ADC2 原始码及返回标志，再比较 20 位结果和饱和标志。v21 的逐项测试台与命令见 [仿真入口](../../flow/sim/README.md)。复核旧组件数据时，输入向量和报告从记录中的 Git 原版查询。[组件验证](../../flow/verification/README.md)
