# 对 2026-09-11 **第四份**外部复核的逐条裁定

- **对象**：`main` 分支 `71a5668`（v7.0.2 提交），复核方明确固定在该提交
- **复核来源**：外部独立复核（第四份）；前几份的裁定见
  `docs/review_response_2026-09-11.md`（第二份）、
  `docs/review_response_2026-09-11b.md`（第三份）
- **裁定日期**：2026-09-11
- **方法**：每条指控在真实代码上独立复现或证伪；本表的数字全部由本仓库自己
  跑出，复现命令见 §6。
- **本轮的版本级结论**：K1 / K2 / K4 已处置；**K3 只闭合了一半，另一半不在
  本轮闭合**，理由写在 §4（那是一条物理主路径变更，需要单独决策，不能借
  "配置开关" 的名义夹带）。

---

## 0. 立场

复核本轮换了方向，而换得对。

前三份指控的内核是"改动有没有进到产生结果的那条路径"；第四份不再问这个，
而是问**判据本身绑在哪个物理节点上**。这个方向更根本：一条判据如果取错了
节点，门禁修得越严密，错得越有说服力 —— K1 就是这种情形，一个错了 52.8 倍的
节点被当成"已知限制"登记进了账本，而账本机制当时刚刚被加固过。

**接受**本轮的总评价方向，也接受它对 K1 的定性：那不是一条电路限制，是一次
口径错误。**不接受**的是把 K3 当作已闭合 —— 我把它记成"半开"，并在 §4 说明
为什么不在本轮替它做决定。

**逐条裁定汇总**

| 编号 | 指控 | 裁定 | 本轮动作 |
|:---|:---|:---|:---|
| K1 | KTC `f_max` 判据绑定 ADC2 量程，而校正项已在数字域扣除 | **成立** | **已修**：判据改绑观测通路节点，`KNOWN_LIMITS` 该条目**删除**（它本来就不该存在） |
| K2 | 物理池契约仍是"结果对象有某几个属性"，不是"有因果作用" | **成立** | 已改为**因果契约**（扰动选中 slice 的电容 → 该样本的内部量必须动；未选中的样本必须不动）。仍 `xfail(strict=True)`，因为池仍未接线 |
| K3 | `dem_mode` 没有真正决定机制；`stage1_reading` 标签与 `b1` 无一致性校验 | **部分成立** | `stage1_reading` 一半**已修**（`check_legal` 增加标签↔`b1` 一致性）；`dem_mode` 一半**只闭合 1/3 入口**，另一半挂起（§4） |
| K4 | 门禁接受非布尔 `PASS`（`bool("False") is True`）；缺少"必需判据"集合 | **成立** | **已修**：`_verdict` 只接受真 `bool`，其余抛错；新增 `REQUIRED_RECORDS`（按 ID 钉住结论载体，堵住"删一条加一条"） |

---

## 1. K1 — `f_max` 判据绑定了错误的节点

**成立，且这是本轮最重的一条。**

### 1.1 复算

同一条代码路径、同一个 `Config`，在两个节点上读数：

```python
import math
from adi_model import Config

cfg = Config(ktc_enable=True)
dt = cfg.ktc_dt()

# 旧节点（v7.0.0 – v7.0.2）：ADC2 量程余量
wrong = min(cfg.adc2_v_max - cfg.g_actual * cfg.delta1, -cfg.adc2_v_min) / (
    2 * math.pi * cfg.v_fs * cfg.g0 * dt
)                                  # 2.5465 MHz   -> FAIL（门限 5 MHz）

# 新节点（本轮）：观测通路可用摆幅 / 观测量步长
right = (cfg.ra_v_clip / cfg.ktc_gain_n) / (
    2 * math.pi * cfg.v_fs * dt
)                                  # 134.4541 MHz -> PASS
```

| 节点 | 表达式 | 实测 | 判 5 MHz | 比值 |
|:---|:---|---:|:---:|---:|
| ADC2 量程余量（旧） | `min(adc2_v_max − G·Δ1, −adc2_v_min) / (2π·v_fs·G0·Δt)` | 2.5465 MHz | FAIL | 1.00 |
| 观测通路摆幅（新） | `(ra_v_clip / ktc_gain_n) / (2π·v_fs·Δt)` | 134.4541 MHz | PASS | 52.80 |

### 1.2 为什么旧节点是错的

