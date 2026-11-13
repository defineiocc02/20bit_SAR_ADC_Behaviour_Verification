# Formality 等价验证

## 状态（2026-11-13 22:45）

**运行中，未出结论。** 不得记为 PASS。

```
netlist.v        已生成（316 KB，DC W-2024.09-SP3 + SMIC180 slow.db）
Formality        read_sverilog(RTL) -> read_db(slow.db) -> read_verilog(netlist)
                 match 完成，verify 处于 Status: Verifying...（>5 分钟未收敛）
```

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
