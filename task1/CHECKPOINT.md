# 20b10mtask 模块一检查点

- 本分支是同学数字任务交付树，从主线 `bc9a16a596dd37d1172d29cbda23979c222bf63b` 建立。任务、接口和九个月计划见 [SPEC.md](SPEC.md)。
- 切片调度、同节点 DEM 和 Fig.21 跨段候选已有 Python、RTL、独立电荷参考与逐事件对拍。工程假设、名义补偿及未完成的模拟绑定见 [BRIDGE_SPEC.md](BRIDGE_SPEC.md)。
- 电容失配统一按 [LMS 四组标准](EXPERIMENT_STANDARD.md) 评价。2%／3% 固定失配五颗配对的 14 位聚合统计 INL／DNL 均改善；20 位逐状态单调性仍需处理。见 [INL／DNL](evidence/inl_dnl/README.md)。
- 12 位 ADC2 的量程、理想减性 dither 和邻码残差控制已有独立分析；实际专利物理 dither、ADC2／RA 宏及完整系统仍未接入。[结论与下一步](ADC2_DITHER.md)
- SKY130HD 已有 GDS 和抽取后 STA：跨段桥接顶层在 6.25 ns 的 TT 角 setup／hold 通过，非桥接顶层 setup 仍有违例。最新结果以[物理证据](physical/SKY130HD.md)为准；详细路由 DRC 不等同独立 DRC／LVS。未来 180 nm BCD 结果不得由 SKY130 代理冒充。
