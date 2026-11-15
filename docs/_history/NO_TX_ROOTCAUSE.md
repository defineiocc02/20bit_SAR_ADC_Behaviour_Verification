# NO_TX 根因：trace TB 的 3 位索引截断（RTL 无错）

## 一、根因分析

| 项 | 值 |
|:---|:---|
| Follower 端口 | `input logic [3:0] sar_index_i` |
| **trace TB 声明** | `logic [2:0] evi`（**3 位**） |
| 第 9 个事件 n=8 | TB 送 `evi = n[3:0]` → 截成 `3'b000` = 0 |
| Follower 期望 | `exp_idx = 8` |
| 触发链路 | `idx_bad = (0 != 8)` → `proto_err_o=1` |
| 结果 | 提交条件被 `!ev_proto_err_o` 阻断 → `done=1` 但 `final=0` |

**这正是"done=1 / final=0"的完整解释。**

## 二、我在此前的三个错误判断（均已撤回）

| 错误判断 | 真相 |
|:---|:---|
| "提交块读旧值，idx==8 永不成立" | **错。** 下一沿读已寄存的 `ev_idx_o==8` 是正常流水 |
| "`last_ev_o` 是必要修复" | **错。** 用 `last_ev_o` 与用 `idx==8` 结果相同，`last_ev_o` 仅作辅助断言 |
| "FINAL: valid_o=0 说明没提交" | **错。** `valid_o` 是单拍脉冲，FINAL 打印在脉冲之后 |

**三次都是在证据不完整时就改 RTL 或下结论。**

## 三、修复与验证

只改 TB：`logic [2:0] evi` → `logic [3:0] evi`。**RTL 一行未动。**

逐拍观测（4 位索引，`idx==8` 判据）：

```
cyc 17  ev_vld=1 ev_idx=8 last=1 perr=0 final=0      <- 第 9 事件吸收
cyc 18  ev_vld=0 ev_idx=8 final=1                    <- 提交置位
cyc 19  final=0 valid_a=1                            <- 进 E1
cyc 22  valid_a=0 valid_o=1 tag_o=900                <- 20 位结果输出
```

## 四、关于主 TB 那 1 例 NO_TX

`tb_follower_int.sv` 的索引本就是 `logic [3:0]`，**此错误无法自动解释它的 1 例失败**。
两者需分别验证。当前主 TB 在 `2889f33` 的握手修复后为 0 错误，
但那次修复含"TB 多加一拍等待"——**按审阅要求该等待不可作为修复手段**，
需在去掉后重新确认。

## 五、proto_err 改为事务级粘滞

```systemverilog
proto_err_q <= proto_err_q || idx_bad || qid_bad;
```

一旦本笔出现位序/qid 违例，后续正常事件不得把它清回合法。

## 六、回归

| 项 | 结果 |
|:---|:---|
| 5544 事件 | **0 错误** |
| 64 组双模式等价 | **0 不一致** |
| 8509 笔整码 | **PASS** |
