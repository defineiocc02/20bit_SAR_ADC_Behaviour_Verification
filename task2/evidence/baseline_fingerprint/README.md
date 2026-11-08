# 数值指纹：严格复现与跨平台检查

冻结指纹和实验数据不变。本机默认 strict，八项标量逐字段精确比较。
GitHub Linux CI 显式设置 `ADC_BASELINE_FLOAT_MODE=portable`：频谱允许绝对
`1e-9 dB`，线性度允许绝对 `1e-9 LSB20`；电压步长、LSB、版本和种子仍精确比较。
它只处理跨平台 FFT／SVD 的浮点尾数，不改变 ADC 性能门槛，不放宽 RTL 或整数码对拍。

本轮实际 Linux 差值最大为 `3.817036e-11 dB`，静态 INL 差为 `7.965406e-12 LSB20`。
容限外正负变化、NaN／Inf、标度和版本变化均有定向测试，禁止更新冻结值来消除失败。

```sh
python tools/snapshot_baseline.py --check             # strict
python tools/snapshot_baseline.py --check --portable  # 跨平台浮点标量
```

`--portable` 只能配合 `--check`，不能写入新指纹。完整整数与 RTL 的零差规则不变。
