# I5 检查点：Fusion Compiler 环境与 28HPC+ 流程定位

时间：2026-11-15　后端：TSMC 28HPC+　工具：Fusion Compiler W-2024.09-SP3

## 本轮完成

### 1. 修正环境结论（重要）

早前一版 `I5_ENVIRONMENT.md` 写"本机没有任何布局布线器"是**错的**。
错误原因：只用 `command -v icc2_shell` 查 PATH，而 Fusion Compiler 装在
`/opt/synopsys/fusioncompiler/W-2024.09-SP3/bin/`，不在默认 PATH。

**实测确认可用**：

| 工具 | 绝对路径 | 状态 |
|:---|:---|:---|
| **Fusion Compiler** | `/opt/synopsys/fusioncompiler/W-2024.09-SP3/bin/fc_shell` | 版本 OK、许可 OK、能进会话 |
| Formality | `/opt/synopsys/fm/W-2024.09-SP3/bin/formality` | 存在 |
| PrimeTime | `/opt/synopsys/prime/W-2024.09-SP3/bin/pt_shell` | 存在 |
| StarRC | `/opt/synopsys/starrc/W-2024.09-SP3/bin/` | 存在 |
| Calibre | `/opt/mentor/calibre2025/.../bin/calibre` | 存在 |
| ICC2（备选） | `/opt/synopsys/syn/W-2024.09-SP3/icc2/bin/icc2_shell` | 存在 |

### 2. Fusion Compiler 流程跑通四个阶段

`flow/synth/v21/fc_rtl_to_gds.tcl` 实测拿到：

```
STEP_CREATE_LIB_OK     NDM 参考库建立成功
STEP_TLU_OK            TLU+ 寄生技术文件注册成功
STEP_ELAB_OK           RTL elaborate 成功
STEP_TOP_OK            set_top_module 成功
compile_fusion         进入并完成初始映射（处理到 out_tag_reg 等寄存器）
```

### 3. 踩过的五个 FC 语法坑（全部写进脚本注释）

| # | 错误做法 | 正确做法 |
|:--:|:---|:---|
| 1 | `set_app_var target_library <ccs.db>` | FC 用 **NDM**：`$PDK/ndm/tcbn28hpcplusbwp7t40p140.ndm` |
| 2 | `open_lib` 试探再 `create_lib` | **直接 create_lib**；open_lib 失败会污染状态、catch 吞掉真错 |
| 3 | `current_design` + `link` | elaborate 后 **`set_top_module`** |
| 4 | `compile_ultra` | FC 是 **`compile_fusion`** |
| 5 | `set_parasitic_tech` | FC 无此命令（那是 ICC2） |

另外 `create_lib` 的目标目录**必须不存在**，且父目录要先 `file mkdir`。

### 4. 待绑定项（明确记录）

**TLU+ 关联到 corner 这一步未通**：`read_parasitic_tech` 成功，
但 `compile_fusion` 报

```
Error: No valid parasitics for LATE corner default (SCN-1001)
Error: No valid parasitics for EARLY corner default (SCN-1001)
Flow status set to Error by step: fc/logic_opto/mcmmStart (FLW-1762)
```

已排除的选项（都是实测）：
- `set_parasitic_tech` → 命令不存在
- `set_parasitic_parameters -early_corner/-late_corner` → 选项非法
- `set_parasitic_parameters -corners` → 存在，但取值格式待查

**下一步**：用 `create_corner` 建名为 `default` 的 corner，把最大/最小
TLU+ 挂上去；或查 `set_parasitic_parameters -corners` 的合法取值。

## 回归状态（未受影响）

接口收敛尝试失败后已完整回退，仓库在 `a867c6a` 状态，
所有回归保持：

| 回归 | 结果 |
|:---|:---|
| 审计三反例 | PASS (10) |
| I1+I2 完整联调 | PASS (105) |
| I3 完整 | PASS (20) |
| I4 重构 | PASS (8213, 8209 retired) |
| I4b 端到端 | PASS (24) |

## 本轮未完成（如实记录）

**生产接口收敛未完成**。目标是去掉 `sched_ext_sel` / `prepare_U` /
`prepare_flash` 等测试注入口，改为内部派生 + 验证包装层。实际做到：

- 硬宏端口改造完成、编译通过
- 包装层写好、编译通过
- 但**7 个 TB 的改造未完成**：Flash 从 `prepare_flash` 注入改为
  `ev_flash_*` 锁存后，事件层 `start` 需要等 Flash 就绪，
  时序链变化导致 TB 的 `post_wr` / `apply` 断言全部失效。

**已回退**，避免留下半成品。下一轮需要：
1. 先定下"事件层 start 等 Flash"的时序契约，写进接口文档
2. 再按契约改 TB（不是反过来）
3. 用包装层一次性切换，不要硬宏/TB 交替改

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
D3D4_RUNNER=vm-yian bash tools/verify_v21.sh
```
