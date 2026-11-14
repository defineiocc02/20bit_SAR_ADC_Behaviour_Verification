# W0-C 公共接口、单位、事件与定点合同（草案）

> 这是**草案**，用于冻结讨论；不是生产代码。字段名可调整，语义/单位/所有权必须先定。
> 依据：main `Config/DefaultParameters/SimResult`、task `ExperimentConfig/codes.py/capacitors.py`、论文 Fig.9.8.1–3、专利 claim 1/2/7/8/12。
> 原则：**数字控制只能消费可观测判决/原码/已知控制/已加载系数**；plant 真值只给调试/独立参考。

---

## 1. 身份与 profile

```python
@dataclass(frozen=True)
class TopologySpec:
    topology_id: str          # 例：adi_isscc2024_40m_6vpp / eng_10m_10vpp / pat_fig19_7slice
    profile_id: str           # 运行 profile；不得静默换几何/系数
    n_slices_pool: int        # 18
    n_slices_active: int      # 8
    n_main: int               # main 的 63（或 task 的 17）
    n_sub: int                # main 的 8（或 task 的 5）
    c_unit_f: float
    c_bridge_f: float
    c_feedback_f: float
    c_parasitic_main_f: float
    c_parasitic_sub_f: float
    codebook_id: str          # uniform512 / fig11 / fig21
    mask_bit_order: str       # "slice_major_lsb_first" 等
    slot_to_slice_rule: str   # 逻辑 slot→物理 slice 的映射规则
    source_ids: tuple[str, ...]  # 论文/专利/PPT 行号
```

**硬规则**
- `topology_id` 必须随每次运行落盘；`profile_id` 不得由代码"猜"。
- main 的 `63+8` 与 task 的 `17+5` **不是同一布局**，不能共用 `topology_id`。
- `n_slices_pool=18` 是论文披露；`n_main/n_sub` 是工程假设，必须标 `ASSUMED_GEOMETRY`。

### 1.1 三个 profile

| profile_id | 来源 | 量程/速率 | 关键几何 | 用途 |
|:---|:---|:---|:---|:---|
| `adi_isscc2024_40m_6vpp` | ISSCC 2024 9.8 | 40 MS/s、±3 V（6 Vpp） | 18/8、20.5 pF、63+8、9b/20b | ADI 主复现 |
| `engineering_10m_10vpp` | task 任务书 | 10 MS/s、±5 V（10 Vpp） | 18/8、1 pF、17+5、2%/3% | 工程变体 |
| `patent_examples` | 专利 Fig.19/AUX/TRACK/参考 | 机制级 | 七片/八片、2D dither | 独立机制试验 |

---

## 2. 时间与事件

```python
@dataclass(frozen=True)
class EventRecord:
    time_s: float             # 模拟秒
    tick: int | None          # RTL tick；模拟事件可空
    domain: str               # "analog" | "digital" | "coarse" | "fine"
    phase: str                # S/C0/C1/RA/C2/AZ/...
    sample_id: int            # uint32，允许回绕
    epoch: int                # 配置代
    owner: str                # "sadc" | "rdac" | "ra" | "adc2" | "caaz"（扩展）
    kind: str                 # 见下表
    payload: dict             # 类型化；禁止裸 ndarray
    order_key: tuple[int, int]  # 同刻排序（先采样孔径，后开关；或反）
```

**事件清单（最小集）**

| kind | 产生者 | 消费者 | 关键字段 |
|:---|:---|:---|:---|
| `decision_valid` | SADC/Cmp | 数字 | code/bit、ready_time |
| `pretrack_start/stop` | 数字 | 输入网络 | target_slice_ids、窗口 |
| `acquisition_start` | 数字 | sampler | sample_id、slice_ids |
| `aperture` | sampler | 全体 | 采样时刻、共享沿 |
| `rdac_update` | 数字 | RDAC | k/mask、sample_id |
| `reference_switch` | 数字 | 参考 | 高/低轨、负载事件 |
| `az_capture` | 数字 | RA | AZ 代、sample_id（普通 AZ） |
| `ra_start` | 数字 | RA | 输入/输出初值 |
| `adc2_bw_switch` | 数字 | ADC2 | 宽/窄带 |
| `adc2_aperture` | ADC2 | 数字 | code、valid |
| `retire` | 数字 | 输出 | code、flags |

