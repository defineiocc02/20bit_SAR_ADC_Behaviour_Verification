# Python–RTL 一致性验证报告（V1 / V2）

> 数据来源：`verification/reports/results_module.json`（由 `python -m verification.run_verification --level module` 生成）。
> 仿真器：VCS W-2024.09-SP1 @ `vm-meow`，license `27080@localhost`。
> 本报告的每个数字都能用 §5 的命令重跑；没有手工补的曲线。

## 1. 结论

**V1 模块级：7/7 模块位真一致，合计 4931 条向量，0 处不一致。**
**V2 逐拍状态：`slice_pool_ctrl` 100 拍状态逐字段一致，`rdac_drv` 55 拍的 fanout 与覆盖语义一致。**
**V3 顶层 / V4 闭环：NOT_RUN**，缺件在 §4 逐条列出，不计入 PASS。

## 2. V1 结果

| 模块 | 比较字段 | 向量 | 结果 | 覆盖的边界 |
|:---|:---|:---:|---|---|
| `sadc_enc` | `sadc_code` | 127 | 127/127 | 全 0 / 全 1 / 单比特 / 交替 / 5 种密度含气泡码 |
| `unit_therm` | `main_on`,`sub_on` | 96 | 96/96 | count=0,1,31,32,62,63,64,127（越界饱和）、三种 rank 排列 |
| `adc2_dec` | `fine_q`,`ovf` | 4195 | 4195/4195 | 全 4096 码 + 5 组量程 + 小量程平局 + 随机非对称量程 |
| `div_floor` | `q`,`err`,延迟 | 257 | 257/257 | 负被除数、d=1/2/3/5/7/1023、d=0、延迟恒为 9 拍 |
| `rdac_drv` | `slice_sel`,`main_sw`,`sub_sw` | 55 | 55/55 | 重复 slice_id、id≥18 忽略、load=0 保持、reset 优先 |
| `slice_pool_ctrl` | 7 个状态字段/拍 | 100 | 100/100 | 首拍 priming、shuffle 开关、游标 252..255 保留区、stall、中途 reset |
| `recon_core` | `dout`+4 flag+延迟 | 101 | 101/101 | 随机 mask/码/注入、单单元、码两端、注入极值、延迟恒为 11 拍 |

合计 **4931** 条向量、**0** 条不一致。

### 为什么这些数字可信

* 参考模型是**整数**的，不是浮点近似；比较是 `==`，没有容差。
* 参考模型从合同正文推导，不 import RTL。运行中它确实和 RTL 分歧过一次（见 §3），说明两者不是同源拷贝。
* 每个模块的激励都含**非法/边界输入**：d=0、count 越界、重复 slice id、id 越界、负被除数。
  这些不是"能跑就行"的正例，而是合同规定必须拒绝或饱和的场景。

## 3. 过程中的四次更正（含一次**撤回**）

### 3.1 【已撤回】`gain` 口径"不一致"——是我读错了合同

上一版这里写着：合同 §3.2 是 `gain = Σ(W·a)`，而 `recon_core` 是 `G=T`，
同一向量算出 124308 vs 309535，属于"合同与实现不一致"。

**这个结论是错的，现予撤回。** 合同 §3.2 紧接着写明：

> 普通单位的 `a=1`、`s ∈ {−1,+1}`；采样 dither 单位的 `a=0`、`s ∈ {−2,0,+2}`。

`a` 是**信号连接标志**，`on` 是**转换开关状态**；普通单位恒有 `a=1`，
开关只翻转 `s`。我把 DAC 的 `main_on/sub_on`（即 `on`）当成了 `a`，
于是 `gain` 看起来随 mask 变，才误以为 RTL 偏离合同。

按正确读法，普通单位下 `gain = Σ W = total`，合同的 `G=T` 与 RTL 完全一致。
改正后 `recon_core` 仍是 **101/101 位真一致**，说明 RTL 一直是对的。

已加回归测试 `test_gain_uses_signal_connection_not_the_switch_mask`，
把这个区分钉住；`datapath_ref.recon_terms` 的 docstring 也写明
`a` 与 `on` 不是一回事，并指向本条记录。

> 这一段保留在正文而不是删掉：一个只有"通过"记录的验证报告没有信息量，
> 而"参考写错了、被 RTL 抓出来"恰好证明两侧不是同源拷贝。

### 3.2 【真缺陷，已修】DEM 逐片审计实际统计的是逻辑 slot

`dem3d.usage_uniformity` 里原本是：

```python
per_slice_main = command.main_on.sum(axis=0)   # 形状 (active_slot, unit)
```

