# W0-D 分阶段工作包、依赖、验收与回退

> 负责人用角色表示：**集成（L）**、**行为（B）**、**数字/RTL（D）**、**验证/文档（V）**。
> 每个工作包是一个可独立审阅的 commit 集；不允许"一边改主引擎一边改合同"。
> 状态：未开始 / 进行中 / 待验证 / 通过 / 阻塞。

---

## 0. 依赖图

```
W0 基线/源文（L+V）
 ├─ W1 合同 + CAAZ 隔离（L）──────────────┐
 │   ├─ W2 核心从实验下沉（D）             │
 │   │   ├─ W3 静态机制迁移（D+B）          │
 │   │   └─ W5 无 CAAZ RTL 联合链（D+V）    │
 │   └─ W4 事件/物理状态统一（B+D）         │
 ├─ W6 ADI 目标机制补全（B+D+V）           │
 └─ W7 系统范围/鲁棒性（V）                │
     └─ W8 文档/发布候选（L+V）
```

**硬依赖**
- W2 必须等 W1 的 `contracts/` 与 CAAZ 隔离先落地；否则下沉对象仍在实验目录里。
- W3 必须等 W2 的 `SwitchState/MapperTable/Q32` 下沉；否则继续反向依赖。
- W4 必须等 W3 的实际电容/参考/RA 接口；否则会串两次 RA。
- W5 必须等 W1 的无 CAAZ 顶层和 W4 的事件合同；否则 RTL 驱动不了同一 plant。
- W6 必须等 W4/W5；否则新机制没有可复现的联合链。
- W7 必须等 W6；否则没有可对照的机制全集。

---

## 1. W0 基线与源文调查（本次，L+V）

| 项 | 内容 |
|:---|:---|
| 目标 | 结构/依赖/源文/基线/迁移/合同/工作包 |
| 允许改 | `docs/reviews/20261112_w0/**`（新目录） |
| 禁止改 | `src/`、`rtl/`、`tests/`、`task*/`、`references/`、分支 |
| 交付 | 01–08 文档 + 真实基线命令/结果 |
| 验收 | 每条结论有文件/符号；未跑项标 `NOT_RUN`；用户确认后才进 W1 |
| 回退 | 删除新目录；不影响任何生产文件 |

---

## 2. W1 公共合同 + CAAZ 隔离（L）

| 项 | 内容 |
|:---|:---|
| 目标 | 建立 `contracts/` 骨架；主包/主 RTL/主 CI 脱离 CAAZ；保留普通 AZ 与通用 sample-ID 队列 |
| 前置 | W0 通过 |
| 允许改 | 新 `src/adi_model/contracts/**`、`profiles/**`、`extensions/caaz/**`、`rtl/rtl_sources.f`、`rtl/core/sar_structural_ctrl.sv`、`rtl/top/sar20_digital_core.sv`、`src/adi_model/__init__.py`、`tests/` |
| 禁止改 | 不改 `conversion.py` 的物理公式；不改 task 原始证据；不删历史 SHA |
| 接口 | `TopologySpec/TimingSpec/EventRecord/SampleContext/SwitchCommand/FrozenCalibration` 首版 |
| 步骤 | 1) 建 contracts 骨架；2) 拆 `chopping_az.py` 为 `ra_az.py` + `extensions/caaz/`；3) 删 `__init__` CAAZ 导出；4) RTL filelist/结构控制/数字顶层去 CAAZ；5) 主 CI 去 CAAZ；6) 扩展独立 filelist/top |
| 验收 | 主包 `pip install .` + `adi-simulate --help`；主 pytest 无 CAAZ；主 RTL 编译（需 Verilator）；**N 笔进 N 笔出**，不降 fs/2；普通 AZ 每笔刷新且全速率 |
| 文档 | `docs/architecture.md`、`docs/caaz_isolation.md`、`extensions/caaz/README.md` |
| 回退 | 每个 commit 独立；`git revert <sha>`；不动 `git checkout .` |

