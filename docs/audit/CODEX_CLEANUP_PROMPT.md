# 给 Codex 的清理指令：vm-yian `$HOME` 污染

**把下面整段复制给 Codex。**

---

```
你在 vm-yian 的 $HOME（/home/yian）留下了大量 EDA 中间产物，
污染了根目录。请按下面步骤清理。

【第一步：先认清什么算「污染」】

这些工具默认把产物写到当前工作目录（CWD），而 ssh 远程执行的 CWD 就是 $HOME：

  Virtuoso AHDL    -> <name>.ahdlSimDB/            （实测 67 个）
  Design Compiler  -> *-verilog.pvl, *.syn, *.mr
  Formality        -> default-<日期>_<user>_<pid>.svf
  VCS              -> csrc/, simv*, *.daidir/
  Voltus / PT      -> *_command.log, *.db, vst_rtdb.bak/
  Verdi            -> verdiLog/, novas.conf

它们必须出现在 $HOME 之外。

【第二步：跑自查，看还有什么】

  cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
  bash tools/check_home_pollution.sh vm-yian

六项检查，全部为 0 才算干净。它会自动过滤「非本次任务」的保留项
（verdiLog/、fc_command.log、*.svf 中 9/10 月的等），不要删那些。

【第三步：清理前必须判断两件事】

（A）这批东西是不是你的？
    按时间戳划界：你的任务开始时间之后 = 你的，之前 = 别人的。
    实例：$HOME 曾有 74 个 *.svf，只有 44 个是本次产物，另 30 个是 9/10 月的。
    绝不能按通配符一把删。

（B）还在被使用吗？
    ⚠️ 坑一：ps 的 etime 不是运行时长
        ps -o pid=,etime=,time=,cmd= -p <pid>
                    ↑存活多久  ↑真正算的CPU时间
        实况：4 个 virtusoo 进程 etime = 1天20小时，但 time 只有 28 秒，
              3 秒内 CPU tick 变化 = 0。它们在挂着，不是在算。

    ⚠️ 坑二：lsof 的两种误读
        · lsof 返回 0 不能证明「没在用」（Virtuoso 会周期性开合句柄）
        · 但 lsof 返回 >0 是「确认占用」的硬证据 —— 此时绝对不删
        实况：/tmp/sar16-user-desktop.CUDUjn 的 lsof = 17 个句柄（活进程持有）
              67 个 *.ahdlSimDB 的 lsof = 0（无进程持有）

    推荐判据（按可靠性排序）：
      1. lsof +D <目录> | wc -l        >0 则绝对不能删
      2. find <目录> -type f -newermt "-5 minutes" | wc -l
         >0 说明在写，不能删
      3. 目录总数是否在增长（等 30 秒再数一次）
         增长说明有东西在生成，先找源头

【第四步：分批删，不要一次全删】

  第一批：静默 > 30 分钟的
  第二批：静默 > 5 分钟的
  第三批：剩下的，确认 10 分钟无写入 + 等 30 秒不再新建

  每批删完都重跑一次自查，确认没有再生。

  实况记录：本次按此流程删了 67 个 .ahdlSimDB（约 31 MB），
            删完等 30 秒，确认 0 个再生。

【第五步：kill 进程的规矩】

  只有满足全部条件才可以 kill：
    · 父进程 = 1（orphan，启动它的 shell 已退出）
    · 累计 CPU time 很小（如 45 小时只用 28 秒）
    · 3 秒内 CPU tick 变化 = 0
    · 用途明确是 GUI/截图，不是仿真

  TERM 对 Virtuoso GUI 通常无效，需要 KILL -9。

  不符合上述条件的，先问仓主，不要动。

【第六步：根治——今后必须这样做】

  执行任何 EDA 命令前：

    export TASK="<项目>_$(date +%Y%m%d_%H%M%S)"
    export WORK="$HOME/eda_runs/$TASK"
    mkdir -p "$WORK" && cd "$WORK"
    # 然后才启动工具

  禁止：
    ssh vm-yian 'dc_shell -f x.tcl'           # CWD = $HOME
    ssh vm-yian 'virtuoso ...'                # 同上

  正确：
    ssh vm-yian 'W=$HOME/eda_runs/t_$(date +%s); mkdir -p $W && cd $W && <工具>'

  工具本身有输出路径参数时必须显式指定：
    set_svf <绝对路径>
    vcs -Mdir <绝对路径> -o <绝对路径>
    create_lib <绝对路径>

  注意：Virtuoso 的 ahdlSimDB 即使 CWD 在 /tmp，也可能因内部设置写回 $HOME。
  启动前先确认 $HOME 里没有新增 .ahdlSimDB。

【第七步：交付前自检（写进你的回复）】

  必须贴出这三条的实际输出，全部要为 0：

    ssh vm-yian 'ls -d $HOME/*.ahdlSimDB 2>/dev/null | wc -l'
    ssh vm-yian 'ls -d $HOME/*.pvl $HOME/*.syn $HOME/*.mr 2>/dev/null | wc -l'
    ssh vm-yian 'ls -d $HOME/csrc $HOME/simv* 2>/dev/null | wc -l'

  以及完整自查：
    bash tools/check_home_pollution.sh vm-yian    # 退出码必须 0

  若清理了东西，在回复里写明：
    · 删了什么、多少个、多大
    · 判断归属的依据（时间戳）
    · 判断「没在用」的依据（lsof / 新鲜度 / 是否增长）

【禁止事项】

  · 不要按 *.ahdlSimDB 一把 rm，先列清单
  · 不要删 lsof > 0 的目录
  · 不要 kill 父进程不是 1 的进程
  · 不要删 $HOME/eda_runs、workspace、SAR16_Migration_20261108 等既有结构
  · 不要删 *.svf 里 20260918 / 20261028 / 20261030 / 11-08 的（非本次任务）
  · 不要删 verdiLog/、fc_command.log、fc_output.txt、parasitics_command.log、
    pt_shell_command.log（11-08 / 09-16 的旧物）

【完整规范】

  仓库根目录 TOOL_WORKDIR_RULES.md（各工具落盘对照表、白名单、清理三步法）
  仓库 docs/audit/REMOTE_WORKDIR_POLICY.md（本次事故复盘）
```

---

## 一句话版

```
清理 vm-yian:$HOME 的 .ahdlSimDB / csrc / *.pvl / *.syn 等 EDA 残留。
判断在不在用：看 lsof 是否 >0、看 find -newermt 新鲜度、看 30 秒内是否还在新增——
不要看 ps 的 etime（那是存活时长，不是运行时长）。
kill 前必须确认父进程=1 且 CPU time 极小。分批删，删完跑
bash tools/check_home_pollution.sh vm-yian 确认六项全 0。
今后 EDA 一律先 mkdir -p $HOME/eda_runs/<task> && cd 再启动。
```
