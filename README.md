# ADI 20-bit SAR ADC · 片上数字模块

复现 ISSCC 2024 §9.8 的**片上数字**部分：18 片调度、Follower、二维 DEM、dither 分路、
片外系数 + 片上权重重构、20 位输出。模拟电路（RA / 参考 / 采样）不在范围内，只作激励。

**一句话状态**：数字链在 Python 侧完整、在 RTL 侧有一条可验证的权重通路（420/420 与生产
主链逐位一致、顶层记分板 PASS），但**未布线、未做门级仿真、硬件单倒数不支持一般失配**。

---

## 当前数字专项的两个入口

| 角色 | 文件 | 说明 |
|:---|:---|:---|
| **算法基准（唯一）** | `src/adi_model/digital_chain.py` | `run_transaction()` 是 D1–D6 的生产主链，也是 RTL 对拍的金标 |
| **RTL 顶层** | `rtl/digital_top_w.sv` | 权重通路顶层；编译清单 `files_topw.f`，验证命令 `./run_topw.sh` |

> `rtl/core/*` + `rtl/top/sar20_digital_core.sv` + `files.f` 属于**另一条历史实现**，
> 与当前专项不是同一条证据链，比对时不要交叉引用。

## 数据流（数字域）

```
c (9b) ──┬─ dither_split ── U ──► k = c − U
         │
adc2(12b)│        ┌── cdac_decode ── main[16:0], sub[3:0]
dither(6b)        │
         └────────┴── dem_shuffle ── h[15:0], y[7:0]  ──► 物理片 ID + 单元 mask
                                                              │
                                                    查冻结系数 w[sid][unit]
                                                              │
                                            Wm = Σw·m,  G = Σw·a（仅 16 采样列）
                                                              │
                                    D_eff = ⌊(6Wm − 3G)·I / 2^36⌋,  I = round(2^60/G)
                                                              │
                                    x̂ = D_eff + r̂ + TΔ  ──► ÷96 + 饱和 ──► code20
```

实测延迟：**4 拍**（accept → valid_o），7789 笔全部落在 4，非按模块名假定。

## 模块真实状态

| 模块 | Python | RTL | 证据 |
|:---|:---:|:---:|:---|
| D1 18 片调度 | ✅ | ✅ `slice_pool.sv` | 64 组 0 失配 |
| D2 Follower 逐事件 | ✅ | ✅ `follower.sv` | 200 组 0 失配 |
| D3 二维 DEM | ✅ | ✅ `cdac_decode` + `dem_shuffle` | 512 / 8192 组 0 失配；`k=4` 每片覆盖 16/16 |
| D4 dither 分路 | ✅ | ✅ `dither_split.sv` | 64 字；64 个扰动字两侧位权一致 |
| D5 权重重构 | ✅ | ✅ `recon_d5_w2.sv` | **420/420 与生产主链逐位一致** |
| D6 顶层事务 | ✅ | ⚠️ `digital_top_w.sv` | 记分板 PASS；未综合 |
| 校正有效性 | ✅ | — | 真系数 **1.7 µV** vs 名义 **9094 µV**（5261×） |

## 怎么跑

**Python 主链 / 校正效果**（无需 EDA）：

```bash
PYTHONPATH=src python3 tools/cal_effectiveness.py          # 校正前后误差
PYTHONPATH=src:. python3 -m pytest tests/unit -q           # 单元测试
```

**RTL 顶层**（需要 VCS 或 iverilog）：

```bash
export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1
export SNPSLMD_LICENSE_FILE=27080@localhost
./run_topw.sh          # 编译 files_topw.f + 跑 tb/tb_digital_top_w.sv 记分板
```

期望输出：`RESULT: PASS (7789 transactions checked)`，延迟直方图 4 拍。

## 源码入口速查

| 想找什么 | 去哪 |
|:---|:---|
| 规格（冻结） | `docs/DIGITAL_SPEC.md` |
| 论文/专利对照（含原图） | `docs/DIGITAL_ALGORITHM.md` |
| 验收状态（唯一） | `docs/ACCEPTANCE_STATUS.md` |
| 验证结果汇总 | `docs/VCS_RESULT.md`、`docs/TOP_INTEGRATION.md` |
| 校正效果 | `docs/CALIBRATION_RESULT.md` |
| 时序与面积 | `docs/TIMING_W2.md`、`synth/results/w2/` |
| 失败与更正记录 | `docs/D5_PROD_VS_RTL.md`、`docs/OPT_ATTEMPT_20261113.md` |

## 明确缺口（按对结论的影响排序）

| # | 缺口 | 影响 |
|:--:|:---|:---|
| 1 | **未布线**，STA 只到综合后单角、无 OCV | 不能声称 40 MS/s |
| 2 | **硬件单 `inv_G`**，9 个片组 G 差 0.344%（≈3600 LSB20） | 不支持一般失配；需按片组索引 9 个倒数 |
| 3 | 顶层未综合 | `digital_top_w` 的时序未知 |
| 4 | 无门级仿真 / Formality 未收敛 | 综合未改行为未被证明 |
| 5 | 系数是寄存器阵列（≈16k flop）非 SRAM | 面积会显著高于 166,799 µm² |
| 6 | 启动/排空调度、DEM 频域质量 | 只做稳态与覆盖，未做 spur |

## 探索性结论（**不可**作为签核依据）

- `recon_d5_w2`：25 ns 约束下 slack **+0.002 ns（报告显示 0.00 MET）**、hold +0.42；
  映射单元面积 **166,799.00 µm² / 4,940 cells**（非完整芯片面积）。
- 名义路径 `recon_d5_pipe`：reg→out slack +0.04 ns，**输入端时序未验证**。

## 目录

```
src/adi_model/       Python 主链（digital_chain.py 为唯一基准）
rtl/                 当前扁平模块（digital_top_w.sv 为顶层）
rtl/core, rtl/top/   历史实现（files.f），与当前专项不混用
tb/                  测试平台与对拍向量（vec_*）
tools/               生成器、校正效果、审计脚本（trace_one_sample.py 为旧走查，非金标）
docs/                SPEC / 论文对照 / 验收 / 各类结果与更正记录
synth/results/       精选综合与时序原始报告
validation_summary.json, mechanism_inventory.json   历史快照，非当前状态
```