`main_on` 的形状是 `[sample, active_slot, unit]`，沿样本轴求和得到的是
**每个逻辑 slot** 的统计。采样池是 18 选 8 轮转的，slot 不是物理片，
这个数不能回答"每个物理单元是否被均衡使用"，却被命名为
`per_slice_units_covered`。

现改为按真实 `slice_ids` 散列到 `[n_slices, n_unit]`
（新增 `_per_physical_slice_counts`），并补两个测试：

* `test_physical_slice_audit_is_not_slot_aggregation`：构造"每片只用到 21/63、
  但各片窗口不同"的反例。旧的 slot 聚合会报 63/63 全覆盖，新的逐片统计报 21/63。
  **断言落在审计结果本身上**，不是靠一个不参与判定的诊断字段。
* `test_slice_ids_reach_every_physical_row_once_per_rotation`：没被选中的物理片
  必须保留为零行，不能被过滤掉；同时校验逐笔 visit 数守恒。

### 3.3 【真回归，已修】迁移漏了 `from adi_model import <模块名>` 这一种写法

把 `pipeline_engine` 搬进 `adi_model.simulation` 后，`tests/integration/test_fig19_static_ports.py`
里的 `from adi_model import pipeline_engine` 直接 ImportError，导致
**故障注入测试整组失效**（它本该证明"交换 dither 位能被独立 oracle 抓住"）。

我的迁移脚本只重写了 `adi_model.pipeline_engine` 形式的绝对路径，没有覆盖
`from adi_model import <name>`。全仓扫描后确认只有两处：这一处是坏的，
另一处 `from adi_model import metrics` 由包的薄适配兜住了。

修法是把测试改成 `from adi_model.simulation import pipeline_engine`，
并在原处留注释说明为什么。**没有把该测试跳过或放宽。**

### 3.4 【工具缺陷，已修】`.mem` 位宽不能用十六进制位数推断

63 位字段写成 16 个 nibble。按文本宽度做符号扩展会把所有负数读成大正数，
现象是"`div_floor` 在 d≥1 时全错"。现在按 `meta.json` 的声明位宽扩展。

## 4. NOT_RUN 与缺件

| 级别 | 状态 | 具体缺什么 | 影响 |
|:---|:---|:---|:---|
| V3 `rtl_top` | **ELABORATED_ONLY**（功能仍 NOT_RUN） | 顶层已用生成的 binding 在 VCS 下 elaborate 并跑到复位后 idle（`acquiring_mask=0xff`、无伪 `dout_valid`）。功能级缺：71 个权重 + offset/min/max + 18 行窗口的完整配置写入与 `cfg_validate`，以及逐相位比较器判决 | 不能声称"整链无丢样/重样/乱序" |
| V4 `closed_loop` | **NOT_RUN** | 需要 Python plant 消费 RTL 实际开关命令并回送 ADC2 码的事务桥；V1 的 bench 只导出最终状态，没有逐事件命令流 | 无法声称模拟–数字闭环一致性 |
| 校准系数加载 | **NOT_RUN** | 需要 `calib_regs`/`weight_store` 的真实加载序列与"错误版本注入"反例 | 无法声称校准版本不错绑 |
| 代码覆盖率 | **NOT_RUN** | 未接 `-cm line+cond+fsm`；功能覆盖由向量表给出 | 不拿代码覆盖当功能覆盖 |
| 图 B / 图 E | **NOT_RUN** | B 需逐周期 VCD 解析；E 依赖 V4 | — |

## 5. 复现

```bash
python -m verification.vectors.generate
python -m verification.run_verification --level contracts
python -m verification.run_verification --level module --simulator vcs --profile adi_40m
python -m verification.reports.make_figures
python -m pytest tests/verification -q
```

单模块：
`python -m verification.run_verification --level module --only recon_core`

## 6. 图

| 图 | 状态 | 说明 |
|:---|:---|:---|
| A 模块一致性矩阵 | 已生成 `figures/A_module_matrix.png` | 每模块 Python 参考 / RTL 运行 / 位真 / corner |
| C 定点误差分布 | 已生成 `figures/C_fixed_point_error.png` | 精确（浮点）结果 − 定点字，实测落在 **[0.005, 0.998] LSB20**：整数路径只做一次 floor，差值就是精确商的小数余项，天然 <1 LSB。**RTL 与 Python 定点是 0 差异**，这张图不是 RTL 误差 |
| D DEM 物理 mask 覆盖 | 已生成 `figures/D_dem_mask.png` | 温度计展开的数值覆盖，**不是**物理 DEM 置换 |
| B 单样本逐事件对拍 | NOT_RUN | 需要 VCD 逐周期解析 |
| E 系统 INL/DNL/FFT | NOT_RUN | 依赖 V4 闭环 |
| F 覆盖缺口 | 见 `module_matrix.md` 与 §4 | — |
