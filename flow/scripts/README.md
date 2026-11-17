# 工作目录清理

[clean.py](clean.py) 在本机预览命中的临时文件。[clean_remote.sh](clean_remote.sh) 为旧远端目录布局预览 VCS 与综合工作目录。两个工具默认仅列出候选，传入 `--apply` 才执行删除。

## 本机预览

在仓库根目录显式指定扫描根。脚本内的缺省根会解析到 `flow/`，缺少 `.git` 检查会令其退出。

```bash
python3 flow/scripts/clean.py --root .
```

候选按路径名匹配，包括 `__pycache__/`、`csrc/`、`work/`、`simv*`、`*.daidir/`、`*.log` 和 `*.rpt`。匹配目录是整棵子树候选。保护名单仍使用移动前的 `tests/` 与 `synth/results/`。本次预览已列出 Git 跟踪的 [Vivado 检查报告](../tests/fixtures/vivado_2018_3_buffered/check_timing.rpt) 和 [时序摘要](../tests/fixtures/vivado_2018_3_buffered/timing_summary.rpt)。在保护范围更新前，仓库根目录只用此脚本预览，清理时逐项核对 [综合证据导航](../synth/README.md) 和 `git ls-files`。

## 远端预览

```bash
REMOTE_HOST=your-eda-host bash flow/scripts/clean_remote.sh
```

将 `your-eda-host` 换成已配置的 SSH 主机别名。预览也会通过非交互 SSH 访问远端。适用目录由脚本固定，范围包括旧 VCS 编译目录、DC 工作目录和旧 timing 工作区。删除模式按候选目录递归处理，目录内部的报告、网表或日志随目录一起删除。运行前先确认主机、目录归属和当前作业状态。 [脚本中的实际匹配范围](clean_remote.sh)
