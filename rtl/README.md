# `rtl/` —— 可综合 RTL 子树

当前修订入口：[完整 RTL 修复报告](../docs/rtl/COMPLETE_RTL_REPAIR_20260920.md)、
[ADR 0017](../docs/adr/0017-rtl-fixed-phase-capture-and-structure.md)。顶层接口和 16 相位
保持；相位 8/14 必须提供同步 ready。历史综合数据不能作为本次 RTL 的频率签核。


## 0. 状态（先说清楚"哪些已经存在、哪些只是规划"）

| 阶段 | 状态 | 证据 |
|:---|:---|:---|
| P0 参数导出与算术契约 | **产物已存在；内部门禁通过；外部复核未做** | `docs/rtl/RTL_ARITHMETIC_CONTRACT.md`、`rtl/params/rtl_params.vh`、`sim/vectors/params_paper_literal.json`、`tests/unit/test_rtl_export.py` |
| P1 纯数字组合/状态逻辑 | **已实现并通过仿真**（`checks=464816`，`errors=0`，`simv` 退出码 0） | `rtl/core/` 下 5 个模块、`sim/tb/p1_tb.sv`、`sim/vectors/p1_*.hex`、`docs/rtl/P1_INTERFACE.md` §8 |
| P2 定点重构核 + 控制时序 | **已实现；定点核的模块级仿真已通过** | `rtl/core/` 下共 13 个模块、`rtl/top/` 2 个、`sim/tb/p2_tb.sv`、`sim/vectors/p2_*.hex`、`docs/rtl/P2_INTERFACE.md` |
| P3 寄存器与标定通路 | **已实现**（`weight_store` / `calib_regs`）；JSON 镜像经顶层载入的端到端比对见 `sim/tb/p3_top_tb.sv` | 同上 |
| P4 可选加速器（`gain_acc` / `pretrack`） | **未实现**（D4 已确认一期不排期） | — |
| P5 综合与签核 | **进行中**：`synth/` 工具链已跑通并给出 P1 子集的面积/时序；真顶层综合与参数扫描在跑 | `synth/`、`synth/artifacts/` |
| P6/P7 | **未开始** | — |

> **P0 是否"完成"由证据定义，不由本文件定义。** 当前口径是：算术契约已冻结、导出器有漂移门禁、
> 契约中的可验证断言都由 `tests/unit/` 覆盖；但**尚未经过仓库外部的独立复核**。
> 本 README 只声明布局与约束，**不构成功能验证或可综合性证明**。

本目录保存 P0 生成的参数产物，并作为 P1 及后续可综合 RTL 的实现入口。

---

## 1. 三类产物的职责（不要混为一谈）

| 产物 | 角色 | 谁写 | 何时读 |
|:---|:---|:---|:---|
| `rtl/params/rtl_params.vh` | **可综合常量**：位宽、计数、状态数、模式码。只含无量纲整数 | `tools/export_rtl_params.py` 生成，**禁止手工编辑** | 编译期 `` `include `` |
| `sim/vectors/params_*.json` | **配置与单位元数据**：模拟量的精确有理数、每个参数的来源分级、寄存器实测值 | 同上 | 测试台／Spectre 转交／人读 |
| `sim/vectors/registers_*.json` | **待载入的整数标定镜像**（Q30 权重、Q32 偏移与后端范围） | 同上，取自一次真实运行 | RTL 载入（未来的 `calib_regs` / `weight_store`） |
| `sim/vectors/{stimulus,expected}_*.hex` | **黄金激励与期望码流**（L3 链路级用） | 同上 | RTL testbench |
| `sim/vectors/p1_*.hex` | **P1 穷举／定向向量** | `tools/export_rtl_vectors.py` | `sim/tb/p1_tb.sv` |

`params.json` 与 `registers.json` **同时存在且用途不同**：前者是元数据（含来源分级），
后者是可直接装进寄存器的整数镜像。两者都可能包含数值，但**不能互相替代**。

---

## 2. 布局

`[已存在]` = 该文件已在仓库中；`[规划]` = 仅声明职责，**尚未创建、未验证、未综合**。

```text
rtl/
  README.md                              [已存在]
  params/
    rtl_params.vh                        [已存在] 生成物，禁止手工编辑
    rtl_error_codes.vh                   [已存在] 手工维护的静态接口错误码（非生成物）

  core/                                  [部分已存在] 可综合逻辑
    dem_state_gen.sv                     [已存在] 按 bank 独立推进的确定性 DEM 状态序列，逐项匹配 Python
    dem_addr_gen.sv                      [已存在] 物理地址 → 逻辑位置（主/子阵列的行列旋转与逆映射）
    swap_decode.sv                       [已存在] 粗码 + dither → 命令 k；裁剪、桥接零和交换、拆主/子计数
    dither_gen.sv                        [已存在] dither 码生成（统计验收，见 §4.3）
    unit_therm.sv                        [已存在] 每 slice 计数 + 逻辑位置 → 温度计掩码；采样 dither 的轨选择
    adc2_dec.sv                          [已存在] 原始后端码 → Q32 仓中心（round-half-even）
    recon_core.sv                        [已存在] 定点重构：Q30/Q32 → 96 bit 受检累加 → floor 除法 → 20 bit 输出
    slice_alloc.sv                       [已存在] 从 18 个物理 slice 中选择 8 个，管理 A/B 采集与转换所有权
    ctrl_fsm.sv                          [已存在] 相位控制与样本流水调度（固定延迟）
    div_floor.sv                         [已存在] 全核**唯一除法**：有符号 floor，恢复余数法，迭代 9 拍
    rdac_drv.sv                          [已存在] 数字开关控制**输出**（薄寄存器扇出；不是模拟驱动电路）
    weight_store.sv                      [已存在] 权重存储及其**读/写窗口**接口
    calib_regs.sv                        [已存在] 标定系数载入、校验与生效控制
    status_regs.sv                       [已存在] 粘滞溢出/剪裁标志与错误寄存器

  top/
    sar20_digital_core.sv                [已存在] 顶层接口与模块集成
    sadc_enc.sv                          [已存在] SADC 温度计码 → 二进制粗码（见 §3.1 的层级决定）

