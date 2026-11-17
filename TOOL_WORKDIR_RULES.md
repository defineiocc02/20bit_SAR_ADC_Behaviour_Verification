# 强制要求：EDA 工具工作目录规范

**本文件是强制要求，不是建议。任何 AI agent、任何脚本、任何人执行 EDA 工具前必须遵守。**

更新时间：2026-11-17
起因：`vm-yian:$HOME` 被灌入 55 个 `.ahdlSimDB` + 21 个 `d3d4*` + 44 个 `.svf` 等约 400 MB 垃圾

---

## 唯一硬规则

> **任何 EDA 工具都必须在专用工作目录里启动，绝不能以 `$HOME` 作为当前工作目录（CWD）。**

**为什么**：几乎所有 EDA 工具（DC / VCS / FC / Virtuoso / Spectre / Voltus / PT / Calibre）
都把中间产物写到 **当前工作目录**，而不是输出参数指定的位置。

---

## 一、各工具的默认落盘产物（实测）

| 工具 | 默认产物 | 落在哪 | 实例 |
|:---|:---|:---|:---|
| **Virtuoso AHDL** | `<name>.ahdlSimDB/` | **CWD** | `dc_ff_m4_20.ahdlSimDB`、`tran_ss125_w0p8.ahdlSimDB` |
| **Design Compiler** | `*-verilog.pvl`、`*-verilog.syn`、`*.mr` | **CWD** | `sched18-verilog.pvl` |
| **Formality** | `default-<日期>_<user>_<pid>.svf` | **CWD** | `default-20261115_songmingjun_485064.svf`（1.6 MB）|
| **VCS** | `csrc/`、`simv*`、`*.daidir/`、`ucli.key` | **CWD** | `d3d4*/csrc/` |
| **Fusion Compiler** | `ref/`、`*.svf`、日志 | **CWD** | `fc_*/ref/lib` |
| **Spectre / APS** | `*.raw/`、`*.psf/`、`input.scs`、日志 | **CWD** | — |
| **Voltus / PT** | `*_command.log`、`*.db`、`*.rpt` | **CWD** | `vcst_command.log`、`vst_rtdb.bak/` |
| **Calibre** | `*.drc.results`、`*.lvs.report`、`svdb/` | **CWD** | — |
| **Verdi / Verdi3** | `verdiLog/`、`novas.conf`、`*.fsdb` | **CWD** | `verdiLog/` |

---

## 二、必须这样做

### 2.1 交互式启动

```bash
# 正确：先进入专用目录
export TASK="sar16_review_$(date +%Y%m%d_%H%M%S)"
export WORK="$HOME/eda_runs/$TASK"
mkdir -p "$WORK" && cd "$WORK"
# 然后才启动工具
virtuoso -nocdsinit ...
dc_shell -f script.tcl
```

```bash
# 错误：直接在 $HOME 里启动
cd $HOME && virtuoso ...        # ← 会在 $HOME 里产生 <name>.ahdlSimDB
ssh vm-yian 'dc_shell -f x.tcl' # ← CWD 就是 $HOME
```

### 2.2 非交互式（ssh 远程执行）

**绝对禁止**这种写法：

```bash
ssh host 'dc_shell -f /path/x.tcl'          # ❌ CWD = $HOME
ssh host 'cd /path && ... '                 # ⚠️ 要确认 /path 是专用目录，不是 $HOME
```

**必须这样**：

```bash
ssh host 'W=/tmp/task_$$; mkdir -p $W && cd $W && dc_shell -f /path/x.tcl'
# 或
ssh host 'cd $HOME/eda_runs/sar16_$(date +%Y%m%d) && dc_shell -f /path/x.tcl'
```

### 2.3 工具内有输出路径选项时

优先显式指定，不要依赖 CWD：

| 工具 | 选项 |
|:---|:---|
| Virtuoso AHDL | `ahdlSimDB` 路径由 `simulatorLang` / `ahdlSimDBDir` 控制；或在启动脚本 `cd` |
| DC | `set_app_var sh_output_log_file`、`-output` 参数 |
| Formality | `set_svf <明确路径>`（必须在 `read_*` 之前） |
| VCS | `-Mdir <明确路径>`、`-o <明确路径>` |
| FC | `set_svf <明确路径>`、`create_lib <明确路径>` |

### 2.4 允许的工作目录白名单

