# Row-total cache: local functional evidence

本目录只属于缓存候选 `1e3fab94b7b29b77113fa2e18eae99b6ee1c180cd7df6004625c85358392eef4` 的 **26 个 RTL 文件**。摘要方法和逐文件哈希见 `rtl_identity.json`；全部 RTL、TB、向量及 runner 的快照哈希见 `source_hashes.json`。归档时已逐字节核对工作树、测试快照和综合候选身份一致。父提交为 `f028ec5`，父提交 CI 结论不能作为本候选的新 CI 结论。

本候选的结构、位宽证明和配置契约见 [ROW_TOTAL_CACHE.md](../../../rtl/ROW_TOTAL_CACHE.md)。这里记录零延迟数字仿真；不证明资源节省、物理时序、ASIC 640 MHz 或模拟电路性能。后续必须按相同器件、P5 参数和时钟约束比较实际综合结果。

## 已验证结果

- `verilator_5_49/`：22 个 bench 全部通过；3 份生产严格 lint 日志也完整保留。原 21 项覆盖未减少，新增 `weight_row_cache_tb`。
- `verilator_5_020/`：新增 loader/cache 检查和扩展归约 miter 另在官方 5.020 上通过；仅这两项重复运行，不声称全部 22 项均已在该版本本地重跑。
- Loader 五几何共 16,150 个事务步，分别为 18×71: 3,572，32×128: 9,208，32×1: 1,080，1×128: 1,272，1×1: 1,018。每一步逐单元检查权重并独立重算行和；同时检查完整性位图、拒写和地址读回。每个几何先把**整个表**装满 `2^47-1`；clear 保留数值，随后替换必须减去旧权重。包括 reset、clear 与 write 同拍、锁定、零/高位非法权重、地址边界、上/下替换以及固定种子的随机事务。
- 扩展 miter 保留原 7 几何、**1,430,848 个样本**；旧公式、未缓存 DUT、缓存 DUT 分别比较独立逐物理单元 oracle。生产例含全部 43,758 个 8-of-18 分配 × 16 种 rail × 2 种 sampling 模式；另含重复/越界 ID、所有地址端点、零/合法最大/无符号全位图及随机数据。加载器拒绝的高位图样只用于叶级完整无符号接口的等价测试。
- 实际 18×71 顶层在 P5/P6/P7 各自通过 3 模式、438 输出、7,680 周期检查、438 延迟检查；实测输出延迟分别 15/13/11 拍。P5/P6 日志见 `profiles/`，P7 见默认 22 项中的 `structural_adc_tb.run.log`。小几何 `recon_ppa_latency_tb` 另以每配置 32 个样本检查 II=16 和 busy 释放 14/12/10 拍。
- `negative_controls/loader.run.log`：仅将行缓存替换结果最低位翻转，首次接受写的 step 2 即被独立 model 求和捕获。
- `negative_controls/consumer.run.log`：保持 raw/sample weights、旧公式及 scalar oracle 不变，只将活动行 0 的 `sampled_row_total` 最低位翻转，check 1 被 **cached DUT scalar mismatch** 捕获。这单独证明缓存 consumer 入口被实际使用，不能以“退回旧树但仍全部正确”的实现蒙混通过。

事务步、归约样本、顶层输出、周期检查和延迟检查是不同计量单位，不应相加为一个“覆盖总数”。`max_clear_replace.csv` 含原日志十个阶段观测及独立整数期望；两个工具版本的实际观测逐项一致。该表只有 `maximum_loaded` 和 `post_clear_replacement` 两个实测阶段，不能解释为连续时间波形或新增的单独 clear 当拍观测。

## 复现

在仓库根目录，用支持 `--timing` 的 Verilator 和 C++20 工具链运行：

```sh
VERILATOR=verilator python tools/run_open_rtl.py
```

该命令严格检查工具返回值、超时和每个 completion marker；无需改变默认 8192 elaboration budget 或测试次数。若只检查本候选新增路径，可选择 `--tops weight_row_cache_tb cal_weight_reduce_ppa_tb calibration_fit_tb structural_adc_tb recon_ppa_latency_tb`。

P5/P6 追加结构测试使用相同编译参数、完整 `rtl/rtl_sources.f`、`sim/tb/structural_adc_tb.sv` 和 `-GP_RECON_STAGES=5` / `6`；执行时传 `+vdir=<repo>/sim/vectors`。完整原始命令数组及返回值见 `profiles/manifest.json`。负控精确单点替换、源文件 SHA、原始命令和预期失败定位见 `negative_controls/manifest.json`；仅在临时副本施加替换，不能改工作树生产 RTL。

两份 `reproduce_original.py.txt` 按原字节保留本次执行脚本，是取证资料，不是安装后的工具。需重放这些脚本时先复制成临时 `.py` 文件，并显式调整文件开头的仓库、输出和工具路径；原有路径/环境和命令原貌均保留。macOS 5.49 使用 PCH 参数，5.020 使用外部 coroutine 头兼容目录；这些是当前宿主编译器适配，Linux 环境不应硬编码这些本地路径。具体环境和两个版本原始 `--version` 输出均已归档。

`sha256.json` 只覆盖本目录有效最终证据，不含自身；不归档 build 目录、生成 C++ 或大向量。早期尚未抽取纯函数 oracle 的草稿编译已停止，不作为 PASS 证据。最终 loader TB 用显式参数纯函数比较各字与行和，保持原刺激及计数，同时避免多份展开代码造成编译膨胀。
