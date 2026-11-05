# README 结果图的数据

这些小文件是绘图所需的冻结数据，不是器件测量或完整仿真产物。

- `calibration_40m.npz` 保存两种噪声条件下的留出输入、标称权重输出和拟合
  权重输出，各 16384 点；`calibration_40m.json` 记录 2026-11-04 对基线
  `40ae71d` 的独立审计来源、原始文件 SHA-256、完整配置、训练摘要、留出
  指标和异常计数。训练各 16384 点，参考为精确外部已知正弦。两条曲线的
  留出数据与训练数据不同，但使用同一个模拟物理池。完整原始训练和观测
  保留在当次审计产物中；本目录保存重画结果图所需的子集。
- 当前 `interleave_40m.npz` 保留四个固定 A/B／generic 随机调度设置的
  **内部浮点重构误差**，包括失调与电容/增益失配；`interleave_40m.json`
  的 `rows` 与这些数组对应。它们已按当前 `sid[8]` bridge 合同重建，
  不再使用旧合同数字。原始 `d809da6` 冻结原字节保存在
  `historical/interleave_40m_d809da6.{json,npz}`，新旧 SHA、逐数组和逐键差异见
  `historical/interleave_40m_bridge_contract_migration.json`。
- 当前 `interleave_timing_40m.npz` 保存三条**最终 20 位整数码**对应的单边
  线性功率谱及 `frequency_hz`，供首页三策略图读取；
  `interleave_timing_codes_40m.npz` 保存同一三条 uint32 码序列。
  `interleave_40m.json` 的 `timing_comparison` 记录固定 A/B、generic 空闲池
  随机选择与结构 RTL allocator 的 Python 合同。它们共享 seed42 的物理时偏池、
  输入和输出量化；结构项不表示本轮重新执行了 SV DUT。
  参考误差 RMS 保留增益/相位偏差，SNDR 与 fit residual RMS 则剔除拟合基波，
  不能将两种 RMS 当作同一指标。未使用逐 slice 失配估计器。
- 在仓库根目录用现有工程 Python 环境重新仿真：
  `python docs/readme/reproduce_interleave_40m.py --out <新外置目录> --compare-frozen`。
  四个浮点误差数组保持 `rtol=0, atol=1e-14 V`；三条整数码及功率谱精确比较，
  所有 key 和形状都必须一致，不能删除失败 case。JSON 自动记录实际生成时的
  `source_head`、工作区 dirty 状态和含 producer 的 `source_sha256`；这些字段标识
  生成快照，不手改成后续提交。未来 HEAD 不同不自动意味着数组已经失效。
- `structural_calibration_holdout.npz` 保存两个静态失配种子的独立留出输入、
  样本 ID 及结构 RTL 校准前后输出码。`structural_calibration_summary.json`
  记录无额外噪声的训练／留出条件、结果和源文件哈希；
  `structural_calibration_noise_summary.json` 记录假设 48 µV RMS 输入等效
  噪声的另一组测试。它们是数字 RTL 与静态行为夹具的仿真，不是芯片测量。

`../make_unified_figures.py` 从这些文件重画对应曲线和当前校准流程图，
`../make_contract_figures.py` 重画 bridge 与结构留出误差图；
`tests/unit/test_readme_effect_evidence.py` 独立检查序列与数字指标是否一致。
不同 NumPy/BLAS 环境重新拟合时末位可能不同；画图不需要重新拟合。

## 本轮结构留出摘要

两份 structural_calibration*_summary.json 已由本轮最终冻结源码实际重新运行后更新；没有手工刷新旧运行的来源摘要。旧标准差、观察数据、冻结系数及寄存器摘要保持相同，新加的 mean/RMS 从实际最终整数码中心误差计算。raw留出轻量NPZ字节保持不变，重绘可独立重算统计。完整过程与来源对照见[本轮复核](../../reviews/20261105-readability-maintenance.md)。

本轮图1–4由 ../make_overview_figures.py 只读绘制；旧 unified_7 布局已从首页撤下，预维护图副本及摘要见 historical/interleave_figure_archive.json。
