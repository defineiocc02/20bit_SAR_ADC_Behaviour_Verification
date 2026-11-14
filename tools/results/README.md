# 历史验收快照与旧图

`results.json`是2026-11-05旧维护流程的52条判据快照，属于af8功能合同／9bdf文档保留的历史记录。原运行源、配置与判据由[原维护记录](../../docs/reviews/20261105-independent-maintenance.md)追溯；当前已退役`adi-run-all`及该套隐式配置，不能再称它是当前验收合同。

旧快照SHA256仍为`5ff9ef9ade0eac30d3a9851a4c52b0cdff22f0e2374d3a2bac5b08a84e394123`；这里保留的后续历史快照为`3724a8ada35da8d8bba59696f9f025356be379ba9a515e6fcbfa44450741d092`。不修改结果数值、字段或来源为新主线身份。

`fig/`是此前的历史图，并未随上述JSON重画；不能把它们当成该快照或现行实现的配套效果。首页历史图与数据另在`docs/readme/`按各自身份说明。当前源的运行、校准和验证见[新复现入口](../../docs/archive/REPRODUCING.md)与[状态表](../../docs/archive/digital_status_matrix.md)，新产物写库外并冻结后再发布。

旧`sim/vectors/`的35个文件已按原字节迁至`docs/history/legacy_vectors_af8/`，配套README与SHA256SUMS用于核身份；旧manifest内部路径不改。另9份历史配置／系数／寄存器JSON保留原路径原字节。它们退出当前运行配置，完整历史实验仍须从[Git af8快照](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/tree/af8aa37d43db40eb896e369305883ec89786547b)恢复，不将历史bundle装作新schema2输入。
