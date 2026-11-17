# 给 Codex 的强制指令：EDA 工作目录规范

**把下面整段复制给 Codex。这是强制要求，不是建议。**

---

```
你是 Codex，在本机或 vm-yian 上执行 EDA 任务。

【强制规则，无例外】

1. 任何 EDA 工具都必须在专用工作目录里启动。启动前必须先执行：
      export TASK="<项目>_$(date +%Y%m%d_%H%M%S)"
      export WORK="$HOME/eda_runs/$TASK"
      mkdir -p "$WORK" && cd "$WORK"
   然后才允许启动工具。

2. 绝对禁止以 $HOME 作为 CWD 启动任何 EDA 工具。
   "ssh vm-yian 'dc_shell -f x.tcl'" 这种写法是禁止的——因为 ssh 的 CWD 就是 $HOME。

3. 这些工具全部会往 CWD 写产物（实测）：
      Virtuoso AHDL   -> <name>.ahdlSimDB/
      Design Compiler -> *-verilog.pvl, *-verilog.syn, *.mr
      Formality       -> default-<日期>_<user>_<pid>.svf
      VCS             -> csrc/, simv*, *.daidir/
      Fusion Compiler -> ref/, *.svf, 日志
      Spectre         -> *.raw/, *.psf/, input.scs
      Voltus / PT     -> *_command.log, *.db, *.rpt
      Calibre         -> *.drc.results, *.lvs.report, svdb/
      Verdi           -> verdiLog/, novas.conf, *.fsdb

4. 工具本身有输出路径参数时，必须显式指定，不要依赖 CWD：
      set_svf <绝对路径>        （Formality / FC，且必须在 read_* 之前）
      vcs -Mdir <绝对路径> -o <绝对路径>
      create_lib <绝对路径>
      set_app_var sh_output_log_file <绝对路径>

5. 任务结束前执行自查，三条都必须输出 0：
      ssh vm-yian 'ls -d $HOME/*.ahdlSimDB 2>/dev/null | wc -l'
      ssh vm-yian 'ls -d $HOME/*.pvl $HOME/*.syn $HOME/*.mr 2>/dev/null | wc -l'
      ssh vm-yian 'ls -d $HOME/csrc $HOME/simv* 2>/dev/null | wc -l'
   不为 0 就必须清理，并在回复里报告清理了什么。

【清理规则（如果你需要清理 $HOME）】

清 A 之前必须做三步：

  第一步 列清单，不要用通配符直接 rm：
      ls -la $HOME/*.ahdlSimDB

  第二步 按时间戳区分归属：
      · 本次任务开始时间之后的 = 你的，可删
      · 之前的 = 别人的，不动
      实例：$HOME 曾有 74 个 *.svf，只有 44 个是本次产物，另 30 个是 9/10 月的。

  第三步 判断是否正在被使用【这一步最容易错】：

      ⚠️ 陷阱：lsof +D <目录> 返回 0 个句柄，不代表没有被使用！
      Virtuoso 的 AHDL 仿真不长期持有文件句柄，但会持续写文件。

      正确判据是看文件新鲜度：
          find <目录> -type f -newermt "-10 minutes" | wc -l
      若 > 0，说明正在被写，**绝对不能删**。

      实测案例：58 个 .ahdlSimDB 的 lsof 全部返回 0 句柄，
      但其中 19 个在最近 10 分钟内有文件被修改，27 个文件新鲜 —— 仿真在跑。

      辅助判据：
          pgrep -af 'virtuoso|dc_shell|vcs|fc_shell|spectre|voltus'
          ps -o pid=,lstart=,cmd= -p <pid>     # 看启动时间

【当前待处理项（你接手时的状态）】

  $HOME 有 58 个 .ahdlSimDB，5 类前缀：
      dc_*       27 个   （DC 工作点仿真）
      logic_*    11 个   （逻辑门表征）
      tran_*      9 个   （瞬态）
      acnoise_*   8 个   （噪声）
      mim_*       3 个   （MIM 电容表征）

  它们由 18 个 Virtuoso 进程产生（4 主 + 14 子，2026-11-15 23:08 起运行），
  这些进程的 CWD 是 $HOME。

  你不得擅自 kill 这些进程。要么等它们结束，要么先问仓主。

  kill 之前必须确认它属于本次任务：
      ps -o pid=,lstart=,cmd= -p <pid>

【自检脚本】

  仓库里有 tools/check_home_pollution.sh，六项检查：
      bash tools/check_home_pollution.sh vm-yian
  退出码 0 = 干净，1 = 有污染。
  它已内置「非本任务保留项」的过滤（verdiLog/、fc_command.log、*.svf 中 9/10 月的等），
  不要再去删这些。

【完整规范】

  仓库根目录 TOOL_WORKDIR_RULES.md 是完整规范，包含：
    · 各工具默认落盘产物对照表
    · 交互式 / 非交互式的正确写法
    · 白名单目录
    · 清理三步法
  开工前读一遍。

【回复要求】

  每次任务结束时，在回复里明确写出：
    1. 本次用的工作目录（绝对路径）
    2. 自查三条命令的实际输出（必须是 0 0 0）
    3. 若清理了任何东西，列出删了什么、为什么确定是你的
```

---

## 一句话版（如果空间有限）

```
执行 EDA 前必须 mkdir -p $HOME/eda_runs/<task>_<date> && cd 进去，
禁止以 $HOME 作 CWD（ssh 裸执行就是 $HOME）。
DC 写 .pvl/.syn/.mr、Virtuoso 写 .ahdlSimDB、VCS 写 csrc/、Formality 写 .svf。
清理前查文件新鲜度 find <dir> -newermt "-10 minutes"——lsof 返回 0 不代表没在用。
详见仓库根目录 TOOL_WORKDIR_RULES.md。
```
