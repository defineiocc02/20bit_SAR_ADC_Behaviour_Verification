# AGENTS.md

## 仓库级规则

1. **合同唯一入口**：`docs/CONTRACT_CURRENT.md`。任何与它冲突的规格文档均为历史件。
2. **来源分级**必须沿用：`[A]` 论文/PPT、`[P]` 专利实施例、`[E]` 工程补全、`[U]` 未确定。
   不得把 `[E]` 说成与 ADI 硅片一致。
3. **不得修改** `3+3+3` 映射、bridge 规则、Dither 符号、电容模型、ADC2 窗口，
   除非有独立原文依据并单独提交影响评估。
4. **提交前**必须跑通对应模块的验收入口；CI 与本地用同一命令。
5. 新增文档不得自称"唯一/权威/最新"，除非同时更新 `README.md` 的导航。

## 模块归属

| 模块 | 文件 | 验收入口 | 状态 |
|:---|:---|:---|:---|
| D3 DEM | `rtl/dem_mask_v3.sv` | `bash tools/verify_d3d4.sh` | 已交付 |
| D4 Split Dither | `rtl/dither_pre.sv` | 同上 | 已交付 |
| D1 / D2 / D5 / D6 | 其他文件 | 见各自说明 | 不在 D3-D4 范围 |

## 当前工作范围

本阶段只维护 **D3 / D4 两个数字 IP**。其他模块的修改需对应负责人进行。

## 复现

```bash
bash tools/verify_d3d4.sh
# 远端 VCS：D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh your-host" bash tools/verify_d3d4.sh
```

预期：

```
DEM    133376/133376 组  失配 0
Dither 512/512 组  失配 0
RESULT: PASS
```
