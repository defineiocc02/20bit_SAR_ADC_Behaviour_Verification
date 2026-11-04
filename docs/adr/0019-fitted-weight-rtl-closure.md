# ADR 0019：外部拟合系数到 RTL 结果的闭环

日期：2026-09-28。状态：已实现，依据为 `calibration_fit_tb`、Python 回归及随附产物摘要。

## 决策

论文公开的边界是片外求系数、片上数字权重校正。因此训练器保留在 Python；
训练输入仅为数字观测和独立参考输入，不把失配电容真值送给校正算法。
`FrozenCalibration` 记录训练摘要、矩阵秩、条件数和残差。新导出器先核对
阵列几何与 `Vfs`，随后仅按现有算术契约量化一次：物理有效权重 `C/Cf`
为正 Q30，偏置与 ADC2 范围为归一化 Q32。输出为寄存器 JSON，旁路
manifest 绑定冻结训练文件和寄存器文件各自的 SHA-256。校正核不参与训练。

```sh
PYTHONPATH=src python tools/export_fitted_rtl.py \
  --config paper_literal --dither-mode sampling --dem-enable --dem-mode permute \
  --frozen sim/vectors/fitted18_frozen.json \
  --out-registers sim/vectors/fitted18_registers.json \
  --out-manifest /tmp/fitted18_export_manifest.json
python tools/run_open_rtl.py --tops calibration_fit_tb
```

`--check` 要求两个输出文件已存在且逐字节相同；首次使用先不带该参数生成。
`fitted18_frozen.json` 是独立训练得到的系数快照；`fitted18_registers.json`
是实际可装载整数，不把这两种文件互称。随附 `fitted18_manifest.json`
记录六个固定仿真产物的字节摘要。
固定例可用 `OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python
tools/export_fitted_rtl_vectors.py --out sim/vectors` 重建；跨 BLAS 实现时浮点
分解的末位可能不同，CI 因此验证已提交产物的哈希与功能，而不要求重新拟合后逐字节相同。

## 可复核的回归

固定仿真例使用 18 个物理 slice、每 slice 71 个有效单元、8 个活动 slice、
采样 dither 和 permute DEM；训练 2048 笔、设计矩阵秩 1279/1279、条件数
710.07。另用不同频率/相位的 128 笔留出样本。导出后的 1278 个 Q30
权重经 `weight_store` 写入，三个 Q32 标量与控制位经 `calib_regs` 写入、
完整性校验并提交，再由真实 `recon_core` 处理留出样本。128 笔码和结果标志
均与独立定点参考逐位一致。仿真例中留出误差标准差由名义权重的
420.19 µV 降到拟合权重的 72.93 µV；这些数字只描述该固定模拟失配样本，
不是论文器件的测量指标。复核入口为 `tools/run_open_rtl.py`、
`tests/unit/test_fitted_rtl_artifacts.py` 和 `tests/integration/test_weight_calibration.py`。

原参数导出器的 `stimulus_summary.register_payload_sha256` 现取自真正的
`registers_*.json` 字节，避免把参数元数据的哈希误标为权重寄存器哈希。
已有黄金码流本身未因此改动。

## 综合相关边界

`weight_store` 对允许的最大阵列 32×128 及合法单元权重 `<2^47`，总和
严格小于 `2^59`，自然满足 `<2^60`。因此原每次写入的 64-bit 全表和
更新/比较在当前参数范围不可触发；RTL 以可证明的 elaboration 常量
绕过该检查，同时保留超出该界时的动态保护。其他写窗、索引、权重正值和
完整性检查不变。`sum_all` 仍保留在源级以兼容调试，但综合器应能剪除其
不可观测路径。这里没有获得生产库的门级面积/时序报告，不能据此宣称
640 MHz 或面积改善已签核；下一步需在目标工艺库做综合、STA 与等价检查。

该闭环验证数字校正实现及其接口，不等价于模拟采样、参考、比较器、
20-bit 建立精度或专利全部可选模拟实施例的晶体管级复现。
