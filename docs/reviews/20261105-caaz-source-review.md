# CAAZ 可选来源扩展：注册、合同与阅读范围

扩展基线：`86106fc66083eafb7798402040b92c83625d2aad`。日期：2026-11-05。
本文登记另一器件的 chopping-assisted auto-zero（CAAZ）来源与本项目选定的工程合同。
实现状态以 [X02 机制清单](../../mechanism_inventory.json) 为准，来源阅读不计作 DUT 测试通过。

## 唯一来源和历史记录

| 逻辑来源 ID | 文档 | 页数 | SHA-256 |
|---|---|---:|---|
| `PKU_VLSI2025_C32_1_PAPER` | Wang 等，VLSI 2025 C32-1 论文 | 3 | `eb958cfa40eed665abfffe2c7e9fc96aa031473da1e22c762d836410207ff550` |
| `PKU_VLSI2025_C32_1_SLIDES` | 同题讲稿（北大、清华） | 42 | `5921d65cfae061ca6301a6104460c2eff3803245c1bc976acecac16adfa79759` |

题名为 *An 88.8dB-SNDR 6-MS/s Pipelined SAR ADC with A Closed-Loop Dynamic Amplifier Featuring Highly-Linear Full-Scale Output Swing*，
[DOI](https://doi.org/10.23919/VLSITechnologyandCir65189.2025.11075015)。作者按正文和封面登记；
PDF 编辑元数据不能代替论文作者。`C32_C32-1.pdf` 是讲稿的同 SHA 别名，只计一次。

原件、全文和页图均保留在仓库外。`ADC_SOURCES_DIR` 可提供按
`sources_manifest.json` 规范文件名组织的材料目录；公开账本不存私人路径。

[原 18 份/596 页记录](20261105-source-coverage.json) 保持不变，新增
[CAAZ 2 份/45 页阅读记录](20261105-caaz-source-coverage.json) 构成当前 20 份/641 页并集。
[原 49 个 Python/41 个 RTL 模块阅读记录](20261105-code-coverage.json) 同样保持历史身份，
新增模块及本轮重读文件登记在 [代码扩展覆盖](20261105-caaz-source-code-coverage.json)。
本轮扩展包括 3 个新增模块和 8 个既有模块的完整重读，共 2,984 行；
与历史记录的并集覆盖当前 50 个 Python、43 个 RTL 生产模块。
扩展登记时逐文件核对当前 SHA-256 和完整行范围，阅读状态与功能执行结果分别记录。
不能把新增资料写回旧记录，造成旧轮次已经阅读它们的印象。

## 来源机制与工程选择

- 来源 Fig.5–6 将输入 chopping、偶数周期 AZ、奇数周期保持同一 `CBIAS` 样本，
  以及完整数据合并和位权校正后的 `(even - odd)/2` 联系在一起。仅翻转 RA 残差或后级细码
  不能作为完整 ADC 输入 chopping 的替代证据。
- 成对抵消依赖两笔记录共享同一 AZ 样本，并具有对应的增益、保持状态和样本归属。
  一般化残差为 `(a_even - a_odd*h)*e/2 - a_odd*delta_e/2`，增益和保持误差会留下残余。
- 原图字面算子为 `(1-z^-1)/2`。先解调再平均只是对齐非重叠 pair 的等价工程表达，
  不是原文另画了一个数字乘 `(-1)^n` 模块。
- 当前工程选择 raw 40 MS/s、非重叠 pair 20 MS/s，pair 时间戳取两次原始采样的中点。
  两点信号响应幅度为 `abs(cos(pi*f/f_raw))`，群延迟为半个 raw 周期；
  该滤波并不是充分的抗混叠滤波，输出带宽与采集输入须另有合同。
- 全速滑动窗口会在跨 pair 时留下 `(e[k]-e[k-1])/2`；不能为每个 raw tick 都输出有效结果，
  同时声称所有输出都精确消除了该 AZ 项。

[20 条来源/工程要求](20261105-caaz-source-requirements.json) 和
[符号、方程与来源边界](20261105-caaz-source-equations.json) 保留披露、推导和工程假设的区别。
每条要求都指向 X02；Python/RTL 的状态只从单一机制清单读取，不把来源 `NOT_RUN` 改写为 `PASS`。

## 已执行的数字子范围

`X02.rtl_contract.status = DIGITAL_IMPLEMENTED` 只表示默认关闭的数字控制、
样本/AZ 归属和当前 bin-center/floor 码合同的非重叠配对已接入。
三个定向 testbench 已在 Verilator 5.30 执行：pair 算术和默认关闭路径、
上下文/复位/异常代际、Python 共享向量均通过。
它们分别完成 7,980、6,824、6,553 项检查；共享向量包含 15 种场景。
这些是定向数字回归结果，不把所在批次或全工程回归一并写成已通过。
总体完成度仍为 `PARTIAL_SAMPLE_DOMAIN`，模拟实现与来源性能边界见下文。
`CAAZ-17` 也保持部分完成：当前遇错后 sticky fault 停流，需 clear/reset 重启；
错误 pair 的 reject/drop 计数、下一 pair 自动 even/AZ 恢复及顶层
`fault_pulse`/`pending` 转接仍未实现。三个 testbench 通过不表示 20 条要求全部通过。

## 尚未由此关闭的边界

1. 公开论文与讲稿的 headline 均为 6 MS/s，但没有独立定义 raw 时钟与 pair 输出率关系。
   本项目不据频谱轴、FoM 或 averaging 字样反推原型内部 12 MS/s。
2. 原型 4.3 dB 总降噪包含 1.3 dB AZ 样本项和 3 dB 普通平均，不能全部归于 CAAZ，
   也不能作为本工程噪声幅度的拟合目标。
3. FGA/CP-FIA、储能电容、超轨开关、真实 AZ/1/f 噪声传递、动态输入 chopper 和保持泄漏
   需要电路参数及相应验证。样本域误差注入和数字配对不关闭这些模拟或可靠性事项。
4. 默认关闭的扩展保留普通 raw 路径；已实现 reset、clear、错误、丢样、重复 ID、极性和
   AZ 代际检查与出错停流。CAAZ-17 的计数、自动恢复和顶层异常脉冲/待配对状态读出仍待实现，
   不能把这些缺口算入现有检查的通过范围。
5. 来源器件与 ISSCC 2024 20 位/40 MS/s 研究目标分别登记；CAAZ 是来自其他作者工作的
   可选来源扩展，不是本仓库原创，也不写成 ISSCC 芯片原有结构。

逐页记录说明实际阅读和图像核查的范围，并保留来源歧义；不认证全文逐字转写绝对无误。
