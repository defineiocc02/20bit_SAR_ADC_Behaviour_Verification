# W0-G CAAZ 隔离清单与普通 AZ 保留

> 目标：主工程**完全不依赖** CAAZ；普通逐笔 RA Auto-Zero **必须保留**；CAAZ 源码/方案/证据/回放入口**完整可查**。
> 关键区分：`conventional_az`（main 现有）含两点平均/降采样，**不是** ADI 的普通逐笔 AZ；不能拿它顶替，也不能把整个 `chopping_az.py` 删掉。

---

## 1. 两个概念

| 名称 | 来源 | 行为 | 主工程 |
|:---|:---|:---|:---:|
| **普通 AZ** | ADI ISSCC 2024（PPT 33–35） | RA 每笔刷新/归零，原始采样率，N→N | **保留** |
| **CAAZ** | PKU VLSI 2025 C32-1 | 输入斩波 + 两笔共享 AZ + 数字配对，输出 fs/2 | **隔离为扩展** |

> main `chopping_az.py:35-37` 自己写了：`conventional_az` 每笔刷新并作**普通两点平均**；`chopping_assisted` 偶刷新/奇保持。前者也不是 ADI 的"每笔独立 AZ"（它降为 fs/2 的两点平均），所以 W1 必须**新建全速率普通 AZ 模型**，而不是保留 `conventional_az` 原名。

---

## 2. main 侧 CAAZ 清单

| # | 文件/符号 | 行/位置 | 处理 |
|:--|:---|:---|:---|
| C-01 | `src/adi_model/__init__.py` 的 `from .chopping_az import ChoppingAZParameters, ChoppingAZResult, pair_readout, run_caaz_pipeline` | 25–29 | 删除导出；普通 AZ 新符号单独导出 |
| C-02 | `src/adi_model/__init__.py` `__all__` 的 `ChoppingAZParameters/ChoppingAZResult/pair_readout/run_caaz_pipeline` | 224–227 | 删除 |
| C-03 | `src/adi_model/chopping_az.py` 的 `chopping_assisted`、`pair_readout`、`run_caaz_pipeline`、`_fresh_pair_ids`、`_SampleIndexedChop` | 32/94/148/168/232 | 移 `extensions/caaz/model/` |
| C-04 | `src/adi_model/chopping_az.py` 的 `conventional_az` 分支 | 35–37 | **不保留原名**；改写成 `behavioral/ra_az.py` 的全速率 AZ |
| C-05 | `src/adi_model/ra.py` 顶部说明 "CAAZ 的输入等效 AZ 状态由此进入…" | 5 | 改写为普通 AZ 说明；CAAZ 引用移扩展 |
| C-06 | `rtl/rtl_sources.f` 的 `rtl/control/caaz_cycle_ctrl.sv`、`rtl/calibration/caaz_pair_processor.sv` | 2–3 | 从主 filelist 删除；扩展单独 filelist |
| C-07 | `rtl/core/sar_structural_ctrl.sv` `P_CAAZ_ENABLE`、`caaz_az_capture_ok`、`caaz_input_chop_neg`、`caaz_az_capture_en`、`caaz_az_hold`、`u_caaz_cycle` | 8/12/29–30/107–113 | 删除参数/端口/实例；保留通用 sample-ID 队列/epoch/backpressure |
| C-08 | `rtl/core/sar_structural_ctrl.sv` `context_chop_neg/context_az_pair_id/context_az_valid` | 50–52/184–191 | 从 `fine_control_word` 打包中移除 |
| C-09 | `rtl/top/sar20_digital_core.sv` `caaz_az_capture_ok`、`caaz_input_chop_neg`、`caaz_az_capture_en`、`caaz_az_hold`、`cal_obs_caaz_data`、`caaz_raw_valid/data`、`caaz_pair_difference/valid`、`caaz_pair_first_sample_id/id`、`caaz_pair_fault/code` | 60–69 | 删除端口；主 top 只输出逐笔结果 |
| C-10 | `rtl/top/sar20_digital_core.sv` `context_chop_neg/context_az_pair_id/context_az_valid` 及打包 | 155–156/171–172/183–184/194–195 | 删除 |
| C-11 | `tests/unit/test_chopping_az.py` | 全文件 | 移 `extensions/caaz/tests/`，主 pytest 不收 |
| C-12 | `docs/caaz/`、`docs/randomization_caaz/`、`docs/history/legacy_vectors_af8/` | 目录 | 移 `extensions/caaz/docs/`，保留原 SHA/链接 |
| C-13 | `sim/tb/caaz_pair_processor_tb.sv`、`caaz_pair_vectors_tb.sv` | 文件 | 移 `extensions/caaz/tb/` |
| C-14 | `ad4080_dsp/rtl/sar20_system_top.sv`、`ad4080_dsp/docs/SAR20_INTERFACE.md` | CAAZ 端口/说明 | 标为外部消费者，不进 ADI 指标 |
| C-15 | `mechanism_inventory.json`、`sources_manifest.json`、`docs/digital_status_matrix.md` 的 CAAZ 条目 | 条目 | 标 `OUT_OF_SCOPE/EXTENSION`，不计 ADI |

