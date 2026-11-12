# W8 状态（2026-11-12）

> 分支 `merge/adi-repro`；基线 `main bc9a16a`；**未推送、未合并 main**。
> 等级：L1 行为/合同、L2 数字 RTL 编译；无后仿/硅测。
> 旧 `docs/status.md` 保留为 main 基线状态，本页只记本次合并分支。

## 1. 工作包

| 包 | 状态 | 实际内容 | 未完成 |
|:---|:---:|:---|:---|
| W0 调查 | ✅ | 分支/结构/依赖/源文/基线/迁移/合同/工作包 9 份文档 | — |
| W1 合同 + CAAZ 隔离 | ✅ | contracts/profiles、普通 AZ、CAAZ 全量移 `extensions/`、主 RTL 去 CAAZ | `cal_sample_context` 还留 3 个通用 AZ 端口接 0 |
| W2 核心下沉 | ⚠️ 部分 | `SwitchState/Q32/MapperTable` 类型入 `contracts/task_types.py` | `task2/eight/model.py` 反向 import 仍在 task 树（未迁入） |
| W3 静态机制迁移 | ✅（内核/控制） | 11 个内核 + 5 个控制模块 + dither 字迁入；119 项测试通过 | 八片负注入模型、整顶层对拍未迁 |
| W4 事件/物理统一 | ⚠️ 部分 | `EventRecord/EventLog/SampleContext` 合同与排序测试 | 未接 `ConversionEngine`；参考/RA/ADC2 未强制单积分器 |
| W5 无 CAAZ RTL 链 | ⚠️ 部分 | VCS 编译 `sar20_digital_core` **0 error**，`simv` 生成 | 未跑逐笔对拍；`cal_sample_context` 有 TFIPC 警告 |
| W6 ADI 机制补全 | ⚠️ 部分 | 噪声/参考/时偏/瞬态内核入 `mechanisms.py`，默认全关 | 未接主 `simulate`；参考动态/带宽/TRACK/AUX 未闭环 |
| W7 范围/鲁棒性 | ⚠️ 部分 | CAAZ 隔离门禁、profile 身份、主回归、扩展回归 | 未做全幅/种子/状态覆盖；INL/DNL/DR 未对标 |
| W8 文档/RC | ✅ | `docs/architecture.md`、`docs/migration.md`、本页、README 首屏 | 未推送/未合并 main |

## 2. 测试

| 范围 | 结果 |
|:---|:---|
| 主 pytest | **1693 passed / 3 failed / 47 errors** |
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