synth/                                   [已存在] 仓库级综合：DC 脚本 + VM 侧驱动（库 = tcbn28hpcplus 28nm TT 0.9V）
sim/                                     [已存在] 仓库级仿真子树（**不在 rtl/ 内**）
  run_vcs.sh                             [已存在] VM 侧 VCS 驱动
  tb/                                    [已存在] RTL testbench（p1_tb / p2_tb / p2_smoke_tb / p3_top_tb）
  ref/                                   [已存在] 独立参考实现（dem_closed_form / recon_rtl_mirror）
  vectors/                               [已存在] 黄金向量与元数据
  artifacts/                             [生成物，已 gitignore]
docs/rtl/
  RTL_ARITHMETIC_CONTRACT.md             [已存在] 冻结的算术契约（P0）
  P1_INTERFACE.md                        [已存在] P1 接口与向量格式（冻结）+ 完成记录
tools/
  export_rtl_params.py                   [已存在] Config → 参数包／寄存器镜像／黄金激励
  export_rtl_vectors.py                  [已存在] Config → P1/P2 穷举与黄金向量
  run_rtl_sim.py                         [已存在] 同步 → 编译 → 运行 → 取回日志
tests/unit/
  test_rtl_export.py                     [已存在] 参数包与算术契约门禁
  test_rtl_vectors.py                    [已存在] P1 向量与其覆盖账本门禁