---

## 3. task 侧 CAAZ 清单

| # | 文件/符号 | 处理 |
|:--|:---|:---|
| T-01 | `task3/model/caaz20.py::CaazOracle/Input/Output/_Issued` | 移 `extensions/caaz/task3/` |
| T-02 | `task3/rtl/caaz20_pair.sv`、`task3/tb/caaz20_pair_tb.sv`、`caaz20_properties.sv` | 移扩展；主 filelist 不收 |
| T-03 | `task3/tests/test_task3_smoke.py`、`task3/tools/*`、`task3/evidence/*` | 移扩展；保留原始 SHA |
| T-04 | `task_integration/rtl/sar20_digital_lms_top.sv` 的 `caaz_enable_i`、`acq_chop_neg_o`、`acq_az_capture_o`、`acq_az_hold_o`、`acq_az_pair_id_o`、`out_az_pair_id_o`、`caaz_fault_code_o`、`adc2_chop_neg_i`、`adc2_az_pair_id_i`、`adc2_az_ok_i`、`tag_q` 中 AZ 字段 | 生产 top 去端口/去字段；扩展保留变体 |
| T-05 | `task_integration/rtl/sar20_digital_top.sv` 同名字段 | 同上 |
| T-06 | `task_reconstruction/rtl/sar20_lms_recon.sv` 的 `recon_chop_neg_i`、`recon_az_pair_id_i`、`recon_az_ok_i` | 主 recon 去掉；扩展 wrapper 保留 |
| T-07 | `.github/workflows/task2.yml` 的 `task3/tools/run_caaz_*`、`task3/tb/formal.ys` | 主 CI 删除；扩展 CI 独立 |
| T-08 | `task_integration/requirements_trace.csv`/`gate_evidence.json` 的 CAAZ 条目 | 标 `EXTENSION`，不计 ADI 门禁 |
| T-09 | `README.md` "主线始终是DEM→dither→CAAZ→整体数字后端" | 改写：主线上限到 dither；CAAZ 为扩展 |
| T-10 | `task3/evidence/joint_noise` 等 | 保留为扩展证据；不混入 ADI 覆盖矩阵 |

---

## 4. 普通 AZ 的重建（不能省）

### 4.1 现状问题

- main `conventional_az`：每笔刷新 + **两点平均**，输出 N/2；这不是 ADI 普通 AZ。
- main `ra.py` 把 CAAZ 的输入等效 AZ 状态塞进 RA 输出限幅前；普通 AZ 没有独立状态。
- task 没有普通 AZ 模型（task3 是 CAAZ）。

### 4.2 目标模型

新增 `src/adi_model/behavioral/ra_az.py`：

```python
@dataclass(frozen=True)
class OrdinaryAZConfig:
    enabled: bool = False
    az_noise_v: float = 0.0        # AZ 采样噪声
    az_offset_v: float = 0.0       # 残余失调
    hold_droop_v_per_s: float = 0.0
    refresh: str = "per_sample"    # 每笔刷新；不降采样

def apply_ordinary_az(ra_input_v, cfg, sample_ids):
    """每笔独立 AZ：返回 (ra_input_az_v, az_error_v, flags)。
    不改变样本数；不做偶奇配对；不读全局 AZ 字。"""
```

验收：
- N 笔进 N 笔出，`fs` 不变；
- AZ 开/关的 RA 失调、低频噪声、保持误差可分别开关；
- 与 CAAZ 的 `pair_readout` 无共享状态；
- 主 `SimResult` 增加 `az_error_v`/`az_flags`，不改变旧字段。

---

## 5. 隔离验收

| # | 条件 | 命令/证据 |
|:--|:---|:---|
| 1 | 主包可安装/导入，无 CAAZ | `pip install .`；`python -c "import adi_model; assert not hasattr(adi_model,'ChoppingAZParameters')"` |
| 2 | 主 CLI/默认实验可跑 | `adi-simulate --samples 512 ...`；`pytest -q`（主 tests） |
| 3 | 主 RTL 可编译，filelist 无 CAAZ | `verilator --lint-only -f rtl/rtl_sources.f`；`rg -i caaz rtl/rtl_sources.f` 为空 |
| 4 | 主依赖扫描无 CAAZ | `rg -i "caaz|chopping_assisted|pair_readout" src/adi_model rtl tests` 为空（普通 AZ 名除外） |
| 5 | N→N，不降 fs/2 | 4096 笔进 4096 笔出；`out_valid` 计数=4096 |
| 6 | 扩展仍可独立跑 | `cd extensions/caaz && <原命令>`；原证据 SHA 可查 |
| 7 | 普通 AZ 保留 | AZ 开/关对照；每笔刷新；全速率 |
| 8 | 通用队列未误删 | sample-ID/epoch/backpressure/reset/cancel/error 路径测试仍在 |

---

## 6. 回退

- 每个文件移动/删除独立 commit；`git revert <sha>` 可恢复。
- 扩展目录保留原路径映射表（`docs/migration.md`），确保旧链接可跳转。
- 不做 `git checkout .`/`reset --hard`；不覆盖原证据。