**普通 AZ 的特别验收**
- `conventional_az` 的两点平均/降采样**不得**作为 ADI 普通 AZ；新 `ra_az.py` 必须每笔刷新、全速率、独立状态/误差。
- 用同一输入、同一失配，比较 AZ 开/关的 RA 失调与低频噪声；报告 N→N、无配对延迟。

---

## 3. W2 核心从实验下沉（D）

| 项 | 内容 |
|:---|:---|
| 目标 | 把 `SwitchState`、`MapperTable`、`Q32`、格式常量、`capture` 从 `task1/experiments` 下沉到 `contracts/`/`calibration/`；实验只调用公共 API |
| 前置 | W1 |
| 允许改 | `src/adi_model/contracts/**`、`calibration/**`、`task1/experiments/**`（只改 import）、`tests/` |
| 禁止改 | 不改实验数值；不改 RTL |
| 验收 | `rg "from task1.experiments" src/adi_model` 为空；`rg "from task" src/adi_model` 为空；task1 实验原数值逐位；`MapperTable` 纯数据/映射可独立测试 |
| 回退 | 保留旧 import shim 一个版本；`git revert` 后旧路径仍可导入 |

---

## 4. W3 静态机制迁移（D+B）

| 项 | 内容 |
|:---|:---|
| 目标 | 迁 DEM（Fig11/Fig21）、dither（6-bit 字/八片物理）、实际电容/独立电荷、校准/定点 |
| 前置 | W2 |
| 允许改 | `control/**`、`behavioral/**`、`calibration/**`、`rtl/control/**`、`rtl/datapath/**`、`tests/` |
| 禁止改 | 不改 main 的 63+8 几何；不把 Fig11/21 改名成"原芯片 3D DEM" |
| 验收 | 旧 task profile 全码/相位逐位；main 旧 profile 有限范围可回放；`conv+sample` 特征=2；dither 64 字 replay；DEM 三候选各自 `topology_id` |
| 回退 | 每个机制单独 commit；`topology_id` 切回旧候选即回退 |

---

## 5. W4 事件与物理状态统一（B+D）

| 项 | 内容 |
|:---|:---|
| 目标 | 统一时钟事件→sample transaction；接逐位 follower 与真实参考电荷；**一个积分器**推进参考/RA/ADC2；保留普通 AZ/动态带宽 |
| 前置 | W3 |
| 允许改 | `simulation/**`、`behavioral/**`、`control/**`、`sim.py`、`pipeline_engine.py` |
| 禁止改 | 不串两次 RA；不把最终码事后反推成历史事件 |
| 验收 | 静态极限、零负载、充分建立、整段/分块一致；参考高低轨符号；逐事件负载/总电荷；普通 AZ 与带宽切换单独对照 |
| 回退 | 保留旧 `execute_pipeline` 入口；新事件路径 feature flag；失败即切回 |

---

## 6. W5 无 CAAZ 的 RTL 联合链（D+V）

| 项 | 内容 |
|:---|:---|
| 目标 | 冻结新版参数/观察/绑定；RTL 驱动同一行为 plant；逐事件/逐笔对拍 |
| 前置 | W1 + W4 |
| 允许改 | `rtl/**`（无 CAAZ）、`verification/cosim/**`、`tests/` |
| 禁止改 | 不把预计算最终粗码喂给 RTL 冒充闭环；不套用 SKY130 收据到新 main |
| 验收 | 接受/退休、每次 mask 更新、ADC2 owner、系数代、原码、整数结果、bad/overflow/sticky 全比较；配置代/stall/reset/非法码/错误 ID/排空/park；先小向量再定向扩大 |
| 回退 | RTL 冻结 SHA；失败回退到 W4 行为路径，不删 RTL |

---

## 7. W6 补全 ADI 目标机制（B+D+V）

