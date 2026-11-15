# scripts/ — 临时产物清理

仿真与综合会持续产出可丢弃的中间文件（Python 字节码、VCS 编译目录、DC 的 `work/`、
日志与报告）。本目录提供两个**默认只预览、不删除**的清理脚本，避免手敲 `rm -rf`
时路径写错。

| 脚本 | 作用范围 | 说明 |
| --- | --- | --- |
| `clean.py` | 本仓库工作区 | 纯标准库，无第三方依赖 |
| `clean_remote.sh` | 远端仿真机 `vm-meow` | 通过 `ssh` 执行，远端侧同样先预览 |

## 1. `clean.py` — 本地仓库

```bash
python3 scripts/clean.py            # dry-run：列出待删路径、文件数、总大小
python3 scripts/clean.py --apply    # 真正删除，并报告删除项数与释放空间
python3 scripts/clean.py --root DIR # 清理指定仓库根（默认脚本所在仓库）
```

输出示例（dry-run）：

```text
仓库根: /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification
模式  : dry-run（只列出，加 --apply 才删除）
保护  : .git/, docs/, src/, rtl/, tb/, tests/, synth/results/

待清理路径：
  [dir ]     252 B  experiments/__pycache__ (1 个文件)
  [file]       7 B  sim/x/blinky.vvp
  [dir ]       7 B  sim/x/work (1 个文件)

合计: 3 项, 3 个文件, 266 B

受保护目录（已跳过）: docs/, rtl/, src/, synth/results/, tb/, tests/

dry-run：未删除任何文件。核对无误后运行：python3 scripts/clean.py --apply
```

### 清理白名单（命中才会成为候选）

| 类型 | 模式 |
| --- | --- |
| 目录 | `__pycache__/`、`work/`、`csrc/`、`*.daidir/`、`simv*/` |
| 文件 | `*.pyc`、`*.pyo`、`*.vvp`、`simv*`、`*.log`、`*.rpt` |

目录命中后**整棵子树**一并删除（大小按子树累加），不再向下递归，因此
`work/` 里的内容也计入统计。

### 保护名单（永不删除）

- `.git/` —— 遍历阶段直接剪枝。
- `docs/`、`src/`、`rtl/`、`tb/`、`tests/` —— 交付物与文档，整体剪枝。
- `synth/results/` —— 精选的综合/STA 报告（`*.rpt`），整体剪枝。

保护路径在**遍历阶段剪枝**，不是"匹配完再排除"，所以连其中名为 `work/`、
`__pycache__/` 的目录也不会被触及。被跳过的目录会在输出末尾列出，便于核对。

### 安全边界

1. 只对**已匹配白名单的单个路径**调用 `shutil.rmtree` / `os.unlink`，全脚本没有
   `rm -rf`，也没有"按目录整体删"的分支。
2. 拒绝在 `/`、`$HOME` 上运行；仓库根缺少 `.git/` 时直接中止（退出码 2）。
3. 不跟随符号链接：符号链接只按文件删除链接本身，绝不进入其目标。
4. 单个路径删除失败只记入错误列表并以退出码 1 结束，不影响其余路径。

## 2. `clean_remote.sh` — 远端 `vm-meow`

```bash
bash scripts/clean_remote.sh            # dry-run
bash scripts/clean_remote.sh --apply    # 删除
REMOTE_HOST=other-host bash scripts/clean_remote.sh   # 指定主机
```

远端白名单（逐字 `case` 匹配，不匹配即拒绝）：

- `/root/vcs/sim_*` —— VCS 编译目录（`simv*`、`csrc/`、`*.daidir` 等）
- `/root/syn/work*` —— DC 的 `work/`、`work_p/`
- `/root/timing` —— 若存在

**绝不删除**：`/root/vcs/*.sv`（RTL/TB 源）、`/root/vcs/*.txt`（激励向量）、
`/root/syn/*.tcl`、`/root/syn/*.rpt`、`/root/syn/*.log`。这三类文件即使被前缀规则
意外扫到，也有独立护栏直接 `[skip]`；删除前还会对每个路径做第二次白名单校验。

远端脚本以 `ssh HOST "APPLY=… bash -s"` + quoted heredoc 传入，本地不展开远端路径，
因此不受本地 shell 通配影响。`ssh` 使用 `BatchMode=yes`，不会卡在密码交互。

## 3. 建议用法

- 提交前：`python3 scripts/clean.py` 看一眼，确认没有误伤，再 `--apply`。
- 远端跑完回归：`bash scripts/clean_remote.sh`，核对 10 个 `sim_*` 目录后 `--apply`。
- 本脚本只清"可再生成的中间物"，源码、文档、`synth/results/` 报告与远端
  `*.sv`/`*.txt` 均不在清理范围内。
