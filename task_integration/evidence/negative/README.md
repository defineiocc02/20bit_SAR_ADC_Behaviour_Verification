# 负注入合同：数字验收与接手入口

本版使用 `k=c−U`、子特征 `conv+sample`，同位使能两次计 2w。
Python／RTL 的整数一致性与模拟宏、目标工艺验收分开。

| 项目 | 本次检查 | 证据 |
| :--- | :--- | :--- |
| 片上重构 | 16,384 笔留出与整数 oracle 一致；全部 4,096 ADC2 中点；Q28 对浮点最大差 1 LSB20 | [结果](../../../task_reconstruction/evidence/negative/result.json) |
| 整体片上路径 | 8,192 次 ADC2 接收；4,096 笔旁路、2,048 对 CAAZ；在途峰值两笔 | [结果](on_chip/result.json) |
| 外部重构路径 | 正常稳态、旁路、配对、错 ID、负端余量与宏异常 | [结果](external/result.json) |
| 带噪联合 | 四组共 16,384 原始码和 8,192 配对结果逐码一致；前两组外部配对，后两组 RTL 配对 | [结果](joint_noise/result.json) |
| 独立 dither | 20,000 拍随机序列，覆盖种子、复位、清空、两代归属及 271 次下溢 | [结果](../../../task2/eight_rtl/independent_review/negative/result.json) |
| 模块一 | 有序组调度、同节点 DEM、Fig.21 候选分别对拍 | [基础](../../../task1/evidence/negative/module1/result.json) · [桥接](../../../task1/evidence/negative/bridge/result.json) |
| 形式与后端 | 本版 CI 另行执行；未取得当前提交的结果前不继承历史 PASS | [CI](../../../.github/workflows/task2.yml) · [物理流程](../../../.github/workflows/task2_sky130_lms_extended.yml) |

运行入口仍是 `task_reconstruction/tools/run_checks.py`、`task_integration/tools/run_checks.py`、
`run_lms_checks.py`、`run_joint_noise_rtl.py`。使用新建的仓库外 `--out`；工具缺失或断言失败会退出。
公开归档只保留结果、lint 和实际运行日志；含本机路径的原始编译日志在维护备份中，
其摘要仍保留在运行收据，不改写成已公开的完整构建工件。

下一步由学生绑定真实负注入的差分端／参考轨、AZ 捕获保持确认和 RA／ADC2 窗口，
再补有源电路非理想、MC、多角时序、功耗和 EM／IR。已有数学或数字结果不能代替这些验收。

本机完整 Python 回归为 **174 passed**（含 5 个收据解析定向测试），见 [日志](pytest.log)。
Ruff 规则、格式及 Git 空白检查通过；库层／模型层反向依赖规则 R1／R2 通过。
[注释率逐文件表](../../../trace/comment_review.csv)记录相对整理前 HEAD 的改前／改后统计；
[改动类别](../../../trace/change_review.csv)区分注释／格式与须做回归的实质源改动。
不把跨文件私有依赖提示或旧收据扫描称为全部绿色：旧源码摘要过期、未公开的原始编译日志、
外部容器条目均保留身份和范围。旧收据不重签，不把其 PASS 套到新源码。
