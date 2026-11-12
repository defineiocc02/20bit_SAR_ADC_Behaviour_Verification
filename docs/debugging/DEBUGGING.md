# 调试指南

每个案例给出**实际入口**和**该看哪个字段**，不写"请检查配置"。

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

三步定位：

1. `raw.main_masks` / `raw.sub_masks` 是**实际**送进阵列的掩码；
2. 与 `dem3d.unary_rotation_masks(geometry, codes, states, slice_ids)` 对拍；
3. 若只有某些码不对，先看是不是**编码边界**（17/5 下：主码 `m=k>>4` 在
   `m` 为偶数/奇数切换时会带上 B4，见 `_unit_switch_pattern`）。
   > 2026-11-13：原写 `n_main=63` 的 15×2C 边界，那是已废弃的 63+8 拓扑。

注意 `command.main_on` 的形状是 `[sample, active_slot, unit]`——
**沿 sample 求和得到的是 slot 统计，不是物理片统计**（本仓库曾因此出现"假覆盖"，
现已改为按 `slice_ids` 散列，见 `dem3d._per_physical_slice_counts`）。

---

## 4. dither 去扰后还有残余

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_dither_charge_port.py -q
PYTHONPATH=src python3 -m pytest tests/unit/test_dither_port.py -q
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

`raw.ra_sat`、`raw.adc2_over` 是逐笔布尔。任意一笔为真，静态训练资格即失效
（`static_domain_reasons` 会写明）。先查该笔的 `residue` 与 `vra`，
再看 `cfg.ra_clip_v` 和 `adc2_window_v` 是否与这条配置匹配。

---

## 7. 图与 README 数字对不上

```bash
PYTHONPATH=src:. python3 tools/check_engineering.py   # C4 会直接报
```

规则：图与表必须同 `points`/`repeats` 与同 `source_sha`。
已知的一处**合法差异**：含校准的行（A5/C1/C3）在图上用 `offset=0` 公共网格、
在矩阵里用留出偏移，数值不同但口径各自成立（见 `figures.json` 的 `F03.note`）。

---

## 8. Python 与 RTL 第一处差异

```bash
python3 -m verification.run_verification --level module --only recon_core
```

失败会打印：`module / vector index / field / python expect / rtl actual / 重放命令`。
先确认 `verification/reports/dumps/<module>/` 里的 dump 是对应那次运行的，
再拿 `vector index` 回 `verification/vectors/generated/<module>/` 取输入。

**别先怀疑 RTL**：本项目就出现过参考模型读错合同（`a` 当成 `on`）的先例。
两侧都可能是错的，先看合同正文。
