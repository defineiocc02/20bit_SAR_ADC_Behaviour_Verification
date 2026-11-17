# CAAZ 可选扩展

CAAZ（Chopping-Assisted Auto-Zero）对应 PKU VLSI 2025 的斩波辅助自零机制。这里只有显式输入等效 AZ、偶奇事务配对的 Python 和 RTL 实现。普通 AZ 在主包逐笔处理，保留原有速率。 [来源与边界](../../../docs/caaz/README.md) · [普通 AZ](../../../src/adi_model/behavioral/ra_az.py)

| 位置 | 内容 |
| --- | --- |
| [model/chopping_az.py](model/chopping_az.py) | 使用明确提供的 AZ 残差、保持和传递条件。活跃路径按相邻样本身份配对，保留错误标志与配对时间。 |
| [rtl/caaz_cycle_ctrl.sv](rtl/caaz_cycle_ctrl.sv) 与 [rtl/caaz_pair_processor.sv](rtl/caaz_pair_processor.sv) | 可选控制与定点配对组件。通过 [显式验证 runner](../../../tools/run_open_rtl.py) 选取扩展夹具。 |
| [tests](tests) 与 [tb](tb) | 独立测试和数字配对夹具。 |

主 Python 包和 v21 源清单按独立合同运行。扩展夹具可单独验证 CAAZ，模拟保持电容与实际斩波开关仍需要独立的物理或宏模型证据。

从仓库根目录运行扩展的 Python 测试。`flow` 路径用于解析共享夹具：

~~~bash
PYTHONPATH=src:flow python -m pytest -q subsys/extensions/caaz/tests/test_chopping_az.py
~~~

数字扩展夹具的注册名包括 `caaz_pair_vectors_tb`。具体工具、源和结果由所选入口的 manifest 绑定。 [组件执行说明](../../../flow/sim/README.md)
