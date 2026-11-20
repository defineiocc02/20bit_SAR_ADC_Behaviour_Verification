# 历史 D3/D4 独立 IP 验收

基线：`merge/adi-repro` @ `c88a7d9` 与当轮 D3/D4 独立 IP 交付。以下记录依各行的输入和源基线解释。[v21 工程合同](../../CONTRACT_CURRENT.md) · [现行项目状态](../../STATUS.md)

**每行尽量绑定提交。标 `—` 的行表示尚未绑定，不得据此声称 PASS。**

## D3/D4 独立 IP 验收记录

本节的命令属于历史检出。脚本原件可用 `git show 2224d30:tools/verify_d3d4_ip.sh` 查阅。

| 层 | 项 | 状态 | 证据 |
|:--:|:---|:---|:---|
| L1 | D3 DEM 物理 post-mask | **PASS** | 133,376 组（521 码 × 8 横 × 8 纵 × 2 coin × 2 half）与 `dem_dither.py` 逐位一致，0 失配 |
| L1 | D4 Split Dither pre-mask | **PASS** | 512 组（64 字 × 8 相位），mask 与 `U`/`f` 全比，0 失配 |
| L2 | D3/D4 IP 顶层（含相位发生器） | **PASS** | 512 笔事务逐位一致；`d3d4_ip` 把 `dem_phase_gen` + `dem_mask_v3` + `dither_pre` 接成单拍通路 |
| L3 | 门级等价 | **PASS** | 综合网表 + 标准单元模型，512 笔 0 失配 |
| L4 | 块级综合（28nm SS） | **PASS** | 3017 cells / 1770.08 µm²；25 ns 下 setup +19.88 ns、hold +0.00095 ns；逻辑级数 40、关键路径 2.82 ns。详见 `docs/SYNTH_D3D4.md` |
| L2 | k 范围合法性 | **PASS** | `k_range_err_o`：k=0..520 不报错、k=521..1023 全部报错（合同 §"必须报错，禁止回绕/夹紧"） |
| — | 形式等价（Formality） | **NOT_RUN** | 门级仿真已覆盖功能，不等价于形式证明 |
| — | P&R / CTS / 布线 | **NOT_RUN** | 无 ICC2/Innovus 配置与完整 LEF |
| — | 真实 hold 签核 | **不能声称** | ZeroWireload，无时钟树；13 ps 不是签核数字 |
| — | DRC / LVS / 功耗签核 | **NOT_RUN** | 无版图；`report_power` 无色切换率输入 |

**CLAIM = IMPLEMENTATION_ONLY + BLOCK_LEVEL_SYNTH**，不是 signoff。

### 随机化模块接线状态（2026-11-15）

| 模块 | 状态 | 说明 |
|:---|:---|:---|
| `dem_phase_gen` | **已接入** | 原为悬空（0 处例化、不在任何 `files*.f`）。现由 `d3d4_ip` 例化，三轴对称 1920/1920 满覆盖 |
| `sched18` | **PASS（修复后）** | 原 16 位乘法溢出导致随机性丢失（固定丢最小两片），修复后 4096 拍 0 错误、χ²=0.9、IDLE 对 153/153。已接入 `d3d4_ip` 支撑两相位交织 |
| `follower_rt` | **已接入** | 事件流模式下逐事件累加 k（实测 1→3→7→…→511，8/8 次严格增长），即论文第 7 条"边产生边载入" |
| `dem_phase_rr` | **默认启用** | round-robin，对齐 PPT "Round robin shuffler"；χ²=0、三轴 MI=0、周期内零重复；面积比 xorshift 小 5.1 倍（见 `docs/PHASE_AB.md`） |

### 与旧实现的关系（不得混淆）

- 顶层 `digital_top_w` 仍走 `dem_shuffle` + `dither_split`（4+3+2 / `c−U`），**不含 D3/D4**。
- 仓库里那条 `8509` 笔 PASS 走的是旧链，**不是本表 D3/D4 的证据**。

旧检出中的复现命令。

