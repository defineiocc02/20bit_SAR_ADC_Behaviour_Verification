# ADR 0004 — 第一级量化器栅格的唯一真相源

- 状态：**已采纳**
- 关联：审计 A02（slice 调度）/ B16（两条主循环各自解释接口）
- 影响模块：`sadc.py`、`sim_split.py`、`pipeline.py`
- 回归测试：`tests/integration/test_pipeline_equivalence.py`

---

## 1. 问题

工程里有**两条独立的主循环**，实现同一条信号流：

| 模块 | 拓扑 | 角色 |
|:---|:---|:---|
| `pipeline.py` | 分段（split） | 相序精确的 v6 流程 |
| `sim_split.py` | 等权 / 分段 | 独立实现的对照链 |

它们必须**逐位一致**（这是"两条实现互相验证"的全部价值）。但 v6.1 里，
两条链**各自**推导第一级量化器的阈值步长：

```python
# pipeline 一侧：从 DAC 子阵列推
step = n_sub * step0
# sim_split 一侧：从配置读
step = cfg.delta1
```

`b1 = 6` 时 `n_sub * step0 = 8 × 11.719 mV = 93.75 mV`，而
`cfg.delta1 = 2·v_fs / 2^6 = 93.75 mV` —— **数值巧合相等**。分歧因此被
掩盖了整整两个版本。

审计 A01 把第一级读到 `b1 = 7` 之后，两式变成 93.75 mV vs 46.875 mV，
**差 8 倍**。stage19/20 立刻出现 2.9 V 量级的输出误差 ——
"两条链逐位一致"的测试第一次失败，暴露了分歧的真实存在。

## 2. 决定

把"第一级一个判决步是多少个 DAC 单位步"收敛到**一个函数**，两条链都必须
调用它，不得自行推导：

```python
# src/adi_model/sadc.py

def units_per_first_stage_step(cfg, dac=None) -> int:
    """第一级一个判决步长 = 多少个 DAC 单位步。唯一换算关系。"""
    if dac is None:
        return max(int(cfg.units_per_lsb1), 1)          # 等权：n_units_sig // 2**b1
    return max(int(getattr(dac, "levels")) // (2 ** cfg.b1), 1)   # 分段：DAC 电平数 // 2**b1


def build_first_stage_quantizer(cfg, dac=None) -> SADC:
    """构造第一级量化器的唯一入口。"""
    if dac is None:
        return SADC(cfg)                                 # 理想栅格 -v_fs + c*delta1
    v_lo, v_hi = dac._nominal_endpoints()
    step0 = (v_hi - v_lo) / (int(dac.levels) - 1.0)
    unit = units_per_first_stage_step(cfg, dac)
    thr = v_lo + np.arange(2 ** cfg.b1 + 1) * (unit * step0)
    return SADC(cfg, thresholds=thr)
```

分段拓扑下的阈值对齐 **DAC 自己的名义栅格**，于是
`k_eq = coarse · units_per_d1` 恰好落在阈值上，残差被限制在
`±units_per_d1·step0/2` 之内 —— 这正是"一个第一级判决步的一半"。

## 3. 后果

1. **退化等价恢复，并且这次是结构性的。** 三条链在全非理想关闭时
   `max|Δout| = 0.0`（逐位一致），见
   `tests/integration/test_pipeline_equivalence.py`。
2. **量化的"两个接口"不再存在。** 任何未来新增的链，只要调用
   `build_first_stage_quantizer` 就不可能再走偏；想走偏必须**显式**绕过
   唯一入口，那是一个可被 review 的动作。
3. **`b1` 成为唯一的自由度。** 换算关系由 `b1` 与 DAC 电平数完全决定，
   与 ADR 0003 的读数自洽判据互相印证。
4. **DAC 误差的建模位置被固定。** `pipeline.SlicePool.acquire_quantizer`
   负责量化器自身的采样 kT/C 与建立误差（独立量化器 sDAC，
   `cfg.sadc_cap_ratio × 活跃电容`）；`sampler.py` 负责 x_S 通路。
   职责边界写进了模块 docstring，避免再次出现"同一误差在两条链里被
   重复或遗漏"。

## 4. 复核方式

```bash
pytest tests/integration/test_pipeline_equivalence.py -q

PYTHONPATH=src python -c "
from adi_model import Config
from adi_model.sadc import units_per_first_stage_step, build_first_stage_quantizer
from adi_model.dac_arch import build_split_chip, SplitDAC
for name, cfg in (('b1=7', Config()), ('b1=6 (legacy)', Config.legacy_v61())):
    c = Config(**{**cfg.__dict__, 'dac_arch': 'split'})   # 分段拓扑示例
    chip = build_split_chip(c); dac = SplitDAC(c, chip)
    q = build_first_stage_quantizer(c, dac)
    print(name, 'unit =', units_per_first_stage_step(c, dac),
          '| dac.levels =', dac.levels, '| thr[1]-thr[0] = %.4f mV'
          % ((q.thresholds[1] - q.thresholds[0]) * 1e3))
# -> b1=7  unit=4  512 levels  46.15 mV（= Δ1，对齐 DAC 名义栅格）
# -> b1=6  unit=8  512 levels  92.31 mV
"
```
