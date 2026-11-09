# Fig.21 桥接候选数字验证

Verilator 5.30 对拍了 65,536 组桥接叶向量与 4,119 拍集成顶层事务，均与独立参考一致。[源码与向量摘要](result.json) [运行日志](run.log)

桥接叶全量覆盖 512 码、16 个保留列和 8 个纵向起始片。顶层覆盖同笔模式冻结、4,096 个连续派发槽、Fig.11 DEM 及旁路、错 ID、停顿、种子拒绝和清空在途。Q32 补偿与独立名义电容模型的差小于半个 LSB。漏加、重复加和实际位权路径额外加补偿的反例见 [定向用例](../../tests/test_rtl_bridge_checks.py)。

证据范围为数字候选和名义两节点关系。ADC2、RA 余量和实际重构消费者仍需在系统集成中验证。输出收据包含全部本机运行日志的摘要，此处保留 lint 与运行完成记录。

```sh
python task1/tools/run_bridge_checks.py --out /tmp/task1-bridge-recheck
```
