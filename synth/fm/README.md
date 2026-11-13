# Formality 等价验证

## 状态（2026-11-13 22:45）

**运行中（曾被我误判为卡死并杀掉一次，已更正）。不得记为 PASS。**

## 更正：不是卡死，是子进程在算

我第一次监控的是 `fm_shell`（父进程），其 CPU TIME = 00:00:00、%CPU = 0.0，
据此判断"卡住"并 `pkill`。**这个判断是错的。**

重跑后观察整机进程：

```
COMMAND         TIME    %CPU
fm_shell_exec   04:05   93.5%     <-- 真正干活的子进程
```

Formality 把 SAT 计算放在 `fm_shell_exec` 子进程里，父进程只做调度，
所以父进程 CPU 为 0 是**正常状态**，不是挂死。我错杀了它一次。

教训：判断 EDA 进程是否在算，要看**进程树**（子进程 CPU），
不能只看主进程。

## 进度

```
read_sverilog(RTL)      OK
read_db(slow.db)        OK
read_verilog(netlist)   OK
match                   38 输入全匹配、0 未匹配 compare point
verify                  Status: Verifying...（已 4+ 分钟 CPU，仍在算）
```

## 为什么慢

被测设计含 42/54 位乘法器与除法链，SAT 状态空间大。
这是真实验证成本，不是工具故障。

## 踩过的坑（都记下来，省下次）

| 现象 | 原因 | 修法 |
|:---|:---|:---|
| `Syntax error near 'k_i'` | Formality 用 `read_verilog` 读 SV | 改 `read_sverilog` |
| `extra positional option <一堆文件>` | `read_*` 不吃花括号列表 | 用 `-f filelist` |
| `Can't open file -01` | `-01` 不是合法 flag | 去掉 |
| `Unresolved references during link` | 网表引用 SMIC 单元但没库 | 先 `read_db slow.db` |

## 复现命令

```bash
export SNPSLMD_LICENSE_FILE=27080@localhost
cd ~/sar20/synth
/opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell -no_init -f synth_with_netlist.tcl
# 生成 fm4.tcl 后：
/opt/synopsys/fm/W-2024.09-SP3/bin/fm_shell -no_init -f fm4.tcl
```

## 为什么慢

被测设计含 42/54 位乘法器与除法链，Formality 做 SAT 时状态空间大。
这是**真实存在的验证成本**，不是工具坏了。
