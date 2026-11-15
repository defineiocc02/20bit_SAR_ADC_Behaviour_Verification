# 架构与数据流

一句话：**18 片物理电容池，每笔用 8 片采样、8 片转换；SADC 只判决，RDAC 跟着已决码走；
余差经共享 RA 放大 32 倍给 12 位 ADC2；20 位输出由定点权重重构。**

---

## 1. 分层

```
contracts/     类型、单位、码制、定点格式、profile          ← 谁都不依赖它以外的东西
topology/      电容网络、码本、电荷算子                     ← 只认电容和开关
control/       调度、DEM、dither、follower                  ← 只发数字命令
behavioral/    采样、参考、AZ、RA、ADC2、噪声               ← 只推进模拟状态
calibration/   特征、拟合、冻结系数、重构                   ← 只用观测
simulation/    装配 + 逐事件推进 + 结果                     ← 唯一运行链
metrics/       静态、频谱、噪声                             ← 只读
```

依赖只能**向下**：`simulation → behavioral/control/topology → contracts`。
`metrics` 和 `calibration` 不得回写仿真数据。
`tools/`、`experiments/`、`tests/` 不在核心包的 import 图里（`tools/check_engineering.py` 会查）。

---

## 2. 一笔样本的数据流

```
input_fn(t)
  │  sampler.capture()                        behavioral/sampler.py:68
  ├─→ x_sadc          = x − L + n_sadc        送量化器
  └─→ rdac_input_v    = x − T                 送 RDAC 采样底板
        │
        │  sadc.convert_codes()               behavioral/sadc.py
        ├─→ coarse[9b]  (0..511)
        │
        │  pool.select(code, dem_state, slice_ids)   simulation/slice_pool.py:158
        ├─→ main_on[8][17], sub_on[8][5]      物理单元开关（FIG.19 17/5）
        │
        │  pool.charge_from_masks()           slice_pool.py:172
        ├─→ charge_c                          参考电荷
        │
        │  q_residue = −q_free − charge_c (+ 噪声)
        ├─→ residue = q_residue / C_norm      输入折算余差
        │
        │  ra.evaluate(residue)               behavioral/ra.py:92
        ├─→ vra = G·residue + n_ra
        │
        │  adc2.quantize_codes(vra)           behavioral/adc2.py:41
        ├─→ adc2_code[12b]
        │
        │  reconstruction.reconstruct()       calibration/reconstruction.py:36
        ├─→ 浮点重构
        │
        │  FixedPointReconstructor            contracts/fixed_point.py:78
        └─→ 20 位 offset-binary 码 + flags
```

---

## 3. 状态归属

| 状态 | 谁持有 | 生命周期 |
|:---|:---|:---|
| `Config` / `DefaultParameters` | 调用方构造，运行期只读 | 整个 run |
| `SplitChip`（制造电容） | `DefaultModel` | 整个实例；同一 seed 决定 |
| `DigitalState`（增益、冻结系数） | 调用方传入或 `initialize_state` | 跨记录可复用 |
| `PhysicalSlicePool` 电气状态 | pool 自己 | **逐笔 acquire → release**，不跨笔 |
| `SampleCapture` | 每次 `capture` 新建 | 单笔 |
| `SimResult` | 引擎返回 | 只读产物 |

**没有可变全局状态。** 同一 `run_id` 重跑得到同一结果；不同 `run_id` 换噪声/调度流。

---

## 4. 时序（一笔）

| 阶段 | 做什么 | 关键约束 |
|:---|:---|:---|
| 采集 | 8 片同时采样，保存自由电荷 | 采集组 = 上上笔的转换组（流水） |
| 量化 | 3b flash + 6b SAR = 9b | 只判决，不驱动大盘 |
| RDAC 更新 | 按已决前缀逐事件加载 | **禁止先算最终码再倒推事件** |
| 余差 | 主/子节点电荷重分配 | 子节点浮置，β 桥接 |
| RA | ×32 放大 | 有单极点、有限建立 |
| ADC2 | 12b 量化 | 宽/窄带可选 |
| 重构 | 定点权重 | Q30 权重 / Q32 电压 / 96b 求检 / floor |

---

## 5. 三个角色必须分开

| 角色 | 在哪 | 能不能当 oracle |
|:---|:---|:---|
| 浮点行为模型 | `src/adi_model/` | **不能**：浮点给不出位真值 |
| 整数定点参考 | `verification/reference/` | 能，且不 import RTL |
| 独立 scoreboard | `verification/cosim/scoreboard.py` | 只比较，不生成期望值 |

---

## 6. 边界（明确不做什么）

* 不做后仿、PEX、硅测；所有数字是 **L1 行为级**。
* `recon_core` 只支持 AllSIGNAL 子集（`G = T`），不支持 dither 单元混入。
* 共享 RA **没有跨笔状态**，每笔按理想初态开始。
* 动态参考 / RA 建立 / ADC2 宽窄带可以跑波形，但**出不了 INL/SNR**（`out=NaN`，见 `pipeline_engine.py:594`）。
* CAAZ 在 `extensions/caaz/`，主链不 import。

---

## 7. 从哪读起

1. `simulation/pipeline_engine.py::_validate_request` — 先看它拒绝什么，就知道支持什么
2. `simulation/pipeline_engine.py::execute_pipeline` — 主链
3. `control/dem3d.py` — 掩码怎么生成
4. `calibration/reconstruction.py` + `contracts/fixed_point.py` — 数字怎么落地
