# W1 实施状态（CAAZ 隔离 + 合同 + 普通 AZ）

> 分支：`merge/adi-repro`（从 `main bc9a16a` 起）；**未推送、未合并 main**。
> 本文件记录 W1 已完成/未完成项；W2–W8 仍待做。

---

## 1. 已完成

| # | 项 | 文件 | 验收 |
|:--|:---|:---|:---|
| 1 | 公共合同骨架 | `src/adi_model/contracts/specs.py`、`__init__.py` | 14 项单测通过；拒绝非正电容/重复 slice/非 0-1 mask/feature=3 |
| 2 | profile 注册表 | `src/adi_model/contracts/profiles.py`、`profiles/README.md` | `adi_isscc2024_40m_6vpp`、`engineering_10m_10vpp`、`patent_fig19_7slice`；`assert_caaz_free()` |
| 3 | 普通逐笔 AZ | `src/adi_model/behavioral/ra_az.py` | N→N、关闭逐位、噪声可复现、droop 需时间；6 项单测 |
| 4 | CAAZ Python 扩展 | `extensions/caaz/model/chopping_az.py` | 32 项原测试通过；主包 `hasattr(adi_model,'ChoppingAZParameters')=False` |
| 5 | CAAZ 测试/文档 | `extensions/caaz/tests/`、`README.md` | 扩展测试 32 passed；主 pytest 不再收集 |
| 6 | CAAZ RTL 扩展 | `extensions/caaz/rtl/{caaz_cycle_ctrl,caaz_pair_processor}.sv`、`tb/` | 主 `rtl/rtl_sources.f` 无 CAAZ；`rtl/` 无 CAAZ 文件 |
| 7 | 主结构控制器去 CAAZ | `rtl/core/sar_structural_ctrl.sv` | 删除 `P_CAAZ_ENABLE`、`caaz_*` 端口、`u_caaz_cycle`；VCS 编译通过 |
| 8 | 主数字顶层去 CAAZ | `rtl/top/sar20_digital_core.sv` | 删除 CAAZ 端口/配对处理器；`dout` 直接来自逐笔重构；VCS 编译通过 |
| 9 | 账本/工具路径 | `mechanism_inventory.json`、`tools/*.py`、`docs/readme/data/source_bound_fig19_evidence.json` | X02 标为 `required=false`/`MECHANISM_MODEL`，路径改 `extensions/caaz/...`；清单门禁 2 项通过 |
| 10 | 主 RTL VCS 编译 | VCS W-2024.09-SP1（虚拟机 `vm-meow`） | **0 error**；1 条 TFIPC 警告（`cal_sample_context` 少接 3 个输出） |

### VCS 编译命令（虚拟机）

```bash
export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1
export VCS_TARGET_ARCH=amd64
export PATH=$VCS_HOME/bin:$PATH
export LM_LICENSE_FILE=27080@localhost
vcs -full64 -sverilog -timescale=1ns/1ps \
    +incdir+. +incdir+rtl/params +incdir+rtl \
    -f rtl/rtl_sources.f rtl_params.vh \
    source_control_binding.sv source_acquisition_binding.sv \
    -top sar20_digital_core -o simv
```

结果：`Top Level Modules: sar20_digital_core`，`CPU time 1.607s compile + .425s elab`，`simv` 生成。
警告：`cal_sample_context u_context` 少接 3 个输出（`fine_chop_neg/fine_az_pair_id/fine_az_valid`）；不参与主链，属可清理项。

---

## 2. 测试结果

| 范围 | 结果 | 说明 |
|:---|:---|:---|
| 主 pytest | **1566 passed / 3 failed / 47 errors** | 3 failed = Verilator、shallow git `d1e1f87`、hatchling；47 error = `actual_capture` 需 Verilator |
| W1 新单测 | **14 passed** | 合同/profile + 普通 AZ |
| CAAZ 扩展 | **32 passed** | `extensions/caaz/tests/test_chopping_az.py` |
| 主 CLI/simulate | **PASS** | `--samples 32` → `SIMULATION_COMPLETE`，32 码 |
| 清单门禁 | **PASS** | `test_inventory_gate`、`test_repo_inventory_gate...` |
| 主 RTL VCS | **PASS（0 error）** | 见上 |

> 与 W0 基线相比：主 pytest 新增 2 个通过（1564→1566），因为 CAAZ 测试移出主树、W1 新增 14 项；3 个失败/47 个 error 仍是环境缺口，不是本次改动引入。

---

## 3. 未完成（W2–W8）

| 包 | 状态 | 说明 |
|:---|:---:|:---|
| W2 核心从实验下沉 | ❌ | `task2/eight/model.py:31-34` 的反向 import 仍在；`SwitchState/Q32/MapperTable` 尚未下沉 |
| W3 静态机制迁移 | ❌ | task1 调度/DEM、task2 dither/八片、task3 CAAZ（已隔离）未并入主包 |
| W4 事件/物理状态统一 | ❌ | 事件合同只有 dataclass 骨架；未接 `ConversionEngine` |
| W5 无 CAAZ RTL 联合链 | ⚠️ 部分 | 主 top 已去 CAAZ、VCS 编译通过；未跑 RTL 对拍 |
| W6 ADI 目标机制补全 | ❌ | 参考动态/带宽/TRACK/AUX 未接入主链 |
| W7 系统范围/鲁棒性 | ❌ | 未做全幅/种子/状态覆盖 |
| W8 文档/发布候选 | ❌ | 未清理重复入口/完整文档集 |

---

## 4. 已知遗留

1. `cal_sample_context` 仍保留 `chop_neg/az_pair_id/az_valid` 端口，主链全接 0；严格隔离可下一步删除端口并同步扩展。
2. `src/adi_model/ra.py` 顶部仍有一句 CAAZ 注释；普通 AZ 已独立，应改写。
3. `rtl/README.md` / `rtl_error_codes.vh` 仍有 CAAZ 词条；不影响编译，属文档清理。
4. `test_review_trace` 仍因 shallow clone 失败；补完整历史后应通过。
5. W1 未推送；分支只在本地。