**同刻规则**：采样孔径与底板开关同刻时，**孔径先于开关**；数字 `sample_accept` 必须早于孔径至少 `t_setup`。16 拍模板是工程接口，不是原芯片时间事实。

---

## 3. 样本事务

```python
@dataclass
class SampleContext:
    seq: int                     # 内部单调序号
    sample_id: int               # RTL uint32
    epoch: int
    quantizer_id: int            # SADC/量化器实例
    physical_slice_ids: tuple[int, ...]  # 有序，长度=8
    logical_slots: tuple[int, ...]       # 不等于 physical
    dither_word: int             # 6 bit；本笔冻结
    dither_word_valid: bool
    dem_state: int               # 本笔冻结；不得读全局新状态
    accept_time_s: float
    aperture_time_s: float
    conversion_sample_id: int
    fine_sample_id: int
    calibration_id: str
    valid: bool
    error_code: int
```

**规则**
- 同一笔的 `dither_word/dem_state/mask` 在**采集接受时冻结**；后续 pipeline 不得读已更新的全局字。
- `logical_slot` 只用于数字内部排序；`physical_slice_id` 才对应电容/掩码。
- 每笔状态明确创建/晋升/取消/退休时刻；stall/clear 不得改变已接受笔的字。

---

## 4. 开关与采集命令

```python
@dataclass(frozen=True)
class AcquisitionCommand:
    sample_id: int
    slice_ids: tuple[int, ...]
    sample_main: tuple[int, ...]   # 每位 0/1，长度 n_main
    sample_sub: tuple[int, ...]    # 每位 0/1，长度 n_sub
    signal_connect: tuple[int, ...]  # Signal/RefLo/RefHi 三态编码
    auxiliary_charge_c: float       # 仅 AUX 路径
    binding_id: str

@dataclass(frozen=True)
class SwitchCommand:
    sample_id: int
    conversion_mask: tuple[int, ...]  # 0/1
    sampling_mask: tuple[int, ...]    # 0/1
    level: str                        # Signal/RefLo/RefHi/Inject
    feature: int                      # 0/1/2；conv+sample 重叠=2
```

**硬规则**
- `Signal`、`RefLo`、`RefHi`、`inject` **不能压成一个 bit**。
- `conv+sample` 同时为真时，特征必须是 **2**，不能 OR/XOR 截断。
- 观察包要么显式保存 `sample_sub`，要么能从冻结 word/state/slice 唯一重建；两者都要有 `binding_id`/版本/摘要/独立解析验证。

---

## 5. 数字观察 vs plant trace

```python
@dataclass(frozen=True)
class DigitalObservation:
    sample_id: int
    coarse_code: int
    adc2_code: int
    dither_word: int
    dem_mask: tuple[int, ...]
    control_snapshot: dict       # 只含公开控制
    flags: dict                  # reject/clip/over
    # 不含：真实电容值、隐藏节点电压、真实失配、plant 内部状态

@dataclass
class PlantTrace:
    sample_id: int
    node_v: dict[str, np.ndarray]     # 调试
    charge_c: dict[str, np.ndarray]   # 调试
    cap_f: dict[str, np.ndarray]      # 真实失配，训练禁用
```

**规则**：训练/重构只吃 `DigitalObservation`；`PlantTrace` 不得进入 `fit()`/`reconstruct()`。校准可用**已声明的独立外部参考**，不得读真实失配或隐藏保持电压。

---

## 6. 冻结校准与定点

