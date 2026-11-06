# 模块一验证环境

| 阶段 | 运行环境 | 证据范围 |
| :--- | :--- | :--- |
| Python 单测和行为实验 | Python 3.12，NumPy、SciPy、Matplotlib 和 pytest，依赖见 [requirements.txt](../requirements.txt) | 含独立两节点求解及定向效应实验，实际 Python／NumPy／Matplotlib 版本与源码摘要见 [实验收据](../evidence/effects/summary.json)。 |
| RTL 仿真 | Verilator 5.030，使用单时钟测试台 | [RTL 对拍入口](../tools/run_rtl_checks.py) 检查 lint、编译、实际运行及计数，保存工具版本、源和向量摘要。 |
| 当前数字工艺 | GitHub Actions 的公开 SKY130HD 工具链；TT、25°C、1.80 V | 实际综合、GDS、提取与 STA 状态以 [物理记录](../physical/SKY130HD.md) 为准。数字时钟 6.25 ns，基于 100 ns 内 16 个 tick 的设计假设。 |
| 目标工艺 | 180 nm BCD 的库、角、宏模型及 I/O 时序待指定 | 目标综合／STA、GDS、DRC／LVS、功耗和模拟后仿待取得所需输入后运行。 |

调度、映射与复位在同一时钟域。Verilator 对拍验证既定数字接口；选择器优化的同步二值等价证据见[优化记录](../physical/SELECTOR_OPTIMIZATION.md)。宏侧跨域、CDC／RDC 及模拟建立仍需在系统集成阶段验证。