```

---

## 3. 关键职责的归属（明确到文件，不留空）

下表**同时列出已实现与已规划**的职责归属，以便接手者知道"该改哪个文件"。
第三列的状态标记与 §2 的目录树同义：`[已存在]` 才有可运行代码。

| 职责 | 归属 | 状态 | 备注 |
|:---|:---|:---|:---|
| DEM 状态序列 | `core/dem_state_gen.sv` | [已存在] | **先使用、后推进**；`sid(n) = (n·2654435761) mod 512`，用 `+433 mod 512` 递推实现 |
| 开关码译码（k → unary + 置换） | `core/swap_decode.sv` + `core/unit_therm.sv` | [已存在] | 名义守恒不变量在此体现 |
| RDAC 开关**控制输出** | `core/rdac_drv.sv` | [已存在] | 只有寄存器扇出。**实际模拟驱动电路不在本子树内** —— 不要把它与数字开关命令混为一谈 |
| SADC 判决编码 | `top/sadc_enc.sv` | [已存在] | 见 §3.1 |
| 后端译码 | `core/adc2_dec.sv` | [已存在] | 唯一运行时舍入点（round-half-even） |
| 定点重构 + 唯一除法 | `core/recon_core.sv` + `core/div_floor.sv` | [已存在] | 唯一真除法（floor）；溢出粘滞不 wrap |
| slice 选择与所有权 | `core/slice_alloc.sv` | [已存在] | 不变量 `conv[n] = acq[n-1]`；同一样本内 slice 不重复 |
| 权重存储与读出 | `core/weight_store.sv` | [已存在] | 读写窗口见 §3.2 |
| 标定系数载入与生效 | `core/calib_regs.sv` | [已存在] | 未完成载入时**必须拒绝转换**（`cfg_ready = 0`） |
| 状态与错误 | `core/status_regs.sv` | [已存在] | 粘滞；只有复位/显式清除能解除 |

### 3.1 `sadc_enc` 的层级决定（记录一个选择，而不是留空）

**决定：编码器放在数字核外面**（`rtl/top/sadc_enc.sv`），数字核继续接收已经编码好的
`sadc_code`。理由有三条：

1. 计划 §4.1 的顶层端口已经把输入定义为 `sadc_code`，改变它会连带动摇 P1 的全部向量契约；
2. P1 的黄金向量是**码域**的，把编码器排除在外才能让核级测试用码域激励穷举 ——
   这正是 P1 能做到 46 万次比对的原因；
3. 编码器本身是纯组合的优先编码器，它的非理想性（比较器亚稳态、阈值失配）属于**模拟域**，
   应和比较器阵列一起由 Spectre 子模块承接（计划 §2.1）。

因此 `sadc_code` 的**正确性由模拟侧负责**，数字核只对"给定码之后的一切"负责。

### 3.2 `weight_store` 的读写窗口（名字不能替代契约）

`weight_store` 提供两套互斥的访问：

| 模式 | 条件 | 行为 |
|:---|:---|:---|
| **载入** | `calib_load = 1` **且** `cfg_ready = 0`（即未在转换中） | 允许写入；写入期不产生有效输出 |
| **只读** | 转换进行中（`busy = 1`） | 只允许读；写请求被忽略并置 `err_cfg_write` |

载入完成后由 `calib_regs` 做合法性校验（权重为正、`ΣW < 2^60`、`adc2_min_q < adc2_max_q`，
见算术契约 §5），校验通过才置 `cfg_ready = 1`。**`cfg_ready = 0` 时 `dout_valid` 必须恒 0。**

---

## 4. 硬约束

1. **`rtl_params.vh` 是生成物。** 改参数改 `Config`，然后重跑导出器。三份产物（`.vh`／
   `params_*.json`／`registers_*.json`）由 `--check` 漂移门禁守着，
   `tests/unit/test_rtl_export.py` 与 `test_rtl_vectors.py` 会在产物过期或被人手篡改时变红。

2. **RTL 内不做浮点，也不做浮点伏特运算。** 但**允许且必须**传递带明确位宽、符号与缩放定义的
   定点编码 —— `F`/`O`/`I` 本身就是 Q32 归一化 `V/Vfs`，有确切的物理对应关系。
   禁止的是把浮点行为模型直接搬进 RTL 的伏特值，不是禁止用整数承载模拟观测量。
   单位换算在模型与导出侧完成；进入 RTL 的归一化编码与标定整数一律由算术契约定义。

3. **算术一律以 `docs/rtl/RTL_ARITHMETIC_CONTRACT.md` 为准。** 该契约冻结后不就地修改，
   要改就新写一份 ADR。（已有一处措辞修正的记录，见契约 §1.1 的引用块。）

4. **每块逻辑都要有能推翻它的测试。** 而且——

   > **反例必须由预期的比较器或断言检出目标错误。编译失败、测试根本没跑、环境错误，
   > 都不算有效检出。**

   本仓库已有的反例（P1 实测）：改坏 LCG 常数一位 → T1 报 1024 处错；反转温度计单调包含方向
   → T5 报 258048 处错。二者都由 `chk()` 的比较器报出，不是靠崩溃或超时。

   参数导出器侧另有两条必须**分开**验证的过期检测：
   **(a) `Config` 改动后旧产物被判过期；(b) 生成物被人手篡改后判不一致。**

5. **不把"规划"写成"已存在"。** 新增模块时同步更新 §2 的状态标记；未创建的模块不得出现在
   依赖它的接口描述里。

---

## 5. 尚未开始（如实登记）

* **P4 可选加速器**：`gain_acc`（增益 LMS）与 `pretrack`（因果队列 + 3 抽头）—— 按 D4 一期不排期；
* **SpyGlass lint / Formality 等价 / PrimeTime STA**：`synth/` 目前只做 DC 综合与面积/时序基线，
  形式与签核工具尚未接入；
* **顶层 L3 的 dither 注入**：`sar20_digital_core` 没有 dither 输入端口，链路级 bit-exact 靠
  仿真期 `force`（见 `sim/tb/p3_top_tb.sv` 模块头）—— **该路径不覆盖 `dither_gen`**；
* 覆盖率采集（`-cm` 已验证可用，P1 未开启）；
* `shuffle_causal` 调度：RTL 只实现确定性 A/B ping-pong，洗牌调度保留为仿真实验；
* 自校正/后台校准硬件（计划 §8 R9：KTC 观测支路被算术契约拒绝，需先补 ADR）。

---

## 6. 参考

* 算术契约：[`docs/rtl/RTL_ARITHMETIC_CONTRACT.md`](../docs/rtl/RTL_ARITHMETIC_CONTRACT.md)
* P1 接口与完成记录：[`docs/rtl/P1_INTERFACE.md`](../docs/rtl/P1_INTERFACE.md)
* 转换计划（含 P0–P7 与风险账本）：项目工作区 `03_工程设计/20bit40M_行为模型转可综合RTL计划.md`
* 已有的独立参考实现：[`sim/ref/dem_closed_form.py`](../sim/ref/dem_closed_form.py)

## 2026-09-19 RTL review fixes

[工程复核与修复报告](../docs/rtl/REVIEW_20260919.md) 汇总基线问题、修复和仍未闭合的目标。
[ADR 0016](../docs/adr/0016-rtl-configuration-and-dither.md) 定义完整配置装载/提交协议与 dither PMF。
运行 `python tools/run_open_rtl.py` 可编译执行 P1/P2/P3 和两项新增 RTL 回归。
