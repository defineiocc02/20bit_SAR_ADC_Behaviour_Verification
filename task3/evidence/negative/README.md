# CAAZ 负注入联合重放

新数据来自同一 3% 固定制造池，DEM／dither 位图、冻结 Q28 系数与样本号保持同笔。
[四组同带宽噪声图](joint_noise/caaz_joint_noise.png)比较独立 AZ、仅斩波、共享 AZ 与保持误差；
[原始数据及参数](joint_noise/summary.json)明确人工噪声和保持假设。
[数字逐码验收](../../../task_integration/evidence/negative/README.md)与行为效果分开。

原始转换 10 MS/s，非重叠配对输出 5 MS/s。先在斩波后坐标逐笔去扰重构，
再按两笔样本号、极性及 AZ 代配对。模拟输入斩波、CBIAS 与捕获保持开关没有在 RTL 实现。
本组受控负电荷源和共享子权重仍属工程假设，不声称复现北大模拟宏。
