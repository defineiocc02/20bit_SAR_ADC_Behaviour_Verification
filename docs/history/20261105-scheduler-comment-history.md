# 旧注释归档：非现行状态入口

基线为1e973cb。以下保留迁出生产代码的历史叙述，不作为本轮重新验证或现行参数定义；本文中的旧行号和库外backup路径只说明归档上下文，完整原文件可用 `git show 1e973cb:src/adi_model/<文件名>` 取得。现行解释以生产代码、ADR和 [STATUS](../STATUS.md) 为准。

# 迁出源码的历史叙述（仅作版本记录）

本轮未改算法/默认值/错误文案。原文完整备份在before/src/adi_model/{mapper,scheduler,dem,timing}.py；对应SHA见before/manifest.json。

- mapper原模块把64等权unary/512状态说明推广到全部DEM；当前注释分清Mapper兼容接口与63+8 split译码、名义守恒与使用频次。
- mapper.encode原注释记录2026-09-25标量与分数粗码检查经过、重复注入导致约50dB SNDR的历史故事。当前保留边界设计理由，不把旧实验数字当当前证据；异常字符串保留原字节以保持API兼容。
- dither_transfer_code原docstring含v6.1/F3的42.7uV误差以及stage21溢出32.7%→16.0%、RMS16308→5699uV。这些属于旧参数/实现下的历史记录，本轮未复跑；当前源码只解释Δ1到ΔDAC的单位换算和不同dither端口。
- scheduler.make_scheduler原docstring叙述2026-09-11字段无读取点及与旧ra_gain_model问题的关联。当前说明有效配置选择、显式冲突拒绝与rtl16旁路，不再用旧缺陷叙事解释现状态。
- ShuffledScheduler原注释声称特定样本量足够快；当前仅给循环复杂度，运行时间由实际命令输出报告。

当前事实：完整512状态遍历不推出63单元等使用；随机物理分配不推出使用偏置消失；杂散下降不推出SNDR/RMS改善。当前baseline与历史d809da6的差异通过旧helper单独替代的审计控制确认，生产源码未回退。
