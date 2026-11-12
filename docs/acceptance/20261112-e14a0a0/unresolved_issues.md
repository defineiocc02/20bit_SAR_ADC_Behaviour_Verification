# 未关闭问题（受测版本 e14a0a0）

按严重程度排序。每项给出文件/函数、最小复现、影响范围与下一步。

## 高

### U1 主引擎单函数 743 行
- **位置**：`src/adi_model/simulation/pipeline_engine.py::execute_pipeline`
- **最小复现**：`python3 -c "import ast,pathlib; ..."` 或 `wc -l`；AST 统计得 743 行
- **影响**：改动风险高；读者必须在同一函数内跨"装配/采集/逐事件/解码"四段来回跳
- **下一步**：按四段拆分，用行为指纹逐位对照（`--check` 已具备）

### U2 V3 功能级未验证
- **位置**：`rtl/top/sar20_digital_core.sv`
- **最小复现**：`python3 -m verification.run_verification --level rtl_top` → `ELABORATED_ONLY`
- **影响**：不能声称"整链无丢样/重样/乱序"，不能给出 `N_accepted = N_retired + N_cancelled + N_pending`
- **下一步**：写完整配置映射（71 权重 + offset/min/max + 18 窗口）→ `cfg_validate` → 逐相位比较器判决

### U3 逐码校准与 DEM 原理性不兼容
- **位置**：`src/adi_model/calibration/mask_calibration.py`；矩阵 C1/C3
- **最小复现**：`tools/run_p4_ablation.py`，比较 A3（6.2）与 C1（26.9）
- **影响**：校准不能与 DEM 同开；当前所有"校准改善"结论只在 DEM 关闭时成立
- **下一步**：把特征从"逐码"改为"活动 mask × 单元权值"（详见 `docs/reviews/PR9_RESPONSE_20261112.md` §5）

## 中

### U4 DNL 不是码密度 DNL
- **位置**：`tools/run_p4_ablation.py::_static`（字段 `dnl_bin_note`）
- **复现**：读矩阵 JSON 的 `dnl_bin_span_lsb20`
- **影响**：不能声称"20 位 DNL"；只能做机制趋势比较
- **下一步**：转换阈值搜索或码密度法，输出独立字段而不是复用 `dnl_bin_*`

### U5 V2 缺统一事务 trace
- **位置**：`verification/cosim/scoreboard.py`
- **复现**：当前按字段比较，无 `epoch + sample_id + event_index` 三元组
- **影响**：事件排序、背压、错误归属未验证
- **下一步**：扩 trace 字段，按事务身份关联而非行号

### U6 ruff 74 / mypy 8 既有债务
- **位置**：全仓（ruff）；`behavioral/reference.py`、`contracts/events.py`、`control/dem3d.py`、`__init__.py`（mypy）
- **复现**：`ruff check .`、`mypy src/adi_model`
- **影响**：CI 的 lint/type job 红；但基线即为 95/8，本轮未增加
- **下一步**：逐项补类型标注与文档串。两处同名 `DigitalObservation` 需先定名

### U7 4/6 专利机制无专题
- **位置**：`docs/mechanisms/`
- **影响**：复现范围只能声明 dither / DEM 两项
- **下一步**：follower、动态参考、TRACK/AUX、普通 AZ 按同一六段模板补写

## 低

### U8 44 个函数 >60 行
- **影响**：局部可读性
- **下一步**：随 U1 一起拆，带指纹对照

### U9 metrics 包级薄适配
- **位置**：`src/adi_model/metrics/__init__.py`
- **影响**：`from adi_model import metrics` 依赖这层转发
- **下一步**：收敛调用方后移除

### U10 β 闭式 vs 两节点求解未对照
- **影响**：`β 等效不精确` 这一说法既未证明也未推翻
- **下一步**：同一静态线性网络、同一边界条件下做两节点求解对照
