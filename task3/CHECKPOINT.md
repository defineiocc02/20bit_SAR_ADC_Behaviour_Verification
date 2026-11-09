# 模块三工作检查点

- 设计基线为 `b592ba825fa2c156f58fab5b2f528d90e49cacff`。模块三的回滚范围为 `task3/`；保持与模块二和整体顶层的公开端口一致。
- [数字合同](SPEC.md)、[RTL](rtl/caaz20_pair.sv)、独立队列 oracle 与 [Verilator 逐拍收据](evidence/rtl/result.json)已交付；[行为样本域数据及图](evidence/behavior/caaz_effect_summary.json)已归档。
- 有限深度证明通过情况以 GitHub 数字工作流为准。整体 SKY130HD 物理状态跟随[总交付](../task2/physical/README.md)；模拟换相、CBIAS 存储和真实建立、跨域及保持验证需要模拟宏和目标工艺。
