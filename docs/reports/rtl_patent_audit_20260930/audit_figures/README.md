# 审查图件的可复现构建

这里的八个制图入口及一个 compact schema 读取模块只读取既有证据，不调用 Verilator、Vivado 或远端命令。当前包含 30、31、32、38、39、40、四张 CI 回归图及独立算术双版本图。图32已从两份完整有效P7综合归档生成，明确显示LUT增加16.23%和仍然为负的时序裕量。

## 口径与交付物

| 脚本 | 输出至 `../figures/` | 证据范围 |
|---|---|---|
| `make_arithmetic_protocol_overview.py` | `30_arithmetic_protocol_audit.png` | 历史算术审计及两项协议负对照；保留原始计数和来源 |
| `make_divider_profiles.py` + `make_compact_divider_profiles.py` | `31_divider_profiles.png` | `compact_profiles` 的 P=5/6/7 生产顶层各 438 次输出；此次 compact 运行没有重复独立流式 bench |
| `make_tree_mapping_trace.py` | `38_tree_mapping_trace.png` | 小单元实际 XSim CSV、旧网表失败行及输入扇出 |
| `make_reducer_scheduler.py` | `39_reducer_scheduler.{png,pdf}` | 双版本独立 scalar、真实 16 行数值及共同陈旧输入负控 |
| `make_weight_msb_evidence.py` | `40_weight_msb_evidence.{png,pdf}` | 合法写 MSB 不变量、功能等价及旧基线结构见证；不代替优化后 PPA 实测 |
| `make_final_regression_evidence.py` | `final_regression_{arithmetic,protocol,system,quantitative}.{png,pdf}` | `ci_rtl_417f` 的 22 项 RTL 日志，三张逐项矩阵及一张定量图 |
| `make_arithmetic_runner_evidence.py` | `arithmetic_runner_dual_tool.{png,pdf}` | `arithmetic_runner_ci` 的本地双版本正控和两项真实负控 |
| `make_vivado_comparison.py` | `32_vivado_comparison.png` 及同名 JSON | `vivado_complete_p7` 两份同器件、工具、P7和周期的有效综合工件；不是布线或ASIC签核 |

每张图的对应来源清单位于本目录：`30_source_hashes.json`、`31_source_hashes.json`、`38_source_hashes.json`、`39_source_hashes.json`、`40_source_hashes.json`、`final_regression_*.sha256.json` 及 `arithmetic_runner_dual_tool.sha256.json`。清单包含生成脚本、输入和输出摘要；四张 CI 图还保存逐项计数定义、测试台行号、不可变 Git 输入及绘图数据。

30 是历史证据，不能用新的 divider 审计文件覆盖输入后仍称为原图。31 当前读取 `docs/evidence/20260930/compact_profiles/`。旧 `final_profiles/` 含旧快照的结构顶层和独立流式日志，保留历史归属；新 `ci_rtl_417f` 另有 P5/P6/P7 各 32 笔、间隔 16 拍的流式实跑，两者不能混称一次实验。38 的小单元映射证据不能代替全顶层功能等价、布局布线或 ASIC PPA；四张 CI 图是零门延迟 RTL 日志证据，也不是 STA。

四张 CI 图严格绑定已发布的 `417f261` 行和缓存候选 / RTL 内容摘要 `1e3fab94b7b2…`，包含 22 项 RTL 回归，不把功能验收解释为 PPA 收益。RTL job `109627112125` 已成功；归档抓取时 workflow `36633152469` 尚为 `in_progress`，单个 job 成功不能替代整个 workflow 的结论。归档内 272 项 physical-flow 检查是单元/mock 检查，不是 Vivado 实跑。

仓库已提交的历史 `evidence_hashes` 清单记录当时脚本版本，不因报告更新而覆盖。旧 169f 四图及生成材料保存在 `../history/final_regression_169f_before_ci_0c4a/`；旧 0c4a 失败 job 图件、脚本、分节与 QA 保存在 `../history/final_regression_ci_0c4a_before_f028/`；f028 成功 job 的四图、脚本、分节、QA 及摘要保存在 `../history/final_regression_ci_f028_before_417f/`。本次成功 CI 四图确实重新生成，不能宣称与旧图字节一致。

