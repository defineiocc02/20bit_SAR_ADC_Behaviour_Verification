# CAAZ 扩展（隔离）

本目录保存 CAAZ（Chopping-Assisted Auto-Zero，PKU VLSI 2025 C32-1）的源码、测试与历史说明。
**主工程 `src/adi_model/`、`rtl/`、主 pytest 不依赖本目录。**

## 来源与迁移

| 原路径（main `bc9a16a`） | 现路径 | 说明 |
|:---|:---|:---|
| `src/adi_model/chopping_az.py` | `extensions/caaz/model/chopping_az.py` | 仅把相对 import 改为 `adi_model.*`；逻辑未改 |
| `tests/unit/test_chopping_az.py` | `extensions/caaz/tests/test_chopping_az.py` | 仅改 import 路径 |
| `rtl/control/caaz_cycle_ctrl.sv` | 保留在原路径（主 filelist 已移除） | 扩展 filelist 见 `rtl_sources.f` |
| `rtl/calibration/caaz_pair_processor.sv` | 保留在原路径（主 filelist 已移除） | 同上 |
| `docs/caaz/`、`docs/randomization_caaz/` | 保留原路径 | 历史证据，主 README 标为扩展 |

> 原文件 SHA 与历史见 git：`git log --follow -- src/adi_model/chopping_az.py`。

## 普通 AZ 去哪了

主工程的普通逐笔 RA AZ 在 `src/adi_model/behavioral/ra_az.py`：
每笔刷新、全速率、N→N，不做偶奇配对。它**不是** `conventional_az` 的两点平均/降采样。

## 运行扩展测试

```bash
PYTHONPATH=.:src python -m pytest -q extensions/caaz/tests/test_chopping_az.py
```

## 扩展 RTL filelist

```text
rtl/control/caaz_cycle_ctrl.sv
rtl/calibration/caaz_pair_processor.sv
```

主 `rtl/rtl_sources.f` 不含上述两行。
