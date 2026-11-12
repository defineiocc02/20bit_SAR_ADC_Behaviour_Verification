# Python–RTL 一致性验证（V1 / V2）

> 目标不是"Python 测试通过、RTL 也能编译"，而是**同一合法数字事务在两侧得到相同的整数结果**。
> 当前状态：**V1 模块级已跑通并位真通过 7 个模块**；V2 的逐拍状态对拍已覆盖调度器与 follower；
> **V3 顶层 / V4 闭环为 `NOT_RUN`**，缺什么写在下面，不折算成 PASS。

## 1. 三个角色，必须分开

| 角色 | 位置 | 干什么 | 明确不干什么 |
|:---|:---|:---|:---|
| A 浮点行为模型 | `src/adi_model/` | 模拟电路、电荷方程、DEM/dither 物理效果、最终性能指标 | **不**当 RTL 的逐位 oracle（浮点不能给出位真值） |
| B 定点参考模型 | `verification/reference/` | 按合同逐位复现 RTL 的整数算术、舍入、截断、饱和 | 不 import 任何 RTL、不调用 DUT；不把 SV 逐行翻译过来 |
| C 独立验证器 | `verification/cosim/scoreboard.py` | 只比较、只报第一处不一致；不修改结果 | 不生成期望值、不修 golden |

分开的理由很实际：如果 B 是照着 SV 抄的，一个共同错误会在两侧同时出现，对拍照样"通过"。
所以 B 是从 `docs/rtl/RTL_ARITHMETIC_CONTRACT.md` 的**正文**推导的，并且**已经被 RTL 抓出过一处偏差**
（见 §5 的 `G=T`）。

## 2. 目录

```
verification/
├── contracts/
│   ├── digital_interfaces.yaml   # 端口/位宽/单位/握手，逐行来自 RTL 文件
│   ├── fixed_point.yaml          # Q30/Q32、舍入、floor、溢出语义
│   ├── timing.yaml               # 时沿、接受条件、延迟周期
│   └── rtl_params.vh             # 冻结参数头（与 yaml 交叉校验）
├── reference/
│   ├── fixed_point.py            # half-even / floor / 受检累加器
│   ├── datapath_ref.py           # popcount / 温度计 / bin centre / 重构
│   └── scheduler_ref.py          # 8-of-18 调度器完整状态机
├── vectors/
│   ├── generate.py               # 定向 + 约束随机；只知边界，不知答案
│   └── generated/<module>/       # .mem（$readmemh）+ meta.json（seed/位宽）
├── rtl/tb/tb_<module>.sv         # 只施加激励、只记录输出
├── cosim/
│   ├── vcs_runner.py             # 远端 VCS 编译/运行/回收 dump
│   └── scoreboard.py             # 逐字段位真比较 + 第一处不一致定位
├── reports/                      # results_<level>.json / module_matrix.md
└── run_verification.py           # 统一入口
```

## 3. 怎么跑

```bash
# 生成向量（可重放：seed 固定在 meta.json 里）
python -m verification.vectors.generate

# 合同与参数头交叉检查（不需要仿真器）
python -m verification.run_verification --level contracts

# V1 模块级对拍（需要 VCS 主机）
python -m verification.run_verification --level module --simulator vcs --profile adi_40m

# 只跑某几个模块
python -m verification.run_verification --level module --only recon_core adc2_dec

# 明确未运行的两级
python -m verification.run_verification --level rtl_top
python -m verification.run_verification --level closed_loop
```

仿真器不在本机。`vcs_runner` 通过 `ssh vm-meow` 把目录送到远端、编译、运行、把 `.out` 取回；
主机与 license 信息见仓库外的主机笔记。**连不上或没有 license 时返回 `NOT_RUN`，不会伪造通过。**

## 4. V1 覆盖（当前真实结果）

| 模块 | 比较字段 | 向量 | 结果 |
|:---|:---|:---:|---|
| `sadc_enc` | `sadc_code` | 127 | 逐位一致 |
| `unit_therm` | `main_on` / `sub_on` 全 mask | 96 | 逐位一致（含越界 count 饱和） |
| `adc2_dec` | `fine_q` / `ovf` | 4195 | 全 4096 码 + 边界 + 平局范围 |
| `div_floor` | `q` / `err` / 延迟周期 | 257 | 含零分母、负数非整商 |
| `rdac_drv` | `slice_sel` / `main_sw` / `sub_sw` | 55 | 含重复 ID、越界 ID、reset 优先 |
| `slice_pool_ctrl` | 7 个状态字段，逐拍 | 100 | 含 priming、游标保留区、stall |
| `recon_core` | `dout` + 4 个 flag + 延迟 | 101 | 全 20 位重构与状态位 |

具体数字以 `verification/reports/results_module.json` 为准；本表是它的摘要。

## 5. 验证过程中发现的两件事

**(1) 合同与实现的 `gain` 定义不同。** `RTL_ARITHMETIC_CONTRACT.md` §3.2 写 `gain = Σ(W·a)`，
但 `recon_core` 的头注释写"本核仅支持 AllSIGNAL 子集，G=T"。实测：按 §3.2 算，
第一个向量的字是 124308，RTL 给 309535；按 `G=T` 算正好 309535。
参考模型因此加了 `gain_mode` 参数，**两种口径都保留**，并注明哪一种对应实现。
这不是把参考改成"能过就行"——是合同正文与实现限制不一致，需要走 ADR 收敛。

**(2) `.mem` 的位宽不能用十六进制位数推断。** 63 位字段写成 16 个 nibble，
按文本宽度解析会把所有负数读成大正数，表现为"RTL 在 d≥1 时全错"。
现在按 `meta.json` 里声明的字段位宽做符号扩展。

## 6. 未运行（NOT_RUN）与原因

| 级别 | 状态 | 缺什么 |
|:---|:---|:---|
| V3 `rtl_top` | **NOT_RUN** | `sar20_digital_core` 需要 calobs v2 观测总线 + 同 profile 的 `source_control_binding` 实例，测试台还要按相位提供比较器判决 |
| V4 `closed_loop` | **NOT_RUN** | 需要 Python plant 消费 RTL 实际开关命令并回送 ADC2 码的事务桥；V1 的 bench 还没导出逐事件命令流 |
| 校准系数加载一致性 | **NOT_RUN** | 需要 RTL `calib_regs` / `weight_store` 的真实加载序列与版本错绑反例 |
| 代码覆盖率 | **NOT_RUN** | 未接 `-cm` 覆盖率收集；功能覆盖由向量表给出，**不用代码覆盖替代** |
| 图 B / 图 E | **NOT_RUN** | B 需要逐周期 VCD 解析；E 依赖 V4 闭环 |

## 7. 硬规则

* 合法事务：Python 定点参考与 RTL **bit-exact**，不用容差。
* 不允许为了通过而改 golden、放宽位宽或改参考口径。
* 任何 `NOT_RUN` 不计入 PASS；`PARTIAL` 不写成"基本通过"。
* 本目录只做数字域一致性，不评价模拟精度、不评价论文指标复现。
