# 撤回："NDM 库没有 PG 引脚" —— 该判定是错的

时间：2026-11-16
撤回对象：`FC_LIBRARY_NO_PG.md` 的结论

## 1. 我错在哪

我用 `get_lib_pins -of_objects <lib_cell>` 查引脚，得到：

```
BUFFD2 的 pins = I, Z
```

**就断定"库没有 PG 引脚"，这是错误的。**

该 API **默认只返回信号引脚**，不返回电源/地引脚。

## 2. 正确的查法

```tcl
get_lib_pins */*BUFFD2BWP7T40P140/VDD   -> 1 个，direction = in
get_lib_pins */*BUFFD2BWP7T40P140/VSS   -> 1 个，direction = in
get_lib_pins */*/VDD                    -> 839 个
get_lib_pins */*/VSS                    -> 839 个
```

**NDM 库里 PG 引脚完全存在，839 个 cell 全都有。**

## 3. 对比

| 说法 | 依据 | 真伪 |
|:---|:---|:---|
| "839 cell 全无 VDD" | `get_lib_pins -of_objects` | ❌ **错**（该 API 不含 PG）|
| "839 cell 全有 VDD" | `get_lib_pins */*/VDD` | ✅ **对** |
| "LEF 有 PG、NDM 丢了" | 前半对，后半错 | ⚠️ 部分错 |

**所以不存在"库缺 PG"这个问题，也不需要重建 NDM。**

## 4. 那 OPT-045 的真正原因是什么

回到原问题。`place_opt` 报：

```
Warning: Cannot find default buffer/inverter for VA DEFAULT_VA (OPT-043)
Error:   Cannot find usable buffers or inverters. (OPT-045)
Number of supply Nets   = 2
Number of used supplies = 0
```

既然库有 PG 引脚，`used supplies = 0` 的原因就在**别处**：

| 已排除 | 依据 |
|:---|:---|
| 库缺 buffer | 15 个 BUFFD，`is_buffer=true` |
| 库缺 PG 引脚 | 839 个 VDD/VSS 全在 |
| 设计只读 | `is_black_box=false` |
| `set_lib_cell_purpose` | 返回 0 |
| VA 未绑 PD | 已绑 `DEFAULT_POWER_DOMAIN` |
| VA 未绑 supply | `NDMUI-444` 不可改（这是**症状**，不是原因）|

**仍待查**：`used supplies = 0` 的机制。

## 5. 我的方法论错误

**这是一个反复出现的模式**：用一个"看起来对"的 API 查了一个侧面，
就把结论当成事实写进文档、提交、并向用户汇报。

同类错误这次会话已发生多次：

| # | 错误 | 真相 |
|:--:|:---|:---|
| 1 | "17 行权重被综合折叠" | 我的门级测试位序接反 |
| 2 | "库 839 cell 全无 PG" | API 查询不全，实际全有 |
| 3 | 多个面积数字 | 工具静默失败，子模块变黑盒 |

**共同点**：都是"验证手段本身有缺陷"，而不是被测对象有问题。

## 6. 正确做法

查"库是否有某类引脚"时，应同时用两种方式交叉验证：

1. `get_lib_pins -of_objects <cell>` —— 信号引脚
2. `get_lib_pins <cell>/<PIN_NAME>` —— 按名字单独查

**两者不一致时，以按名字查为准**（它不受 API 过滤影响）。

## 7. 对后端工作的影响

| 项 | 原判定 | 修正后 |
|:---|:---|:---|
| 是否需要重建 NDM | 需要 | **不需要** |
| 后端是否可行 | 阻塞在库 | **仍可能可行，卡在配置** |
| 结论 | 环境问题 | **未定，需继续查 `used supplies=0`** |
