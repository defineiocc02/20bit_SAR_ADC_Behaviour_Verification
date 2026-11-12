# 快速上手

目标：20 分钟内跑出一次结果、看懂一笔样本、复现一组对照。

所有命令在仓库根目录执行。本地没有仿真器，**Python 部分不需要 VCS**；
只有 `verification/` 的 RTL 对拍需要远端主机（见 §5）。

---

## 0. 环境

```bash
python3 -V                    # 本仓库用 3.13 验证；>=3.10
python3 -c "import numpy, scipy, matplotlib, yaml, pytest"
```

缺依赖时：`python3 -m pip install numpy scipy matplotlib pyyaml pytest`。

Python 侧不需要安装包，用 `PYTHONPATH=src` 即可；装了也可以 `pip install -e .`。

---

## 1. 最小运行（10 秒）

```bash
PYTHONPATH=src python3 -m adi_model simulate --samples 64 --output /tmp/adi-demo > /tmp/adi-demo.json
```

**stdout 是一份完整的来源档案**（推断表、启用/未建机制、精度声明），不是结果摘要——
所以上面把它重定向到文件。真正的结果在 `--output` 目录：

| 文件 | 是什么 |
|:---|:---|
| `codes.csv` | 20 位输出码（offset-binary） |
| `raw/` | 逐笔模拟记录：粗码、ADC2 码、余差、RA 输出 |
| `metadata.json` | 本次运行的配置、seed、启用项、未建模项 |

**这次运行能说明什么**：定点解码链路是通的、码宽是 20 位。
**不能说明什么**：没有校准、没有 DEM、没有 dither，`precision_claim` 字段写的是
"code width only"。

---

## 2. 看懂一笔样本（30 秒）

```bash
PYTHONPATH=src python3 tools/trace_one_sample.py --out /tmp/trace
```

输出 `trace.json`，把**同一笔样本**在有/无 dither 两种情况下的中间量并排放：

```
输入 → SADC 判决 → RDAC 事件码 → 参考电荷 → 余差 → ADC2 码 → 重构误差
```

这一笔是用来核对"下段 dither 动了量化器输入、上段动了码"的。
**它只有一笔，不能用来谈性能。**

---

## 3. 复现一组对照实验（约 3 分钟）

```bash
PYTHONPATH=src python3 tools/run_p4_ablation.py \
  --static-points 256 --static-repeats 32 --dynamic-n 8192 \
  --out docs/reviews/20261113_p3p4/data
```

11 个配置，同一颗芯片、同一输入网格，只切换 DEM / dither / 校准。
配置定义在 `experiments/configs/matrix.py`，不在工具脚本里。

结果读 `p4_ablation.json` 的 `configs[].static/dynamic`。
**每行都必须看 `status`**：`NOT_RUN` 不是通过。

重画图（必须与上一步同参数，否则图与表对不上）：

```bash
PYTHONPATH=src python3 tools/run_review_figures.py \
  --points 256 --repeats 32 --dynamic-n 8192 \
  --out docs/reviews/20261113_p3p4/figures
```

---

## 4. 改一个参数，看它影响什么

| 想改什么 | 改哪里 | 必须先跑什么 |
|:---|:---|:---|
| 失配强度 | `DefaultParameters(unit_mismatch_std=...)` | `tests/unit/test_dem3d.py` |
| 噪声开关 | `config_overrides={"ra_enable_noise": False}` | `tests/unit/test_pipeline_smoke*` |
| 片选策略 | `experiments/configs/matrix.py` 的 `uniform_plan` | 指纹 `--check`（若改到主链） |
| dither 字 | `dither_words=` 传入 64 以内的整数数组 | `tests/unit/test_dither_charge_port.py` |

改完先跑 **行为指纹**：

```bash
PYTHONPATH=src python3 tools/behavior_fingerprint.py \
  --check docs/restructure/behavior_fingerprint.json
```

- `exit 0` → 主链行为没变（纯结构改动）
- `exit 1` → 有漂移，会打印第一处不同的 `group.field`
- 想有意更新基线：`--force`，并在提交信息里说明为什么

---

## 5. RTL 对拍（需要远端仿真器）

本机没有 VCS。命令会连 `vm-meow`（配置见仓库外的主机笔记）：

```bash
python3 -m verification.run_verification --level contracts   # 不需要仿真器
python3 -m verification.run_verification --level module      # 7 个模块，约 3 分钟
python3 -m verification.run_verification --level rtl_top     # 顶层 bring-up
```

结果在 `verification/reports/results_<level>.json`。
连不上或没有 license 时返回 `NOT_RUN`，**不会伪造 PASS**。

---

## 6. 工程质量检查

```bash
python3 tools/check_engineering.py     # 依赖方向/CAAZ 隔离/文档链接/结果身份/重复公式/入口
python3 -m ruff check .
python3 -m mypy src/adi_model
PYTHONPATH=src:. python3 -m pytest tests/unit tests/verification -q
```

---

## 7. 下一步读什么

| 你想知道 | 去看 |
|:---|:---|
| 整体架构、数据流、状态归属 | [`docs/design/ARCHITECTURE.md`](../design/ARCHITECTURE.md) |
| 术语与符号（SADC/RDAC/Cnorm/粗码…） | [`docs/guide/TERMS.md`](TERMS.md) |
| 出错怎么查 | [`docs/debugging/DEBUGGING.md`](../debugging/DEBUGGING.md) |
| 某项技术到底实现了多少 | [`docs/mechanisms/source_crosscheck_20261112.md`](../mechanisms/source_crosscheck_20261112.md) |
| 当前验收状态 | [`docs/acceptance/20261112-e14a0a0/report.md`](../acceptance/20261112-e14a0a0/report.md) |