ADR 0006 采纳 KTC 校正项的**数字域扣除**：`ADC2.quantize_with_correction`
返回 `quantize(v_ra) − κ·v_N`。因此：

- `over` 只反映 `v_ra` 本身是否越界，`κ·v_N` **无论多大都不占 ADC2 量程**；
- 旧口径的前提"校正量消费 ADC2 模拟量程"与 ADR 0006 直接冲突；
- 真正被带宽限制的量是观测节点上的 `G_N·2π f A Δt`，约束是它不得超过该节点
  可用摆幅 `ra_v_clip`。

### 1.3 处置

| 位置 | 改动 |
|:---|:---|
| `config.py::validate` | 判据表达式改绑观测量；**键名**由「KTC 校正项带宽上限 f_max (满幅)」改为「KTC 观测通路摆幅上限 f_max (满幅)」（键名跟着口径走，防止旧口径以同名回归） |
| `acceptance.KNOWN_LIMITS` | **删除**该条目，并留下一条注释说明"它不是被删除的限制，而是取错节点的判断" |
| `acceptance.REQUIRED_RECORDS` | 把新键名列为**必需判据**，钉住"不许改回 ADC2 口径、不许名字漂移" |
| `experiments.py::stage7_headline` | 同一个守卫改同口径。默认参数下新上限 ≈ 134 MHz，远高于 `fs/2 = 20 MHz`，因此当前**不会触发**；保留它是为了参数退化时仍拦得住 |
| `tools/make_report.py` | `f_bw` 取值键与正文公式同口径；原文附带的「374 mV / 溢出率 3.1% / SNDR 70 dB」在仓库内**没有复现入口**，已就地标注为历史叙事、不作为结论 |

`f_max` 的**出口**因此不是"抬高门限"，而是换节点。抬门限那条路（改
`adc2_v_min` / `Δt` / `G0`）会移动已发布数值，属于架构决策，本轮没有走。

---

## 2. K2 — 物理池契约仍是"属性存在"，不是"因果作用"

**成立。** v7.0.2 把源码字串扫描换成了属性检查 —— 那是进步，但仍然只是
**关于对象形状的陈述**：属性可以加，且可以填成常数，断言照样通过。

### 2.1 现在的契约

契约改为**因果关系**，并被真正执行：

1. **指向**：取到"哪个 slice 转换了样本 i"（今天做不到，因此报出缺环的名字，
   而不是抛 `AttributeError`）；
2. **扰动**：把那些 slice 的电容改掉，重跑；
3. **断言**（两半，缺一不可）：
   - 用过这些 slice 的样本，其**内部量**（`e_dac`）必须动；
   - 没用过的样本必须**不动**。

第二半是关键：只断言"动了"的话，一个对所有样本一视同仁的全局敏感度也能
满足它，那样就什么都没证明。

### 2.2 两条正向对照（今天就通过）

- `test_the_selection_information_exists_and_is_causal`：调度器**已经**返回逐样本的
  `(conv, acq)` 索引数组，且满足跨周期归属。缺的是绑定，不是数据。
- `test_the_pool_ties_a_slice_to_exactly_the_samples_that_use_it`：在**池上**扰动
  slice 0 的电容，转换电容只对**转换组含 slice 0** 的样本改变，其他样本逐位不变。
  这证明这条因果关系是良定义且锋利的，因此 `xfail` 不会因为"关系本身不成立"
  而通过。

对照必须存在，否则将池接线之后这条测试会**因为错误的原因**变绿。

### 2.3 仍然 `xfail(strict=True)`

池没有被任何 runner 使用（`pipeline.py` / `sim_split.py` / `sim.py` 与全部实验
均无引用），而 ADR 0005 声称两条信号链"从同一个池取物理量"。**契约保持失败
状态**，标记由 `strict=True` 强制：接上线的那天它会 XPASS，测试套件立刻报错，
逼人摘标记并改写断言。

---

## 3. K4 — 门禁会接受一个它必须靠猜的判据

**成立。** `bool("False")` 是 `True`。v7.0.2 用 `bool(raw)` 归一化，于是
**一次序列化改动就能把 FAIL 读成 PASS**，而且门禁无从察觉 —— 类型在归一化时
已经被丢掉了。

### 3.1 处置

