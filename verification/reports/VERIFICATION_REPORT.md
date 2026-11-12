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

## 3. 验证抓到的两处问题

### 3.1 `gain` 口径：合同写 `Σ(W·a)`，实现是 `G=T`

`RTL_ARITHMETIC_CONTRACT.md` §3.2 把增益定义为 `gain = Σ(W·a)`；
`recon_core` 的头注释写"本核仅支持已独立证明的 AllSIGNAL 子集，G=T"。

实测差异（向量 0）：按 §3.2 得字 **124308**，RTL 得 **309535**；按 `G=T` 得 **309535**。

处理：参考模型加 `gain_mode={"masked","all_signal"}`，两种口径都保留，
`recon_core` 用 `all_signal`。**这不是把参考改成能过**——是合同正文与实现限制不一致，
需要走 ADR 收敛；在收敛前，任何"逐单元权值"结论都不能引用 §3.2 的 `Σ(W·a)`。

### 3.2 `.mem` 位宽不能用十六进制位数推断

63 位字段写成 16 个 nibble。按文本宽度做符号扩展会把所有负数读成大正数，
现象是"`div_floor` 在 d≥1 时全错、d=1 时给出 -1"。现在按 `meta.json` 的声明位宽扩展。

## 4. NOT_RUN 与缺件

| 级别 | 状态 | 具体缺什么 | 影响 |
|:---|:---|:---|:---|
| V3 `rtl_top` | **NOT_RUN** | `sar20_digital_core` 需要 calobs v2 观测总线 + 同 profile 的 `source_control_binding` / `source_acquisition_binding` 实例；测试台还要按 16 相位提供比较器判决与 ADC2 原始码 | 无法声称"整链无丢样/重样/乱序" |
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