## 本机重建命令

路径均通过参数指定；迁移至新的 Git 交付目录后修改以下变量即可。需要 Python 3.10 或更新版本、Matplotlib、NumPy；代码检查使用仓库依赖中的 Ruff 0.6.9。本次实际环境为 Matplotlib 3.11.2、NumPy 2.5.3、Ruff 0.6.9。不同绘图库/字体版本可能改变图像字节，须以新清单记录，不能期待跨版本 PNG 哈希相同。

```sh
SAR_REPORT='/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_patent_rtl_report_20260928'
SAR_REPO='/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260919/calibration-closure'
SAR_PYTHON='/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260912/.venv-runtime/bin/python'
SAR_HIST_ARITH='/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_calibration_audit_20260930/reproducible_run'
SAR_HIST_PROTOCOL="$SAR_REPO/sim/artifacts/open_rtl"
SAR_TREE_EVIDENCE='/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_vivado_20260930/tree_fix'

cd "$SAR_REPORT"

"$SAR_PYTHON" -m ruff check \
  --config "$SAR_REPO/pyproject.toml" \
  audit_figures/make_final_regression_evidence.py \
  audit_figures/make_arithmetic_runner_evidence.py
"$SAR_PYTHON" -m ruff format --check \
  --config "$SAR_REPO/pyproject.toml" \
  audit_figures/make_final_regression_evidence.py \
  audit_figures/make_arithmetic_runner_evidence.py

"$SAR_PYTHON" audit_figures/make_arithmetic_protocol_overview.py \
  --arithmetic "$SAR_HIST_ARITH" \
  --protocol "$SAR_HIST_PROTOCOL"

"$SAR_PYTHON" audit_figures/make_divider_profiles.py \
  --evidence "$SAR_REPO/docs/evidence/20260930/compact_profiles"

"$SAR_PYTHON" audit_figures/make_tree_mapping_trace.py \
  --evidence-root "$SAR_TREE_EVIDENCE"

"$SAR_PYTHON" audit_figures/make_final_regression_evidence.py \
  --schema ci \
  --repo "$SAR_REPO" \
  --evidence "$SAR_REPO/docs/evidence/20260930/ci_rtl_417f"

"$SAR_PYTHON" audit_figures/make_reducer_scheduler.py \
  --evidence "$SAR_REPO/docs/evidence/20260930/reducer_scheduler"

"$SAR_PYTHON" audit_figures/make_weight_msb_evidence.py \
  --evidence-root "$SAR_REPO/docs/evidence/20260930"

"$SAR_PYTHON" audit_figures/make_arithmetic_runner_evidence.py \
  --evidence "$SAR_REPO/docs/evidence/20260930/arithmetic_runner_ci"
```

需要重新排版代码时，将 `ruff format --check` 改为 `ruff format`；随后再次检查并重建受影响图件，以更新脚本 SHA。多数入口默认以脚本所在报告目录确定输出位置；39/40 也接受 `--report`，综合比较显式要求 `--output`。compact 读取模块由 `make_divider_profiles.py` 自动调用，无需单独执行。没有隐藏的仿真或额外波形生成。

## 必须保留的原始工件

### 局部行更新的同约束综合与布线负证据图

使用冻结的完整顶层综合 A/B 与失败布线归档重画报告中的局部行更新 PPA 图：

```sh
"$SAR_PYTHON" audit_figures/make_local_row_ppa.py \
  --baseline-route "$SAR_REPO/docs/evidence/20260930/vivado_cached_p5_route_25ns_fail" \
  --synth "$SAR_REPO/docs/evidence/20260930/local_row_trial_synth" \
  --route "$SAR_REPO/docs/evidence/20260930/local_row_trial_route_congestion" \
  --out figures/local_row_ppa
```

脚本先验证三份归档的81项原始文件哈希与两版DCP身份，再绘制综合LUT和物理通过/失败状态。失败版本没有最终布线WNS或hold；图中不使用中间WNS代替最终结果。

### 图 30：历史外部算术和协议负对照