| 项 | 改动 |
|:---|:---|
| `acceptance._verdict` | 只接受真 `bool`；`str` / `int` / `float` / `None` / 容器一律抛 `ValueError` 并点名是哪种类型。**不做**归一化 —— "拒绝读取"是不会静默出错的唯一行为 |
| `acceptance.REQUIRED_RECORDS` | 新增必需判据 **ID 集合**。`MIN_RECORDS` 只数条数，"删一条 + 加一条"骗得过它；这份集合钉住"删掉它、某个结论就失去证据"的那些条 |
| `GateResult.missing_required` | 缺失必需判据是**独立失败项**，与"未登记失败""过期豁免"并列 |
| 账本账目 | 见 §3.2 |

### 3.2 账本变空不是把机制删掉

`KNOWN_LIMITS` 现在为空 —— 唯一那条（KTC `f_max`）是 K1 的口径错误造成的
假失败，不是真限制。v7.0.2 的测试里有一句 `assert KNOWN_LIMITS`，意思是
"账本空了就把机制一起删掉"。**那句话在本轮被改写，而不是被满足。**

理由：账本机制的价值恰恰在于让豁免无法隐藏，而"当前没有豁免"不是删除它的
理由 —— 删除之后，下一次出现真实限制时它会以"直接在门禁里加白名单"的形式
回来。因此机制保留，行为改为用**合成输入**直接验证（§6 命令里含）：

- 登记一条豁免 → 它从 `failures` 移入 `exempted` 并被**打印**；
- 已登记的豁免**不再失败** → 报为 `stale`，且门禁**不通过**；
- 豁免理由为空 → 抛错。

即：账本的每一条行为都仍然被钉住，只是不再需要一个假限制来充当样本。

---

## 4. K3 — `dem_mode` 只闭合了三分之一，另一半**故意不闭合**

**部分成立。** 拆成两半说。

### 4.1 `stage1_reading` 标签一致性 —— 已修

标签（`paper_literal` / `b1`）此前与 `b1` 的实际取值**没有任何一致性校验**：
标签说一种读法、参数按另一种设，代码照跑。`Config.LEGAL_VALUES` 现在带一条
跨字段校验（`READING_B1` 映射），`check_legal()` 在不一致时于**入口**抛
`ConfigError`。

### 4.2 `dem_mode` —— 三个入口里只有一个真的变了机制

v7.0.2 把三个仿真入口都接到 `scheduler.make_scheduler(cfg, rng, scheduler)`，
`rotate → Scheduler`、`permute → ShuffledScheduler`，并且当显式传入的调度器
与 `cfg.dem_mode` 矛盾时**拒绝运行**。绑定是真的。但它**只绑住了名字**：

| 入口 | 消费调度器的哪个方法 | `ShuffledScheduler` 是否覆写 | `dem_mode` 是否改变机制 |
|:---|:---|:---:|:---:|
| `run_pipeline` | `reserve_dual` | **是** | **是**（实测输出不同，见 §6） |
| `run_sim_split` | `reserve` | **否**（继承基类的乒乓） | **否**（实测逐位相同） |
| `run_sim` | `reserve` | **否**（继承基类的乒乓） | **否**（未单独测，同一代码形状） |

也就是说：把 `dem_mode` 写成 `"permute"` 再跑 `run_sim_split`，用户拿到的
**仍然是乒乓调度的切片组**。这与 v7.0.0 的 `ra_gain_model` 是同一类缺陷
（名字许诺了没接线的机制），只是这次它被从"完全没接线"推进到了"接了一半"。
**不声称已闭合。**

### 4.3 为什么不在本轮闭合

要让 `ShuffledScheduler.reserve` 真的洗牌，必须先回答**洗哪一种**：

- **因果洗牌**（`conv[n] == acq[n-1]`，即 `PhysicalSlicePool.shuffle_causal` 的
  策略）—— 这正是 R1 那条未闭合缺陷的修法，它会**移动已发布的 stage-19
  数值**（洗牌抑制量、`slice_bw_spread` 与 skew 两族行的数字）；
- **独立洗牌**（每周期独立重排）—— 就是当前 `reserve_dual` 的行为，它是
  R1 缺陷本身，不能把它推广到另一个入口。

两条都会改变"跑出来是什么"，属于**物理主路径变更**，需要一条独立决策
（连同"哪些数值会变、由哪一项修复引起"的差异表），不能借"把一个配置开关接
完整"的名义夹带进来。因此本轮：

