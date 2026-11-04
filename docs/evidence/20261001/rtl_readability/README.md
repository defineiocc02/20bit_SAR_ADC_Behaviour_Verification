# RTL 可读性与受测硬件的连接证据

26文件有效token、字符串、语义pragma、预处理行与官方Verilator展开均与71e7d5a恒等。25文件只有空白/注释变化，自动生成参数头字节不变。本结果是有限词法与预处理恒等检查，不是Formality证明或新EDA实测。

`reviewed_result.json` 区分当前源文件内容摘要与历史受测89b9f815摘要。旧DCP/布线/功耗结果仍保留原来源。本档新增公开runner的3类strict lint与1项FSM bench实测日志；原22测试不以遗留本机日志冒充本轮重跑。CI会重新运行现注册23项。

复核：`VERILATOR=verilator python tools/audit_rtl_readability.py --out <new-directory>`；负控：`pytest tests/unit/test_rtl_readability.py -q`。
