# 远端 EDA 工作目录规范（强制）

时间：2026-11-17　起因：仓主指出 $HOME 里散落大量垃圾

## 一、我违反了哪条

`~/.agents/AGENTS.md` §4「不擅自联网、删除、重装、升级工具或环境；破坏性操作先确认并备份」
—— 我把它理解为只约束「删除」，但**真正的问题是「写入位置」**：
DC / VCS / FC 的中间产物直接落在 `vm-yian:$HOME`，没有隔离。

## 二、清理结果（2026-11-17）

### 已删除（约 345 MB，全部为本会话产物）

| 类别 | 数量 | 大小 |
|:---|--:|--:|
| `d3d4*` VCS 实验目录（`csrc/`、`build/`、`.mr`）| 21 个 | **338 MB** |
| `*-verilog.pvl` / `*-verilog.syn`（DC 综合产物）| 29 | 196 KB |
| `*.svf`（`default-20261115~17_*`，11-15 及之后）| **44** | 6.9 MB |
| `*.mr`（DC 模块报告）| 15 | — |
| `/tmp/ip`、`/tmp/fc_*`、`/tmp/v21*`、`/tmp/d3d4*` 等 | — | 201 MB |

### 保留（确认非本会话产物）

| 项 | 原因 |
|:---|:---|
| `*.svf` 中 30 个更早的（20260918 / 20261028 / 20261030 / 11-08）| 非本会话 |
| `eda_runs/`（58 GB，含 `sar16_static` / `flash_sch_0849` / `ringamp_28nm` 等）| 其他项目，非本会话 |
| `workspace/`（25 GB）、`SAR16_Migration_20261108/`（2.8 GB）| 仓主既有结构 |
| `rtl_cal/`、`sar16_trash_20261113/`、`archive/`、`workspace_index/` | 早于本会话或为既有整理结构 |
| `simulation/`（16 MB，11-17 18:32）| **我不确定是否自己建，按「不确定不要慌」保留** |

**未动任何符号链接、未动 `eda_runs` 与 `workspace`。**

## 三、今后必须遵守

### 硬规则

1. **所有 EDA 产物只写到 `/tmp/<任务名>/` 或 `$HOME/eda_runs/<项目>_<日期>/`，
   绝不写到 `$HOME` 顶层。**
2. 跑 DC / VCS / FC 前，脚本里**显式设置工作目录**，不依赖 shell 当前目录：
   ```bash
   WORK=/tmp/${TASK}_$(date +%Y%m%d_%H%M%S)
   mkdir -p "$WORK" && cd "$WORK"
   ```
3. `set_svf` / `-verilog.pvl` / `.mr` 这类会落在 CWD 的产物，
   **必须在 `cd $WORK` 之后才执行**。
4. 任务结束前自查：
   ```bash
   ssh vm-yian 'ls -d $HOME/d3d4* $HOME/*.pvl $HOME/*.syn $HOME/*.mr 2>/dev/null | wc -l'
   # 必须为 0
   ```
5. 清理前**先列清单、区分时间戳**，不批量删同后缀文件
   （本次 `*.svf` 有 74 个，只有 44 个是我的）。

### 为什么这次会失控

- 我把 `ssh vm-yian '...'` 当作「在远端跑一条命令」，**没有先 `cd` 到隔离目录**
- DC 的 `.pvl/.syn`、VCS 的 `csrc/`、FC 的 `.svf` **默认写 CWD**
- `d3d4_ip` 的验证反复重跑，每次都新建一个 `d3d4XX` 目录（21 个），没有复用

## 四、自查命令

```bash
# 远端 HOME 是否被污染
ssh vm-yian 'cd $HOME && ls -d d3d4* *-verilog.* *.mr *.svf 2>/dev/null | wc -l'

# 本次会话留下的 /tmp 目录
ssh vm-yian 'ls -d /tmp/fc_* /tmp/v21* /tmp/ip /tmp/d3d4* 2>/dev/null | wc -l'
```