`--arithmetic` 下必须包含：

```text
manifest.json
run.log
```

`--protocol` 下必须包含：

```text
structural_protocol.negative_controls.json
structural_protocol_tb.run.log
structural_protocol.baseline.negative.log
structural_protocol.cancel_mutant.negative.log
```

这些文件的原始绝对路径和 SHA 在 `30_source_hashes.json`。算术目录位于仓库外；协议原路径在 `sim/artifacts/`，不能假定普通 Git clone 自动带有这些运行产物。交付时需保留这六个原始文件，或用参数指定其逐字节相同的归档副本。缺少负对照原始失败日志时，不能只凭成功摘要重建该图。

### 图 31：compact 三档参数对照

在 `docs/evidence/20260930/compact_profiles/` 保留：

```text
manifest.json
rtl_compact_identity.json
evidence_hashes.json
archive_manifest.json
p5/run.log
p6/run.log
p7/run.log
```

每档均为三种模式、438 次输出、438 次精确延迟检查、7,680 个协议检查节拍，P5/P6/P7 延迟依次 15/13/11 拍。图中的 16 拍预算是设计调度标注；本次 compact 实验没有测 busy 释放，也没有重复独立流式 bench。脚本不会为 compact schema 伪造旧 `final_profiles` 文件。

完整 build log、生成对象和冻结源码保留在外部 campaign，由归档 manifest 的路径及哈希追溯；只制图无需访问它们。`make_divider_profiles.py` 仍支持旧 schema，但用旧 `final_profiles/` 重建会替换当前 31，因此只应在独立历史报告副本内执行。

### 图 38：仓库外完整 XSim CSV 与扇出表

`--evidence-root` 接收 `tree_fix` 层级，不能误传其下的 `mapped_trace_final`。该目录必须保留以下八个文件：

```text
mapped_trace_final/manifest.json
mapped_trace_final/trace_after/trace.csv
mapped_trace_final/trace_baseline_fixed/trace.csv
mapped_trace_final/trace_before/trace.csv
mapped_trace_final/trace_before/reducer_negative.csv
mapped_witness_retry/out_before/input_fanout.csv
mapped_witness_retry/out_after/input_fanout.csv
mapped_witness_retry/out_baseline_fixed/input_fanout.csv
```

两个成功 trace 分别为 12,904 行数据、约 3.47 MB；三个扇出表分别含 969 个输入。脚本读取完整 CSV，检查成功行、相同刺激下两种修复公式的逐行一致性、失败行和连通数量，然后仅截取已注明的区间绘图。不能只配送图中可见的子区间 CSV，或拿 RTL 日志替代 XSim 输出。路径参数允许工件另行打包，不要求固定在当前 `outputs` 树。

### 四张 CI 图：22 项日志与相同 Git 版本测试台

使用 `--schema ci` 与 `docs/evidence/20260930/ci_rtl_417f/`。必须保留完整归档及其 `sha256.json`，包括根 manifest、job metadata、被测 merge/tree 信息、88 项输入清单、26 项 RTL 摘要、22 份运行日志、3 份 lint 日志，以及 `arithmetic-audit/manifest.json` 和 `run.log`。脚本逐字节读取本地 Git 中 `417f261f24fe917552ac71f6aadb6b3dbf779c5f` 的源文件；浅克隆或历史缺失需先取得该对象，不能退回当前工作树。

新增 `weight_row_cache_tb` 的五种几何共 16,150 个事务步使用独立保留权重字求和，并以独立装载位图的全与结果检查 complete 端口；归约器七种几何均打印 `cache=1`，三份实现逐笔与独立 scalar oracle 对照。脚本维持原 21 项集合并只允许此一项扩展。

脚本分别核对 RTL step 与 job 结论；成功 job 还要求独立算术步骤、完成计数及七项来源 SHA 一致。旧 `ci_rtl_0c4a` 失败-job schema 仍可用于历史重建，必须保留启动器失败的 127 返回码；它不会被默认升级为成功。`--schema local` 专用于旧 `final_rtl/` schema，不适用于 CI 归档。历史重建应在独立目录进行，避免覆盖当前四图。