```python
@dataclass(frozen=True)
class FrozenCalibration:
    calibration_id: str
    topology_id: str
    codebook_id: str
    feature_order: tuple[str, ...]
    binding_id: str
    v_range_v: tuple[float, float]
    v_domain: str                 # "input_referred" | "ra_output"
    adc2_bin_edges: tuple[int, ...]
    coefficient_unit: str         # 例："V/V"、"C/C"
    sign_convention: str
    q_format: str                 # 例："Q30 weight / Q32 voltage"
    rounding: str                 # floor/nearest
    overflow: str                 # saturate/reject
    training_set_id: str
    mode_id: str                  # NLMS/LS/SVD；不得混名
```

**规则**
- 错误 `topology_id/codebook_id/binding/q_format` 的系数必须拒绝，不得"尽量用"。
- 片外训练与片上应用分工保留；不得把 LS/SVD/NLMS 拼成一条说不清的方法。
- 两种 DEM 模式的系数不能互换；切换要冻结/排空/取消/重载/启用五步。

---

## 7. 模拟宏 ↔ 数字接口

| 信号 | 方向 | 语义 | 时序/所有权 |
|:---|:---|:---|:---|
| `sample_accept` | 数字→模拟 | 本笔已被调度接受 | 早于孔径 `t_setup` |
| `decision_valid/data` | 模拟→数字 | 比较器判决有效 | `ready_time` 后才可消费 |
| `rdac_command` | 数字→模拟 | 逐位/最终码 | 同笔 mask，不得跨笔 |
| `analog_ready/idle` | 模拟→数字 | 建立/复位完成 | 未到不得复用物理片 |
| `adc2_valid/code` | 模拟→数字 | 二级码 | 同 `sample_id` |
| `error` | 模拟→数字 | clip/over/sat | 不得静默裁剪 |

**CDC**：RTL 输入是同步捕获还是显式跨域桥接必须写清；不得把异步模拟宏自然当同步输入。`eligible` 的外部责任不得写成"调度器已完成模拟建立判断"。

---

## 8. 单位与命名

| 后缀 | 含义 | 例 |
|:---|:---|:---|
| `_v` | 电压 V | `vref_high_v` |
| `_f` | 电容 F | `c_unit_f` |
| `_c` | 电荷 C | `q_sample_c` |
| `_s` | 时间 s | `t_ra_s` |
| `_hz` | 频率 Hz | `fs_hz` |

- `Vfs_peak`（单端峰值）与 `Vspan`（差分峰峰）分开；`V/Vfs`、`V/Vspan`、物理 V 不能混。
- `LSB20`、`LSB1`、`ppm` 必须带后缀：`inl_lsb20`、`dnl_lsb1`、`inl_ppm`。
- ADC2 的 RA 输出域与输入折算域只能经显式命名适配器转换（`adc2_code_to_input_v`）。
- `delta_adc1`、`native_dither_step` 分开；`physical_slice_id`、`slot_index` 分开。

---

## 9. 事件/事务的所有权

| 状态 | 所有者 | 只读方 |
|:---|:---|:---|
| 物理电容/失配 | `PhysicalSlicePool` | 行为模型/plant trace |
| 采样保持节点 | `sampler` | 数字只读 `DigitalObservation` |
| 参考/RA/ADC2 节点 | `ConversionEngine` | 数字只读码/flags |
| 调度/DEM/dither 字 | `control` | 行为模型只按命令执行 |
| 样本生命周期 | `simulation` | CLI/实验只读 `SimResult` |
| 冻结系数 | `calibration` | 重构只读 `FrozenCalibration` |

---

## 10. 待确认（W0 输出，不是结论）

1. `topology_id` 的枚举值是否按"论文/工程/专利例"三值固定，还是允许组合？
2. `same-time order` 取"孔径先于开关"是否与论文 Fig.9.8.2 的采样沿一致？需原文页图确认。
3. `sample_sub` 是显式保存还是从冻结字重建？两者对存储/复现的影响需在 W1 定。
4. `FrozenCalibration.q_format` 采用 main 的 Q30/Q32/64/48/96/20，还是 task 的 Q28？建议**两套并存、按 topology 绑定**，不强行统一。
5. `auxiliary_charge_c` 是独立注入电容还是共享权重？必须与 dither 的 `native_dither_step` 分开命名。