| 位置 | 用途 | 保留期 |
|:---|:---|:---|
| `$HOME/eda_runs/<project>_<date>/` | 需要保留的 EDA run | 按项目定 |
| `/tmp/<task>_<pid>/` | 一次性验证、可随时丢弃 | 任务结束即删 |
| `$HOME/workspace/projects/<name>/` | 长期项目 | 按项目定 |

**`$HOME` 顶层不在白名单内。**

---

## 三、每次任务结束必须自查

```bash
# 三条都要为 0
ssh host 'ls -d $HOME/*.ahdlSimDB 2>/dev/null | wc -l'   # Virtuoso AHDL
ssh host 'ls -d $HOME/*.pvl $HOME/*.syn $HOME/*.mr 2>/dev/null | wc -l'  # DC
ssh host 'ls -d $HOME/*.svf $HOME/csrc $HOME/verdiLog $HOME/vcst_rtdb.bak 2>/dev/null | wc -l'
```

**若不为 0，先查清是谁产生的，再决定清理方式。**

---

## 四、清理前必须做的三件事

1. **列清单**：`ls -la` 全部列出，不要用通配符直接 `rm`
2. **按时间戳区分**：
   - 本次任务开始时间之后 = 我的
   - 之前 = 别人的，**不动**
   - 例：`*.svf` 有 74 个，只有 44 个是本次产物，另 30 个是 9/10 月的
3. **检查是否被运行中进程使用**：
   ```bash
   pgrep -af 'virtuoso|dc_shell|vcs|fc_shell|spectre' | head
   lsof +D <待删目录> 2>/dev/null | head
   ```
   **被占用的目录不能删**（会崩掉别人的仿真）

---

## 五、当前遗留问题（2026-11-17 状态）

### 已清理（约 400 MB）

| 类别 | 数量 | 处理 |
|:---|--:|:---|
| `d3d4*` VCS 实验目录 | 21 | ✅ 已删 |
| `*-verilog.pvl` / `.syn` | 29 | ✅ 已删 |
| `*.mr` | 15 | ✅ 已删 |
| `*.svf`（11-15 及之后） | 44 | ✅ 已删 |
| `/tmp/ip`、`/tmp/fc_*` 等 | — | ✅ 已删 |

### 待处理（**需人工确认**）

| 项 | 数量 | 阻塞原因 |
|:---|--:|:---|
| **`*.ahdlSimDB`** | **55** | **4 个 Virtuoso 进程仍在运行**，占用其中部分；删了会崩仿真 |

**这 4 个进程**：

```
/home/yian/workspace/projects/sar16_static_review_20261110/
  native_publish_clock_wait_20261115/logs/screens/
  native_publish_clock_wait_20261115/logs/screens_wait_resolved/
  native_publish_clock_wait_20261115/published_readback/logs/phase_focus/
  native_publish_clock_wait_20261115/published_readback/logs/phase_close/
```

**处理方式（二选一）**：
- **A**：停掉这 4 个 Virtuoso → 删 55 个 `.ahdlSimDB` → 用新规范重启
- **B**：保留，但今后启动 Virtuoso 必须在 `eda_runs/` 里 `cd` 后再启动

### 保留（确认非本次产物，不动）

`eda_runs/`（58 GB）、`workspace/`（25 GB）、`SAR16_Migration_20261108/`（2.8 GB）、
`rtl_cal/`、`archive/`、`_archive/`、`workspace_index/`、`adc_rtl_*/`、
`alib-52/`、`ant/`、`Project/`、`TASK000R_WC/`、`FM_INFO/`、`HDL_LIBRARIES/`、
`_cgsmoke/`、`cksum_dir/`、`incoming/`、`legalizer_debug_plots/`、`simulation/`、
`eda/`、`formality_svf/`、`sip/`、`verdiLog/`、`vcst_rtdb.bak/`

（`*.svf` 中 30 个更早的也保留：20260918 / 20261028 / 20261030 / 11-08）

---

## 六、给 AI agent 的具体指令

**如果你是被要求在这个仓库或 `vm-yian` 上工作的 AI：**

1. **执行任何 EDA 命令前**，先确认 CWD：
   ```bash
   ssh vm-yian 'pwd'    # 若是 /home/yian，禁止直接跑工具
   ```
2. **必须显式建目录并 cd**：
   ```bash
   ssh vm-yian 'W=/tmp/task_$$; mkdir -p $W && cd $W && <工具命令>'
   ```
3. **任务结束自查**（本文件 §3 的三条命令）。
4. **不要在 `$HOME` 顶层留任何文件或目录。**
5. **清理前按 §4 三步走**，特别是「检查是否被运行中进程使用」。
