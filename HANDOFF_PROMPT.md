# 交接提示词（复制给接手的 AI）

---

```
你现在接手一个 ISSCC 2024 数字 IP 复现项目。先按下面的顺序做，不要急着改代码。

【第一步：读交接文档】

  cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
  cat HANDOFF.md

它包含：仓库是什么、上一任做了什么、当前真实状态、他的错误清单、建议接手顺序。

【第二步：开工前必读（四个文件）】

  1. README.md              唯一入口，顶部有强制规范警告
  2. docs/STATUS.md         唯一权威状态表（含"不能声称什么"和"作废数字清单"）
  3. rtl/README.md          顶层关系、8 个实例化、3 个未接入
  4. TOOL_WORKDIR_RULES.md  远端 EDA 强制规范，违反会被仓主批评

【第三步：先查清仓库状态，不要动手】

  git status                  # 注意：工作区有 ~30 个未提交改动，不是上一任做的，且仍在变化
  git log --oneline -15
  git stash list

  ⚠️ 重要：上一任离开时有 ~30 个并行改动（Codex 或其他人做的，数量仍在变化），
     他没有提交也没有回滚。其中：
       flow/verification/manifests/mechanism_inventory.json  +36 / -679
       .github/workflows/ci.yml                              +79 / -36
       src/adi_model/defaults.py                             +9 / -18（重写了注释）
     他恢复了 17 个被误删但未提交的文件。

  【你必须先问仓主：这些并行改动是继续还是回滚。不要自己决定。】

【第四步：理解当前真实状态】

  能信：
    ✅ RTL-to-GDS 流程可复现（脚本在 flow/synth/v21/）
    ✅ 13 个模块全部 DC 综合，均满足 25 ns
    ✅ 7 个模块报过 Formality SUCCEEDED（比较点合计 2,189）

  不能信 / 有缺口：
    ⚠️ 那 7 个里只有 sched18 还有网表，其余 6 个的 .v/.gds 被上一任清理 /tmp 时删了
    ❌ 顶层 adc_digital_ip_v21 从未综合成功
    ❌ 3 个模块（dem_phase_rr / dem_phase_gen / follower_rt）未在顶层实例化
    ❌ recon_v21.sv:373 有未修缺陷（负数高位全 1 被误判溢出 + 读上一拍照旧值）
    ❌ 全无 foundry DRC / LVS / PG 布线 / 多角 / IR-EM
    ❌ 不是 tapeout signoff

【第五步：远端 EDA 的强制规矩（违反会被批评）】

  执行任何 EDA 命令前：
    export TASK="<项目>_$(date +%Y%m%d_%H%M%S)"
    export WORK="$HOME/eda_runs/$TASK"
    mkdir -p "$WORK" && cd "$WORK"
    # 然后才启动工具

  禁止：
    ssh vm-yian 'dc_shell -f x.tcl'    # CWD = $HOME
    ssh vm-yian 'virtuoso ...'         # 同上

  这些工具全部往 CWD 写产物：
    Virtuoso AHDL    -> <name>.ahdlSimDB/
    DC               -> *-verilog.pvl, *.syn, *.mr
    Formality        -> default-<日期>_<user>_<pid>.svf
    VCS              -> csrc/, simv*, *.daidir/
    Voltus / PT      -> *_command.log, *.db
    Verdi            -> verdiLog/

  任务结束自查（六项必须全 0）：
    bash tools/check_home_pollution.sh vm-yian     # 退出码必须 0

  清理规矩（三条，缺一不可）：
    1. 先列清单，不按通配符一把删
       （实例：74 个 .svf 里只有 44 个是本次的）
    2. 按时间戳划归属：本次任务之后 = 你的，之前 = 别人的
    3. 判断是否在用，三个判据两个陷阱：
       陷阱一：ps 的 etime 是存活时长，不是运行时长
               ps -o pid=,etime=,time= -p <pid>
               实例：4 个进程 etime=1天20小时，time 只有 28 秒 → 挂着不是算着
       陷阱二：lsof=0 不能证明没在用；lsof>0 是确认占用的硬证据
       判据三：等 30 秒看目录总数是否增长

  kill 前必须同时满足：父进程=1 + CPU time 极小 + 3 秒 tick 变化=0 + 用途是 GUI。
  TERM 对 Virtuoso 通常无效，需 kill -9。

【第六步：绝对不要做的事】

  ❌ 以 $HOME 作 CWD 跑 EDA（已污染过三次）
  ❌ 引用 docs/_history/ 任何文档（107 份已作废）
  ❌ 声称 tapeout signoff / foundry DRC clean / LVS clean
  ❌ 声称整核集成完成（顶层从未综合成功）
  ❌ 声称「D5 算法不可实现」（实测只支持「预算内未完成」）
  ❌ 引用「D5 占面积 95.8%」（已撤回：三层嵌套重复计数）
  ❌ 按通配符批量删文件
  ❌ 只报问题不给证据
  ❌ 改 ADC2 位宽（仓主已裁定 13 bit / 0.75 V，窗口 [-0.1875, +0.5625)）

【第七步：上一任的错误清单（避坑）】

  1 清理 /tmp 时删掉了 6 个模块的网表 —— 清理前必须确认已归档
  2 place_opt 失败归因连错四次 —— 改了变量数字没变就是归因错，立刻换判据
  3 把「我没跑完」写成「技术不可行」 —— 预算到期 ≠ 技术不可行
  4 文档乱了就新建文档去"说明"它 —— 应合并进已有入口
  5 说「四个顶层各自合理」 —— 实际是顶层漏接 3 个模块，该改接线而不是写解释
  6 未先 cd 就 ssh 跑 EDA
  7 未验证文件存在就改脚本路径 —— ls 五秒可发现，浪费了四轮

【第八步：建议的接手顺序】

  0 先问仓主：~30 个并行改动怎么处理
  1 重跑那 6 个模块，归档 .v/.def/.gds 进 docs/audit/artifacts/
  2 修 recon_v21.sv:373 的 err3_q（判据见 docs/audit/todo/README.md §2）
  3 顶层实例化 3 个缺失模块 + 顶层综合
  4 place_opt 门禁 + 降低利用率重试 4 个未完成模块
  5 PG 布线 / foundry DRC / LVS

【复现命令】

  D3D4_RUNNER=vm-yian bash tools/verify_v21.sh    # RTL 回归 9 项
  bash tools/check_home_pollution.sh vm-yian      # 远端污染自查
  python3 tools/check_doc_counts.py               # 文档计数核对

【最后】

  所有需要裁定的问题找仓主，不要自己决定。
  交活前给你的每条结论附可复现命令。
```

---

## 一句话版

```
接手 ISSCC 2024 数字 IP 复现项目。先 cat HANDOFF.md，再读
README.md / docs/STATUS.md / rtl/README.md / TOOL_WORKDIR_RULES.md。
先 git status（有 ~30 个并行改动，不是上一位做的，要问仓主）。
真实现状：流程可复现、13 模块综合完成、但只有 sched18 还有网表，
顶层从未综合成功，无 foundry DRC/LVS/PG。
远端 EDA 必须先 mkdir -p $HOME/eda_runs/<task> && cd 再启动，
禁止 CWD=$HOME。清理前查 lsof/新鲜度/是否增长，不要看 ps 的 etime。
不要声称 signoff。不确定的标"待确认"。
```
