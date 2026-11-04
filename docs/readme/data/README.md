# README 结果图的数据

这些小文件是绘图所需的冻结数据，不是器件测量或完整仿真产物。

- `calibration_40m.npz` 保存两种噪声条件下的留出输入、标称权重输出和拟合
  权重输出，各 16384 点；`calibration_40m.json` 记录 2026-11-04 对基线
  `40ae71d` 的独立审计来源、原始文件 SHA-256、完整配置、训练摘要、留出
  指标和异常计数。训练各 16384 点，参考为精确外部已知正弦。两条曲线的
  留出数据与训练数据不同，但使用同一个模拟物理池。完整原始训练和观测
  保留在当次审计产物中；本目录保存重画结果图所需的子集。
- `interleave_40m.npz` 保存固定 A/B 与随机 8-of-18 调度的四组误差序列，
  `interleave_40m.json` 保存条件、误差和指定频点的杂散。它们来自集成
  基线 `d809da6`；没有使用逐 slice 失配估计器。可在当前工程运行
  `../reproduce_interleave_40m.py --out <新目录> --compare-frozen` 复现并
  对照序列；建议设置 `OPENBLAS_NUM_THREADS=1`。

`../make_unified_figures.py` 从这些文件重画对应曲线，
`tests/unit/test_readme_effect_evidence.py` 独立检查序列与数字指标是否一致。
不同 NumPy/BLAS 环境重新拟合时末位可能不同；画图不需要重新拟合。
