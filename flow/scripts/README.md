# 清理检出里的临时产物

两个入口使用同一份 [clean.py](clean.py)。默认预览。显式 `--apply` 只处理命中临时文件模式的未跟踪路径。Git 索引中的文件、包含跟踪文件的候选目录、嵌套仓库和 `flow/tests/` 等保护目录会跳过。

```bash
python3 -B flow/scripts/clean.py
python3 -B flow/scripts/clean.py --root .
```

远端预览需要明确的 SSH 主机和检出绝对路径。该入口把本次检出的规则通过标准输入交给远端 Python，在远端 Git 索引上判断文件归属。未提供目标时在连接前退出。[远端入口](clean_remote.sh)

```bash
REMOTE_HOST=your-eda-host bash flow/scripts/clean_remote.sh --root /path/to/checkout
```

独立任务的日志与工件按原运行目录管理，见 [EDA 工作目录规范](../../docs/audit/REMOTE_WORKDIR_POLICY.md)。
