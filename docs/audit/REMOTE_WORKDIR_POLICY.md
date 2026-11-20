# EDA 工作目录规范

所有 EDA 工具必须从本次任务的专用目录启动。DC、VCS、FC、Formality、Virtuoso、Spectre、Voltus、PrimeTime、Calibre 和 Verdi 可能把中间产物写到当前目录，即使已经显式指定日志或网表路径。禁止以 `$HOME` 为当前目录启动这些工具。

## 启动前

- 一次性检查使用 `mktemp -d /tmp/sar-eda.XXXXXX` 创建目录。需要保留的流程使用 `$HOME/eda_runs/<项目>_<日期>/`，长期项目可用其专用运行目录。
- 启动前用 `pwd` 确认路径。远程命令也要在同一个 SSH 会话里先建立专用目录并成功 `cd`。
- 对支持显式路径的产物继续指定路径，例如 VCS 的 `-Mdir` 和 `-o`，Formality 在读入设计之前设置的 `set_svf`，以及 FC 的 `create_lib`。
- v21 回归的项目级工作目录用法见[仿真环境](../v21/I5_ENVIRONMENT.md)。

在本机或远端准备任务目录时，命令示例如下。把工具启动命令放在同一个已进入任务目录的 shell 中。

```bash
sar_eda_run="$(mktemp -d /tmp/sar-eda.XXXXXX)" && cd "$sar_eda_run" && pwd
ssh "$SAR_EDA_HOST" 'sar_eda_run="$(mktemp -d /tmp/sar-eda.XXXXXX)" && cd "$sar_eda_run" && pwd'
```

## 检查哪些产物

| 工具 | 可能写入工作目录的产物 |
| --- | --- |
| Virtuoso AHDL | `*.ahdlSimDB/` |
| Design Compiler | `*-verilog.pvl`、`*-verilog.syn`、`*.mr` |
| Formality | `default-*.svf` |
| VCS | `csrc/`、`simv*`、`*.daidir/`、`ucli.key` |
| Fusion Compiler | `ref/`、`*.svf`、日志 |
| Spectre / APS | `*.raw/`、`*.psf/`、运行网表和日志 |
| Voltus / PrimeTime | `*_command.log`、`*.db`、`*.rpt` |
| Calibre | `*.drc.results`、`*.lvs.report`、`svdb/` |
| Verdi | `verdiLog/`、`novas.conf`、`*.fsdb` |

任务结束时清点自己的运行目录，并用 `bash tools/check_home_pollution.sh "$SAR_EDA_HOST"` 检查远端家目录。还需核对是否新增 `*.svf`，该脚本的检查列表不包含历史指导文件。

## 清理操作

1. 列出候选路径，保留清单。
2. 将任务启动时间、运行日志和产物路径一并核对，确认归属。对归属不明的文件保留。
3. 使用 `pgrep -af` 或 `lsof +D <目录>` 核查进程占用，保留使用中的目录，再按确认的清单处理。

历史清理记录可以用 `git show 2224d30:TOOL_WORKDIR_RULES.md` 和 `git show 2224d30:docs/audit/REMOTE_WORKDIR_POLICY.md` 查阅。