- 已闭合的一半：**绑定 + 冲突拒绝**（有测试）；
- 未闭合的一半：**`xfail(strict=True)`**，reason 里写清"接通那天必须改写"，
  并在 `scheduler.py::make_scheduler` 的 docstring 里就地声明
  "接线 ≠ 调度正确"。

这是本轮唯一一处**知道怎么改而没有改**的地方，特此标明。

---

## 5. 数值影响

本轮的全部改动落在三处：**判据表达式**、**门禁读取规则**、**配置合法性校验**。
没有一处触及信号主路径的数学，因此预期 `results.json` 相对 v7.0.2 **只有
`validate_ktc` 一条记录变化**（键名 + 实测值 + PASS），其余逐字节相同。
实测结论与基线哈希见 `CHANGELOG.md` 的 7.0.3 段。

---

## 6. 复现命令

```bash
# K1：两个节点的数值（应为 2.5465 MHz / 134.4541 MHz）
PYTHONPATH=src python - <<'PY'
import math
from adi_model import Config
cfg = Config(ktc_enable=True); dt = cfg.ktc_dt()
print(min(cfg.adc2_v_max - cfg.g_actual*cfg.delta1, -cfg.adc2_v_min)/(2*math.pi*cfg.v_fs*cfg.g0*dt)/1e6)
print((cfg.ra_v_clip/cfg.ktc_gain_n)/(2*math.pi*cfg.v_fs*dt)/1e6)
PY

# K2 / K4 / K3：三条契约（含正向对照与半开标记）
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py -q
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py::TestR2PhysicalPoolOnMainPath -v
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py::TestR10DemModeIsWired -v
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py::TestR12KtcMaxUsesTheObservedNode -v
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py::TestR15GateRefusesNonBooleanPass -v

# K3 的实测差异（pipeline 变机制 / sim_split 不变）
PYTHONPATH=src python - <<'PY'
import numpy as np
from adi_model import Config, run_sim_split, sine_input
from adi_model.pipeline import run_pipeline
base = dict(dac_arch="split", dyn_ref_settling=False, dyn_crosstalk=False,
            mismatch_enable=True, dyn_input_settling=True)
x = sine_input(0.5 * 1.8, 97 * 40e6 / 2048)
for name, fn in (("pipeline", run_pipeline), ("sim_split", run_sim_split)):
    a = np.asarray(fn(Config(dem_mode="rotate", **base), x, 2048, rng=np.random.default_rng(11)).out)
    b = np.asarray(fn(Config(dem_mode="permute", **base), x, 2048, rng=np.random.default_rng(11)).out)
    print(name, "identical:", np.array_equal(a, b), "maxdiff:", float(np.max(np.abs(a - b))))
PY

# 门禁与账本
PYTHONPATH=src python -m pytest tests/audit/test_review_contracts.py::TestR11KnownLimits -v
```

---

## 7. 未决项（需要决策，不属本轮）

1. **K3 的另一半**：`ShuffledScheduler.reserve` 是否改为因果洗牌。改则 stage-19
   数值移动；不改则 `dem_mode` 在 `run_sim` / `run_sim_split` 上继续是名义开关。
2. **R1**：`ShuffledScheduler.reserve_dual` 的独立洗牌（`conv[n] = acq[n-1]` 在
   0/8191 次转换上成立）是否改为因果策略。与第 1 条是同一个决定的两面。
3. **R2/K2 的物理接线**：`PhysicalSlicePool` 是否接管主路径的电容与保持电荷。
   接线的同时应给出"哪些已发布数值会变"的差异表。
4. **`require()` 的生产调用点**：它自称"强制钩子"，但生产代码里仍无调用点
   （第三份复核已记录，未变）。

---

## 更新记录

| 日期 | 变更 |
|:---|:---|
| 2026-09-11 | 初版：4 条逐条裁定。K1 判据改绑观测通路节点（2.5465 → 134.4541 MHz，`KNOWN_LIMITS` 条目删除）；K2 物理池契约改为因果断言（两条正向对照 + `xfail(strict)`）；K4 门禁拒绝非布尔 `PASS` 并新增 `REQUIRED_RECORDS`；**K3 只闭合 1/3 入口**，另一半作为独立决策挂起（§4）。全量复跑确认 `results.json` **只变一条记录**，哈希 `65c047a9…4ecdec` → `a1ccd92f…35ac70` |
