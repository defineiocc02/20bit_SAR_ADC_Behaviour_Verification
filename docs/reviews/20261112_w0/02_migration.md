# W0-B 模块迁移表

> 处置只有六类：**保留（keep） / 迁移（migrate） / 适配（adapt） / 重建（rebuild） / 隔离（isolate） / 归档（archive）**。
> 原则：main 是骨架；task 是算法/验证来源；同名模块先按符号 diff，禁止整文件覆盖。
> 验收：旧 profile 数值不变（整数逐位、浮点按预算）+ 新 profile 独立验收，二者分开。

---

## 1. main 侧处置

| # | main 模块/符号 | 处置 | 目标/动作 | 理由 | 验收 |
|:--|:---|:---:|:---|:---|:---|
| M-01 | `config.py::Config`、`defaults.py::DefaultParameters/DefaultModel/DefaultRun` | **保留** | 仍是唯一运行合同 | 已有 schema/覆盖/拒绝/来源分级 | `adi-simulate` 512 笔逐位不变 |
| M-02 | `pipeline_engine.py::execute_pipeline` | **保留+拆分** | 按状态所有权拆成 `sampling/coarse/rdac/ra/adc2/reconstruct` 阶段，接口不变 | 单函数过大，难定位 | 拆分前后 `SimResult` 字段逐位一致 |
| M-03 | `conversion.py::ConversionEngine` | **保留** | 作为唯一 RA/参考/ADC2 联立求解器 | 不能与 task `reference_mux` 串两次 RA | 与 task 独立节点解在容差内 |
| M-04 | `sim.py::SimResult` | **保留+扩展** | 增加 `event_record`/`sample_context`/`plant_trace` 字段 | 现有字段已覆盖物理量，缺事件合同 | 旧字段不改名；新字段可空 |
| M-05 | `slice_pool.py`、`dac_arch.py` | **保留** | 40MS/s 几何唯一实现 | task 的 17+5/63+8 不是同一布局 | 20.5pF/63+8 名义闭合 |
| M-06 | `sampler.py`、`sampling_charge.py` | **保留+适配** | 加 `AcquisitionCommand` 的 `sample_sub` 显式字段 | 采样/转换 mask 不能混 | 重叠特征可为 2 |
| M-07 | `sadc.py`、`adc2.py` | **保留** | 独立 SADC + 12b ADC2 | 与 task 的 9b/12b 一致 | 码本/窗口边界 |
| M-08 | `ra.py` | **保留+重建普通 AZ** | 拆出 `behavioral/ra_az.py`：每笔刷新、全速率、独立状态/误差；不降采样 | 现有 `conventional_az` 含两点平均，不能当 ADI 普通 AZ | N 笔进 N 笔出；AZ 开关对照 |
| M-09 | `reference_charge.py` | **保留+适配** | 接入 task `reference.py` 的 `settle_error/dynamic_reference`，保留逐事件电荷 | 现有有电荷、无动态建立 | 参考高低轨符号/总电荷 |
| M-10 | `scheduler.py` | **适配** | 保留 main 的样本生命周期；吸收 task 逐沿 `SliceScheduler` 的 `stall/clear/eligible` | main 按样本计划，task 按上升沿 | 同一 schedule 下 sample_id/mask 一致 |
| M-11 | `dem.py` | **适配** | 增加 `fig11`/`fig21` 具名 topology；`basic_segmented` 保留 | 不能把候选改名成"原芯片 3D DEM" | 三候选各自独立 topology_id |
| M-12 | `dither.py` | **适配** | 吸收 task `DitherWordGenerator` 的 6-bit/seed/sample_id；保留 Fig.19 program | main 有 program，缺字发生器/八片物理特征 | 64 字 replay + 拒绝率 |
| M-13 | `chopping_az.py` | **拆分** | `conventional_az`→`ra_az.py`；`chopping_assisted/pair_readout/run_caaz_pipeline`→`extensions/caaz/` | CAAZ 不在主工程；普通 AZ 必须留 | 主包无 CAAZ 导入；N→N |
| M-14 | `timing.py` | **适配** | 合并 task `sine_slope/aperture_jitter/jitter_limited_snr/dac_pole_attenuation`；保留相位/事件 | 两者同名不同义 | 抖动/带宽敏感度曲线 |
| M-15 | `low_frequency_noise.py` | **适配** | 合并 task `noise.py` 的 `thermal_sigma/white/flicker/FrontEndNoise` | main 噪声分散 | 噪声预算逐项可关 |
| M-16 | `pretracking.py`、`input_network.py` | **保留+适配** | 接真实物理状态；当前 `pipeline_engine.py:182-186` 明确拒绝无 binding 的 TRACK/AUX | 不能把预测值直接赋 held | 无未来信息反例 |
| M-17 | `calib.py`、`weight_calibration.py`、`reconstruction.py`、`fixed_point.py` | **保留** | 吸收 task NLMS/Q28/recon RTL 的数值合同 | main 有冻结/导出，task 有 NLMS/Q28 | 旧系数逐位；新 Q28 对拍 |
| M-18 | `metrics.py` | **适配** | 增加 LSB20 逐码 INL/DNL、零宽码、`sample_sub` 统计 | main 以 LSB1/ppm 为主 | 与 task 754k 码表一致 |
| M-19 | `provenance.py`、`inventory_gate.py`、`serialization.py` | **保留** | 增加 claim_id/evidence 绑定 | 已有来源分级/闸门 | 缺证据拒绝 |
| M-20 | `cli.py`、`__main__.py` | **保留** | 增加 `--profile`、`--topology-id`、`--claim`（薄封装） | 不能把公式塞进 CLI | 旧命令不变 |
| M-21 | `rtl/*`（20 模块） | **保留** | 生产 RTL 唯一实现 | 已综合/形式 | 旧向量逐位 |
| M-22 | `rtl/rtl_sources.f` | **适配** | 移除 `caaz_cycle_ctrl.sv`/`caaz_pair_processor.sv`；新增无 CAAZ 主顶层 | 主 filelist 不得含扩展 | 编译/扫描无 CAAZ |
| M-23 | `rtl/sar_structural_ctrl.sv` | **适配** | 删除 `P_CAAZ_ENABLE`、`caaz_*` 端口、`u_caaz_cycle`；保留通用 sample-ID 队列/epoch/反压 | 不能随 CAAZ 删通用队列 | 端口表/编译 |
| M-24 | `rtl/sar20_digital_core.sv` | **适配** | 拆出 `caaz_*` 端口（60-69）与配对输出；生产 top 直接退休逐笔结果 | 主输出不能等偶奇配对 | N→N |
| M-25 | `tests/unit/test_chopping_az.py` | **隔离** | 移到 `extensions/caaz/tests/` | 主 CI 不测扩展 | 主 pytest 无 CAAZ |
| M-26 | `docs/caaz/`、`docs/randomization_caaz/`、`sim/tb/caaz_pair_*` | **隔离/归档** | 移到 `extensions/caaz/docs|tb/`，保留 SHA | 历史证据不丢 | 链接可追溯 |
| M-27 | `ad4080_dsp/` | **隔离** | 作为外部消费者，不进 ADI 指标验收 | DSP 是扩展 | 主包不依赖 |