四图不需要 build log、巨大生成向量或仿真器。存在逐样本记录的只有 FIT_ROW 部分；其余面板使用真实摘要统计，没有从完成标记制造波形。

## 图 32 的门禁

实际P7比较来自仓库 `docs/evidence/20260930/vivado_complete_p7/`。每个 `--run '名称=工件目录'` 完整保留 `manifest.json`、`vivado.log` 以及 `out/` 下的 `status.txt`、`utilization.rpt`、`utilization_hier.rpt`、`timing_paths.rpt`、`timing_summary.rpt`、`check_timing.rpt`、`drc.rpt`、`power_vectorless.rpt`。大报告允许原字节无损压缩为 `.rpt.gz`；脚本同时记录存储文件与解压后原报告的SHA-256。若有独立审查文件 `reviewed_result.json`，脚本亦读取其有效性结论。

```sh
"$SAR_PYTHON" audit_figures/make_vivado_comparison.py \
  --run "Repaired baseline P7=$SAR_REPO/docs/evidence/20260930/vivado_complete_p7/baseline" \
  --run "Column/borrow P7=$SAR_REPO/docs/evidence/20260930/vivado_complete_p7/optimized" \
  --output figures/32_vivado_comparison.png
```

生成的JSON保存全部原始输入摘要，`32_source_hashes.json`另记录绘图脚本和输出图件。图显示LUT从171,246增至199,039，而WNS仍为负值，不能称这一改写带来PPA优势。

只有完成且未被审查否定的结果才进入比较；脚本拒绝已观察到的无驱动缺陷，并要求器件、工具版本与时钟周期一致。该检查仍不等于自动证明全顶层功能正确，也不把 OOC、vectorless 功耗或负 WNS 解释成 ASIC/板级签核。证据不完整时不生成占位图，不引用旧无效网表作有效 PPA 基线。

## Figure 39: reducer scheduling and independent scalar evidence

`make_reducer_scheduler.py` reads the committed `docs/evidence/20260930/reducer_scheduler` archive. It validates its hash index, all seven completion counts, both successful exit records, the exact first16 logged rows, and the deliberate common-stale mutation's check9 failure. It plots T/G/R numerical values, not reconstructed waveforms. Signed R values are masked to the valid 66 bits before two's-complement decoding; simulator storage padding is discarded.

```sh
python audit_figures/make_reducer_scheduler.py \
  --evidence /path/to/calibration-closure/docs/evidence/20260930/reducer_scheduler
```

Outputs are `figures/39_reducer_scheduler.png`, the matching vector PDF, and `audit_figures/39_source_hashes.json`. No new simulation is run. This figure requires only that archived repository directory; the earlier diagnostic build trees in `/tmp` are not inputs. The two simulator versions each completed 1,430,848 samples; those counts are not added together as distinct test coverage. The visible curves show only the 16 logged directed samples of one geometry, and the shared-input negative control is explicitly a detected failure.

## 图 40：合法写 MSB 不变量与结构证据

`--evidence-root` 指向仓库的 `docs/evidence/20260930/`，由脚本读取其 `weight_msb/` 与 `vivado_structure/` 两个归档，而不是直接传其中一个子目录。必须保留两份哈希清单、修复前后 `weight_store` 源码、等价测试台、验证 manifest、原始 `equiv.run.log` 及基线 primitive 见证。图中数学不变量、基线实际 FF 计数、3 种几何的有限功能回归分别标注；它的来源归档没有修复后的面积实测，不能仅凭 MSB 恒零推定真实 LUT/FF 节省。后续 PPA 比较属于独立测量。

## 独立算术双版本图

`make_arithmetic_runner_evidence.py` 使用 `arithmetic_runner_ci/` 的完整哈希归档，包含两个通过 manifest 和日志、陈旧 MAC/固定 ADC 两个负控日志及返回码、路径环境单测日志。图只取日志中的精确计数和失败行数值：两种工具各自为 MAC 40,010（合法非溢出 20,859 为其子集）、ADC2 40,000、divider 10,048、flags 8；两版本复用相同向量，计数不相加为更大覆盖。