| 项 | 内容 |
|:---|:---|
| 目标 | 论文主 profile；三维 DEM/边界、lower/upper 余量、TRACK、AUX、参考代表实施例与联合接入 |
| 前置 | W4 + W5 |
| 允许改 | `profiles/adi_isscc2024_40m_6vpp/**`、`control/**`、`behavioral/**`、`rtl/**`、`docs/source_coverage/**` |
| 禁止改 | 不把专利可选实施例自动升级为原芯片披露；不伪造未公开矩阵 |
| 验收 | 每机制有有效对照 + 失败反例；`claim_id`→证据；未公开项标 `ASSUMED` |
| 回退 | 机制级 feature flag；`topology_id` 保持旧候选 |

---

## 8. W7 系统范围与鲁棒性（V）

| 项 | 内容 |
|:---|:---|
| 目标 | 端点因果策略、满幅/幅频/制造种子/噪声/漂移；冻结条件比较性能 |
| 前置 | W6 |
| 允许改 | `experiments/**`、`verification/**`、`docs/reports/**` |
| 禁止改 | 不静默 clip/丢失败样本；不改 golden 掩盖变化 |
| 验收 | 全幅/幅频/种子/状态覆盖；拒绝/饱和/代价；普通 AZ/带宽/TRACK/AUX/参考各有开关对照；训练/留出分离 |
| 回退 | 实验配置/种子固定；结果目录独立；不覆盖旧结果 |

---

## 9. W8 文档与发布候选（L+V）

| 项 | 内容 |
|:---|:---|
| 目标 | 清理重复入口；模块说明/注释/单样本调试/图/状态表；交付真实证据与未完成项 |
| 前置 | W7 |
| 允许改 | `docs/**`、`README*`、模块 docstring（不改逻辑） |
| 禁止改 | 不把"未实现"改成"完成"；不删失败记录 |
| 验收 | `README` 首屏回答目标/完成/未通过/三条命令/一笔样本；`docs/` 九件套齐全；每条命令实际可跑 |
| 回退 | 文档 commit 独立；`git revert` |

---

## 10. 每张任务卡的模板

```yaml
id: W?-??
goal: 一句话
owner: L/B/D/V
depends_on: [...]
allowed_files: [...]
forbidden_files: [...]
interfaces: [TopologySpec, EventRecord, ...]
sources: [PAPER-xx, PAT-xx]
assumptions: [...]
steps: [...]
acceptance:
  - 数值/逐位/反例/工具
docs: [...]
rollback: commit/revert/feature flag
status: 未开始
```

---

## 11. 风险登记（与工作包绑定）

| 风险 | 触发 | 后果 | 检出 | 缓解 | 阶段 |
|:---|:---|:---|:---|:---|:---|
| 快进删 main | 直接 `git merge 20b10mtask` | 丢 1851 文件 | `git diff --stat` | `-s ours` + 选择性 checkout | W1 |
| CAAZ 漏拆 | 只移 task3 | 主包/主 RTL 仍依赖 | `rg caaz` | 按 07 清单逐项拆 | W1 |
| 普通 AZ 被误删 | 把 `chopping_az` 整模块移走 | ADI AZ 丢失 | 主链 AZ 开/关对照 | 先建 `ra_az.py` 再移 CAAZ | W1 |
| 反向依赖 | 整目录搬 eight | 主模型依赖实验 | import 扫描 | W2 先下沉 | W2 |
| 串两次 RA | 参考/RA/ADC2 各自求解 | 建立/噪声翻倍 | 节点 KCL/整段一致 | 一个积分器 | W4 |
| 用最终码反推 | 只算最终码 | 冒充因果 follower | 早期事件反例 | 逐位 follower trace | W4 |
| 参数混用 | 10MS/s 数字套 40MS/s | 结果无效 | profile_id/topology_id | 强制落盘/拒绝 | W1/W6 |
| 证据缺 SHA | 归档被覆盖 | 不可追溯 | manifest 校验 | 只增不改 + SHA | W8 |
| 大二进制 | GDS/zip 入库 | 仓库膨胀 | 体积扫描 | LFS/artifacts | W8 |
