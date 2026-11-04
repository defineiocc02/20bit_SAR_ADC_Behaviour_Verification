# Vivado FPGA 结构筛选

本流程比较同一 FPGA 型号、Vivado 版本和约束下的 RTL 资源与综合后时序。
它不等价于 ASIC 面积、功耗或布线后签核；没有真实活动文件的功耗报告只作
vectorless 估计，不能据此宣布低功耗优势。

## 实际运行

先在已授权 EDA 主机确认可执行文件、许可证和安装器件；不要猜器件型号。
本轮已实测 `windows-codex`（现有SSH别名）为 Windows 主机
`Administrator@100.74.122.21`，机器名 REEDZHAO；Vivado 位于
`D:\Academic\Vivado2018\Vivado\2018.3\bin\vivado.bat`，版本2018.3。
用户指定的参考会话使用同一Windows跳板访问 `vm-meow`；Vivado安装在Windows本机。

已查询并确认 `xc7vx690tffg1761-2` 可用，实际对照命令为：

```sh
python synth/run_vivado_ssh.py --remote-os windows --host windows-codex \
  --vivado 'D:\Academic\Vivado2018\Vivado\2018.3\bin\vivado.bat' \
  --part xc7vx690tffg1761-2 --stages 7 --period 1.5625
```

新旧RTL首先只替换reducer进行P7比较；随后将候选的 `--stages` 改为6、5。
Windows分支使用UTF-8输出及编码PowerShell命令，在USERPROFILE下建立唯一目录，
保留批处理、控制脚本和远端退出码。超时仅终止此次启动的进程树。
Linux路径仍受支持；将 `FPGA_PART` 设置为实际安装且容量足够的器件：

```sh
python synth/run_vivado_ssh.py --part "$FPGA_PART" --stages 7 --period 1.5625
python synth/run_vivado_ssh.py --part "$FPGA_PART" --stages 6 --period 1.5625
python synth/run_vivado_ssh.py --part "$FPGA_PART" --stages 5 --period 1.5625
```

可用 `--settings /absolute/path/settings64.sh` 或 `--vivado /absolute/path/vivado`。
`--prepare-only` 只生成源文件包，不声称已综合。源代码通过清单复制到新建目录，
不覆盖远端已有工程；SHA-256、参数、工具版本、原始日志、DCP 与报告均保留。
远端默认 3600 秒超时，TERM 后 30 秒强制结束；SSH 连接超时 8 秒。

## 固定比较口径

- 顶层固定 `sar20_digital_core`，结构模式默认开启，全 18×71 项可配置物理权重。
- `out_of_context`，`flatten_hierarchy=rebuilt`，不做 I/O pad 插入。
- 为避免挤占EDA主机，固定两线程，比较任务逐个执行。
- 目标 1.5625 ns（640 MHz）；clock uncertainty 0.05 ns，输入/输出 delay 均为 0。
  这是结构筛选约束，不能替代模拟宏或板级实际 setup/hold 预算。没有多周期或假路径掩盖关键路径。
  Vivado2018.3现场提示时钟周期舍入为1.563ns；最终必须同时报告请求值与工具实际值。
- 记录 LUT/FF/DSP/BRAM、分层资源、WNS/TNS、20 条路径、未约束端点、DRC 和
  vectorless 功耗。必须阅读 `check_timing.rpt`，不能只看程序退出码。
- 返回 0 仅表示综合完成且最大延迟路径的 setup WNS 非负；返回 3 表示综合完成但
  WNS 为负。两者都不表示 hold、DRC、实际功耗或布线后时序已经签核。
- 新旧 RTL 比较必须从各自提交使用相同流程、器件、参数与约束，不能将不同 FPGA
  或不同频率约束下的资源差异归因于 RTL 优化。

## 2026-09-30 现场状态

旧默认地址 `yian@192.168.38.129` 直连超时。经用户指出参考会话后，已按已有SSH配置
连接Windows主机并实测启动Vivado，确认版本、器件，基线已进入RTL综合与优化。
完整结果另见审查记录；不能把启动或局部阶段完成视为综合成功。
最初Windows打包阶段遇到PowerShell诊断编码错误，已修复并增加回归。
本地37项mock/Tcl流程测试通过，仍与EDA实跑结果分开记录。

参考 AMD 官方命令说明：
[report_utilization](https://docs.amd.com/r/2025.2-English/ug835-vivado-tcl-commands/report_utilization)、
[report_timing_summary](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/report_timing_summary)。