负控分别在 MAC 第 3 行、ADC 第 2 行触发独立判据，失败是预期结果；8 项 runner 单测另标为过程夹具，不算 RTL 仿真。该图明确是本地双版本证据；本次 417f Ubuntu CI 的独立算术通过来自另一个 `ci_rtl_417f/arithmetic-audit/` 归档，不冒充第三个本地工具或把旧 0c4a 失败改为成功。

本次四图制图 QA 见 `ci_417f_figure_upgrade_qa.json`：输入/输出读回、不可变 Git 绑定、篡改拒绝、新缓存判据、三代历史文件读回及静态 TeX 检查。此前本地独立算术图的 QA 保留于 `ci_f028_figure_upgrade_qa.json`，本次不重新生成该图。该检查没有执行 EDA，也没有编译报告主文。

## 图 41：行和缓存的双版本与负控证据

```sh
"$SAR_PYTHON" audit_figures/make_row_cache_evidence.py \
  --evidence "$SAR_REPO/docs/evidence/20260930/row_cache"
```

脚本验证42项归档文件、26项RTL内容身份、两个版本各16,150个加载步骤与1,430,848次归约比较，并以任意精度整数重算十个已记录行和。只绘制最大装载和clear后替换两组实际观测，不推造未记录的clear瞬间波形。生产者写回与消费者缓存输入的两项独立破坏均使用实际失败日志。源码集合和输出图件摘要见 `41_source_hashes.json`；四类篡改拒绝与原分节静态检查见 `41_qa.json`。

后续只调整图41的TeX分页，未改图件、数据或制图脚本；旧分节已按其原摘要保存在 `history/row_cache_before_layout_fix/`。`41_layout_followup_qa.json` 记录新的分节摘要、两遍编译及第74/75页放大检查：替换公式完整位于图前，没有被整页浮图截断。这是78页中间版本的布局检查，最终交付仍以 `delivery_manifest.json` 所绑定的PDF与全页QA为准。

## 当前缓存P5完整顶层映射功能图

```sh
python audit_figures/make_mapped_p5_evidence.py \
  --archive /path/to/calibration-closure/docs/evidence/20260930/vivado_cached_p5_full_mapped \
  --out figures/mapped_p5_full_top
```

脚本核对归档清单中96项原始payload的SHA-256、最终运行的trace/log摘要，再逐行检查三模式各146个码值和flags、各11个跳过ID、各1个取消ID以及435对相邻有效样本的16拍间隔。生成的PNG、PDF和 `mapped_p5_full_top.sha256.json` 绑定本次数据与脚本；它们仅展示完整顶层功能网表在这组刺激下的结果，不是SDF或布局布线时序证据。归档保留前两次导出/端口适配失败，只有 `final_pass/` 是通过运行。

## 当前缓存P5实际布线失败与寄存器间STA图

```sh
python audit_figures/make_p5_route_evidence.py \
  --synth /path/to/calibration-closure/docs/evidence/20260930/vivado_cached_p5 \
  --route /path/to/calibration-closure/docs/evidence/20260930/vivado_cached_p5_route_25ns_fail \
  --out figures/p5_route_25ns
```

脚本验证综合原始25项、布线原始35项和只读寄存器引脚STA的6项哈希，再读实际时序报告及reviewed result。图中25\,ns为申请周期，布线WNS为负；OOC全路径hold为负，而同一DCP的寄存器到寄存器hold为正。后者没有替外部配置端口提供板级最短输入延迟。生成器固定PDF元数据时间，以便同源重复生成字节一致。

## 局部行更新候选的三档结构回归图

```sh
python audit_figures/make_local_row_profiles.py \
  --archive /path/to/calibration-closure/docs/evidence/20260930/local_row_trial_rtl \
  --out figures/local_row_profiles
```

脚本核对该候选57项原始日志/身份文件哈希和22项回归摘要，再从P5/P6/P7真实完整顶层RTL日志读取三模式、438输出、7,680协议周期、438延迟检查及15/13/11拍观测。图只证明零延迟数字功能；尚不包含映射、真实布线或模拟性能。
