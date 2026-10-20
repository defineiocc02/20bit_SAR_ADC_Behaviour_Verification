# 形式验证（BMC）结果

> 日期：2026-10-20 · 工具：Yosys 0.70 + `yosys-smtbmc` + **Boolector 3.2.4**
> 复现：`bash fv/run_fv.sh [fifo|spi|all]`

---

## 1. 为什么不用 `sby`

本机 `sby` 的 Python 封装在创建工作目录时被 WorkBuddy 的 `mkdir` 兼容层拦住
（`PermissionError: EEXIST ... /model`）。因此绕开 SBY，直接用
`yosys prep → write_smt2 → yosys-smtbmc`。`fv/*.sby` 仍保留，供普通机器使用。

## 2. 验证的属性与结果

### 2.1 `spi_slave`：写提交地址（仿真抓到过的 bug）

```systemverilog
assert (!we || (addr == addr_ptr));   // 写提交必须落在本字节的指令地址
assert (!we || !cmd_r);               // 读事务绝不产生写
assert (addr[15] == 1'b0);            // 地址只用 15 位
```

- **BMC depth 60 → PASSED**（Boolector，5 分 51 秒，无违例）。
- 为什么是条件式：`addr_ptr` 在 `cs_fall` 清零，而 `addr` 保留上一帧值，
  两者在**指令相**本来就不等；只在 `we=1` 的写提交拍必须相等。
  这一点写进注释，避免后来者把它"简化"成无条件断言。

### 2.2 `result_fifo`：容量与读写边界

```systemverilog
assert (count <= V_DEPTH);                          // 占用量不超过深度
assert (!(wr_fire && full && !overwrite_en));        // 满不写
assert (!(rd_fire && empty));                        // 空不读
```

- 用小实例 `DATA_W=4, DEPTH=8`（避免 16K 存储展开），
  **BMC depth 40 → PASSED**（1 秒，无违例）。

## 3. 这组形式验证能声称什么

| 能声称 | 不能声称 |
|---|---|
| 从复位起 60/40 拍内，上述断言无违例 | **不是无界证明**（BMC 是有限深度） |
| SPI 写地址偏移这一类 bug 有形式化闸门 | 没有覆盖 CRC/streaming 的全部状态 |
| FIFO 不会写出深度、不会满写/空读 | 没有证明存储内容正确（数据通路靠 TB） |

## 4. 为什么没做成无界证明（如实）

对 `we -> addr==addr_ptr` 做 k-归纳失败：设计里存在**不可达状态**
（例如 `phase/bitcnt` 任意组合下 `addr` 从未装载却出现 `we`），
需要补充辅助不变式（phase/bitcnt 与帧结构的一致性）才能归纳。
这属于"可以继续做"的工作，不是工具缺失。当前用 BMC 深度覆盖一次完整
24-bit 帧的最快合法时序，作为回归闸门。

## 5. 复现

```bash
export PATH=$HOME/.workbuddy/tools/oss-cad-suite/bin:$PATH
cd ad4080_rtl
bash fv/run_fv.sh fifo   # 约 1 秒
bash fv/run_fv.sh spi    # 约 6 分钟
```
