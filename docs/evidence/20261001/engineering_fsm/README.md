# 实际 FSM 功能轨迹（2026-10-01 CST）

官方 Verilator 5.020，零延迟 RTL、小几何重构、10 ns 激励时钟。三个 CSV 各175个真实边沿。输入在下降沿改变，CSV记录上升沿前输入和NBA后1 ps状态。真实源码 SHA 记录在 `source_sha256.json`；原始生成命令在 `command.json`。本档不证明物理时序、模拟性能或ASIC签核。

重构9接受、7输出、2取消(ID301/601)、1错误(ID401)、1提交同沿接收下一笔；所有有效输出延迟11拍。ID401保持此前合法word150并flags2。SAR完成345/201；phase首次复位零相与稳态wrap有区别。可用 `tools/run_engineering_fsm.py --out <new-directory>` 重跑，再用报告 `audit_figures/make_engineering_trace.py` 重算本归档并制图。历史22测试与这项新增测试的来源分别记录。