---

## 2. task 侧处置

| # | task 模块/符号 | 处置 | 目标 | 理由/注意 | 验收 |
|:--|:---|:---:|:---|:---|:---|
| T-01 | `task1/model/scheduler.py::SliceScheduler/SchedulerOutput/_advance_lfsr` | **迁移** | `src/adi_model/control/scheduler_edge.py` | 逐沿 stall/clear/eligible 是 task 的强项 | 与 task1 RTL 对拍 |
| T-02 | `task1/model/module1.py::Module1/DemSnapshot/Module1Output` | **迁移** | `control/module1.py` | 调度+DEM 同笔快照 | sample_id/mask/state |
| T-03 | `task1/model/fig11_dem.py::Fig11Masks/map_fig11` | **迁移** | `control/dem_fig11.py` | 2D 3b 水平/垂直候选 | 全码/相位 |
| T-04 | `task1/model/fig21_bridge.py::Fig21BridgeResult/map_fig21_bridge` | **迁移** | `control/dem_fig21.py` | 跨段桥接候选；保留 topology_id | 4.596mV 余量 |
| T-05 | `task1/model/codebook.py::map_record/codebook` | **适配** | `contracts/codebook.py` | main 已有 `NominalCodebook`；只补非均匀映射 | 旧 uniform512 不变 |
| T-06 | `task1/rtl/slice_scheduler.sv`、`fig11_dem.sv`、`fig21_bridge.sv`、`module1_top.sv`、`module1_bridge_top.sv` | **迁移** | `rtl/control/` | 生产 RTL 需与 main 风格/端口统一 | 旧向量逐位 |
| T-07 | `task2/model/dither_word_gen.py::DitherWordGenerator` | **迁移** | `control/dither_word.py` | 6-bit、seed、sample_id、stall 不改字 | 64 字 replay |
| T-08 | `task2/eight/model.py::Dither/decode/words/native_step_v/coarse_decision` | **迁移** | `behavioral/eight_slice.py` | 负注入合同/八片特征 | 独立电荷 |
| T-09 | `task2/eight/model.py::active_features/fit_features/weighted_sum` | **迁移** | `calibration/features.py` | 特征=0/1/2，重叠不能 OR | conv+sample=2 |
| T-10 | `task2/eight/model.py::ChargePool/ConversionState` | **迁移** | `behavioral/charge_pool.py` | 两节点电荷 | KCL 独立解 |
| T-11 | `task2/eight/model.py:31-34` 的 `SwitchState/Q32/MapperTable` | **重建/下沉** | `contracts/switch_state.py`、`calibration/q32.py`、`contracts/mapper.py` | 禁止 model→experiments；`MapperTable` 依赖私有函数 | import 扫描 |
| T-12 | `task2/rtl/dither_word_gen.sv`、`task2/eight_rtl/rtl/dither8_top.sv` | **迁移** | `rtl/control/dither_gen.sv`（diff）、`rtl/datapath/dither8_top.sv` | main 已有 `dither_gen.sv`，先比再合 | 逐位 |
| T-13 | `task3/**`（`caaz20.py`、`caaz20_pair.sv`、tb/tests/evidence） | **隔离** | `extensions/caaz/task3/` | CAAZ 不进主工程 | 主包无 CAAZ |
| T-14 | `task_integration/rtl/sar20_digital_lms_top.sv` | **适配** | `rtl/sar20_digital_lms_top.sv`（去 CAAZ 端口） | 保留 LMS 重构；删 `caaz_enable_i`、`acq_chop_neg_o`、`acq_az_*` | N→N，无配对 |
| T-15 | `task_integration/rtl/sar20_digital_top.sv` | **适配** | `rtl/sar20_digital_top.sv`（去 CAAZ） | 外部重构接口同样 | 端口表 |
| T-16 | `task_reconstruction/rtl/sar20_lms_recon.sv` | **迁移** | `rtl/datapath/recon_core.sv`（与 main `recon_core.sv` diff） | Q28 位宽/舍入 | 16384 笔 ≤1 LSB20 |
| T-17 | `src/adi_model/codes.py` | **迁移** | `contracts/codes.py` | 9b/20b 单一真源 | 旧字面量消失 |
| T-18 | `src/adi_model/topology/capacitors.py::ExperimentConfig` | **适配** | `profiles/engineering_10m_10vpp.py` | 10MHz/5V/1pF/0.1% 是 profile，不是全局 | profile_id 落盘 |
| T-19 | `src/adi_model/behavioral/noise.py` | **迁移/合并** | `behavioral/noise.py` | kT/C/白/1/f；与 main 噪声不重复 | 逐项开关 |
| T-20 | `src/adi_model/behavioral/reference.py` | **迁移/合并** | `behavioral/reference.py` | 建立/预充/RA 非线性；先修 `precharge_frac` | 参考事件 |
| T-21 | `src/adi_model/behavioral/reference_mux.py` | **迁移** | `behavioral/reference_mux.py` | 两轨切换/耦合 | 高低轨符号 |
| T-22 | `src/adi_model/control/timing.py` | **合并** | `behavioral/timing.py` | 抖动/极点；main `timing.py` 是相位 | 敏感度 |
| T-23 | `src/adi_model/behavioral/transients.py` | **合并** | `behavioral/transients.py` | 连续激励/瞬态 | 整段/分块一致 |
| T-24 | `src/adi_model/behavioral/impairments.py` | **迁移** | `verification/impairments.py` | 故障注入给验证层 | 反例触发 |
| T-25 | `src/adi_model/behavioral/charge_ref.py` | **保留 main 版** | 不迁移 | main `charge_ref` 更强 | 旧 oracle 不变 |
| T-26 | `src/adi_model/metrics/metrics.py` | **合并** | `src/adi_model/metrics/metrics.py` | 加 LSB20/零宽码 | 与 task 表一致 |
| T-27 | `src/adi_model/hashing.py` | **迁移** | `contracts/hashing.py` | 证据 SHA | 清单 |
| T-28 | `src/tests/*` | **迁移** | `tests/unit/` | 7 个内核测试 | 全绿 |
| T-29 | `task1/tests/*`（10）、`task2/tests/*`、`task3/tests` | **迁移/隔离** | `tests/` 与 `extensions/caaz/tests/` | task3 隔离 | 主 pytest 不含 CAAZ |
| T-30 | `trace/source_matrix.csv`、`req_matrix.csv`、`digital_status.json`、`milestones.json` | **迁移** | `docs/source_coverage/`（机器表） | 只 1 个专利；需补 5 个 | 表头/行数 |
| T-31 | `task*/evidence/`（约 900 MB） | **归档** | `artifacts/` 或仓库外 | 不进源码包 | SHA/路径 |
| T-32 | `delivery/`、`handoff/` | **归档** | `docs/history/` | 历史/计划 | 可追溯 |
| T-33 | `task2/physical/*` | **隔离/归档** | `verification/physical/` | GDS/DRC/LVS 是数字代理 | `PARTIAL_SIGNOFF` |