```bash
bash tools/verify_d3d4_ip.sh                                    # L1+L2
D3D4_RUNNER=vm-yian bash tools/verify_d3d4_ip.sh                # 远端
D3D4_NETLIST=<net.v> D3D4_SCMODEL=<std.v> bash tools/verify_d3d4_ip.sh   # 含 L3
```

## 二、其他模块（历史基线，非本阶段交付）

| # | 项 | 状态 | 证据 | 提交 |
|:--:|:---|:---|:---|:---|
| D1 | 18 片调度 | PARTIAL | `slice_pool.sv` 64 组 0 失配；仅稳态，未做启动/排空 | — |
| D2 | Follower 逐事件前缀 | PARTIAL | `follower.sv` 200 组 0 失配；入口仍由最终码反推前缀 | — |
| D3 | 旧二维 DEM（4+3+2） | 已被取代 | 见 `docs/_history/DIGITAL_SPEC.md` 历史横幅 | — |
| D4 | 旧 dither 分路（`k=c−U`） | 已被取代 | 同上 | — |
| D5 | 权重重构（Python） | PASS | K 恒等式 420 组 0 违例 | — |
| D5 | 权重重构（RTL） | PASS | `recon_d5_w2` 420/420 与生产主链逐位一致 | — |
| D6 | 顶层事务 | PASS（仿真） | 见下方笔数口径 | `30976e3` |
| D6 | 9 组倒数表 | PASS | 9 组 G 极差 233M/167M/110M | `30976e3` |
| D6 | 顶层时序 | NOT_RUN | `digital_top_w` 无综合结果 | — |

### 顶层事务笔数口径（原表混用 8509 / 7789）

| 数字 | 口径 |
|:---|:---|
| **8509** | **当前** `run_topw.sh` 实测计数（`RESULT: PASS (8509 transactions checked)`） |
| 7789 | 更早一版 TB 的记录，已被 8509 取代 |

> 更正：本表上一版把两个数字的归属写反了（误写 8509=旧版、7789=当前），
> 经复核，当前实测为 **8509**。

## 三、校正有效性

| 项 | 状态 | 证据 | 备注 |
|:---|:---|:---|:---|
| 输入恢复（真系数） | PASS | 1.7 µV max / 1.0 µV rms | — |
| 名义系数留下失配 | PASS | 9094 µV max（5261× 劣化） | — |
| 片 ID / mask 变化后寻址 | PASS | 三条事务记录，片组与 G 各不同 | — |
| dither 相容观测下消除 | PASS | 163 组含 64 字随机覆盖 | — |
| 无隐藏名义回退 | **PARTIAL** | `code20` 走 `x_q24_w`，但缺系数时仍会回退名义权重 | 待修 |
| 硬件 9 组倒数支持一般失配 | PASS | 各片行和不等下 8509 笔通过 | 见笔数口径 |
| 交换两组倒数被抓 | PASS | NEG swap: hits=132 | — |
| 漏载一组必报错 | PASS | NEG missing: hits=33（首版未抓到，已修） | — |

## 四、物理实现

| 项 | 状态 | 证据 | 备注 |
|:---|:---|:---|:---|
| `recon_d5_w2` 综合 | PASS（探索） | 25 ns slack **+0.002 ns**，面积 166,799 µm² / 4,940 cells | 非完整芯片面积 |
| 顶层综合 | NOT_RUN | — | 需先完成 9 组倒数改造 |
| 完整顶层综合 | NOT_RUN | — | 不能用 166,799 µm² 代替 |
| 布线 / 多角 STA | NOT_RUN | — | 不能声称 40 MS/s |
| 门级仿真 | NOT_RUN | — | — |
| Formality | FAIL | 19 分钟未收敛，已停 | — |
| 布局规划 | NOT_RUN | 五区方案已定，未出图 | — |

## 五、明确缺口（不得声称）

1. **40 MS/s 未达成** —— 未布线，STA 只到综合后单角无 OCV
2. **D3/D4 未综合、未接顶层**
3. 顶层时序未知
4. 无门级仿真、无等价证明
5. 系数为寄存器阵列（≈16k flop），非 SRAM
6. D3 只验 mask 与覆盖，**未验频域 spur / 噪声代价**
7. `DIGITAL_SPEC.md` 的 4+3+2 与 `k=c−U` **已作废**，勿引用
