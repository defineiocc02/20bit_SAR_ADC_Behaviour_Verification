# W8 状态（2026-11-12）

> 分支 `merge/adi-repro`；基线 `main bc9a16a`；**未合并 main**。P3/P4 完成后按用户指示
> 推送分支供审阅（见 `docs/reviews/20261113_p3p4/`）。
> 等级：L1 行为/合同、L2 数字 RTL 编译；无后仿/硅测。
> 旧 `docs/status.md` 保留为 main 基线状态，本页只记本次合并分支。

## 0.1 P3/P4（2026-11-13 新增）

| 项 | 状态 | 证据 |
|:---|:---:|:---|
| P3 RDAC follower 因果 | ✅ | `follower.py`；前缀截断不变性 28 672 检查 / 0 违约；偷看实现被抓 24 576 次 |
| P3 follower 参考代价 | ✅ | follower/直接加载 = 1.000（最终电荷逐位同）；传统试探 SAR = **2.99×** |
| P3 DEM 三维模型 | ✅ | `dem3d.py` 两套绑定；名义守恒 5.6e-21 LSB20；三处使用极差 0 |
| P3 dither 电荷口 | ✅（模型） | `dither_port.py`；非法早期码 6368→0；Fig.11 残余 6.70 mV = 1170 LSB20 |
| P3 dither 范围预算 | ✅ | 64 字下 SADC 下界 −2.9648 V（缺口 35.16 mV = 6144 LSB20） |
| P3 TRACK/AUX 记账 | ⚠️ 部分 | `track_aux.py` 因果审计 0 违约；输入电荷降 6.36×；AUX 支路未接 |
| P4 消融矩阵 | ⚠️ 部分 | 6 条实跑 + 2 条 NOT_RUN；`data/p4_ablation.{json,csv}` |
| P4 逐码校准 | ✅ | INL 25.4→11.0 LSB20（2.31×）、SFDR +10.5 dB，SNR/DR/NSD 不变 |
| P4 逐单元权值 | ❌ 不可辨识 | 残差域 96 V−96 V→0.1 V，实测残差 80 mV/14 V、权重 −43 V；留作负结果 |
| P4 热噪声对标 | ✅ | 模型 8.60 nV/√Hz vs 论文 9.3（0.92×） |
| P4 线性度对标 | ❌ 未达标 | INL 11.0 LSB20（校准后）vs 论文 2.3；剩余差距=片间组合误差+表项噪声 |
| 40 nm/电压口径 | ✅ | `06_paper_anchor_40nm.md`：3.3 V 器件 / 1.1 V 数字 / 6 Vpp / 20.5 pF / 1.8 mA 逐条出处 |

## 0. P0–P2（本轮新增）

| 项 | 状态 | 证据 |
|:---|:---:|:---|
| P0 反向依赖 | ✅ | `rg "from task|import task" src/adi_model` 仅 1 条注释；`task2/eight/model.py` → `behavioral/eight_slice.py`，import 全为 `adi_model.*` |
| P0 测试基线 | ✅ | `docs/verification/test_baseline.md`；1699 passed/3 failed/47 errors，全部归类 |
| P1 普通 AZ 主链 | ✅（API） | `DefaultModel.simulate(..., ordinary_az=...)`；32 码 N→N；零误差逐位一致；offset=1mV 全码改变 |
| P2 RTL 编译 | ✅ | VCS W-2024.09-SP1，0 error |
| P2 RTL 仿真 | ⚠️ 部分 | `source_control_contract_tb`：observations=56 / outputs=54 / **checks=368**；组件夹具，不是 20b 全链 |

## 1. 工作包

| 包 | 状态 | 实际内容 | 未完成 |
|:---|:---:|:---|:---|
| W0 调查 | ✅ | 分支/结构/依赖/源文/基线/迁移/合同/工作包 9 份文档 | — |
| W1 合同 + CAAZ 隔离 | ✅ | contracts/profiles、普通 AZ、CAAZ 全量移 `extensions/`、主 RTL 去 CAAZ | `cal_sample_context` 还留 3 个通用 AZ 端口接 0 |
| W2 核心下沉 | ✅ | `SwitchState/Q32/MapperTable` 入 `contracts/task_types.py`；`task2/eight/model.py` 迁为 `behavioral/eight_slice.py`，主包无 `task*` import | 八片模型只做冒烟，未接主链 |
| W3 静态机制迁移 | ✅（内核/控制） | 11 个内核 + 5 个控制模块 + dither 字迁入；119 项测试通过 | 八片负注入模型、整顶层对拍未迁 |
| W4 事件/物理统一 | ⚠️ 部分 | `EventRecord/EventLog/SampleContext` 合同与排序测试 | 未接 `ConversionEngine`；参考/RA/ADC2 未强制单积分器 |
| W5 无 CAAZ RTL 链 | ⚠️ 部分 | VCS 编译 **0 error**；`source_control_contract_tb` 真跑 **368 checks / observations=56 / outputs=54** | 组件夹具（N_UNIT_MAIN=2/ADC2=6），非 20b 全链 scoreboard |
| W6 ADI 机制补全 | ⚠️ 部分 | 噪声/参考/时偏/瞬态内核入 `mechanisms.py`，默认全关 | 未接主 `simulate`；参考动态/带宽/TRACK/AUX 未闭环 |
| W7 范围/鲁棒性 | ⚠️ 部分 | CAAZ 隔离门禁、profile 身份、主回归、扩展回归 | 未做全幅/种子/状态覆盖；INL/DNL/DR 未对标 |
| W8 文档/RC | ✅ | `docs/architecture.md`、`docs/migration.md`、本页、README 首屏 | 未推送/未合并 main |

## 2. 测试

| 范围 | 结果 |
|:---|:---|
| 主 pytest | **1699 passed / 3 failed / 47 errors** |
| 3 failed | Verilator 缺失、shallow clone 缺 `d1e1f87`、hatchling 未装 |
| 47 errors | `actual_capture` 需 Verilator |
| 内核测试 | **78 passed** |
| 控制测试 | **41 passed** |
| W1 合同/普通 AZ | **14 passed** |
| 合同类型/事件 | **5 passed** |
| CAAZ 扩展 | **32 passed** |
| CAAZ 隔离门禁 | **PASS** |
| 主 CLI/simulate | **PASS** |
| 主 RTL VCS | **0 error**（虚拟机 `vm-meow`，W-2024.09-SP1） |

## 3. 不能声称

- 不能声称 ADI 公开机制已完整复现：内核存在 ≠ 已接主链。
- 不能声称 20b INL/DNL/DR 达标：未跑 paper profile 的逐码/噪声对标。
- 不能声称 RTL 对拍通过：只有编译，没有逐笔 scoreboard。
- 不能声称普通 AZ 已进主链：`ra_az.py` 提供了模型，但 `DefaultModel.simulate` 未强制调用。
- 不能声称 CAAZ 已"删除"：它完整保留在 `extensions/caaz/`，主包零依赖。

## 4. 下一步（若要继续）

1. 把 `ra_az.py` 接入 `DefaultModel.simulate` 的 RA 前级，并对 AZ 开/关做 N→N 对照。
2. 把 `mechanisms.py` 的噪声/参考/时基接 `Config`，在 `adi_40m` profile 跑 INL/DNL/DR。
3. 用 VCS 跑 `source_control_contract_tb` 逐笔对拍，补 `cal_sample_context` 端口清理。
4. 迁 `task2/eight` 八片模型，拆 `task1.experiments` 反向依赖。
5. 补 5 个未入账专利的 source matrix 行，再谈"复现完成"。