---

## 3. 迁移顺序（依赖）

```
T-01/02/03/04  →  T-05  →  T-06        （控制/调度/DEM）
T-07           →  T-08/09/10 → T-11    （dither/八片/特征，先拆反向依赖）
T-17/18/19/20/21/22/23/24/26/27        （共享内核，先 contracts）
T-12/14/15/16                           （RTL，先无 CAAZ 顶层）
T-13 + M-13/M-22/M-23/M-24/M-25        （CAAZ 隔离，最先做）
T-25/28/29                              （保留/测试）
T-30/31/32/33                           （台账/证据/归档）
```

---

## 4. 迁移验收（每项都要）

1. **旧 profile 数值保持**：`main` 的 512 笔 `SimResult` 字段逐位一致（整数）或浮点差 < 预算（如 1e-12 相对）。
2. **task 参考对拍**：迁移后的实现与 task 原实现同输入、同种子、同 mask，逐位/逐码一致。
3. **依赖方向**：`rg "task1.experiments|task2.experiments" src/adi_model` 为空；`src/adi_model` 不 import `task*`。
4. **CAAZ 隔离**：主包/主 RTL/主 CI 无 `caaz|chop`；扩展仍可独立运行。
5. **可安装**：`pip install .` 后 `adi-simulate --help` 可用；task 的 pytest 不因包结构失败。
6. **证据绑定**：每个迁移项有 commit、旧/新 SHA、对拍数据、失败反例。
