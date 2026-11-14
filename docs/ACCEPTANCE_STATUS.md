# 验收状态

基线：`merge/adi-repro` @ `612df75`。
权威链：`README.md` → `docs/CONTRACT_CURRENT.md` → 本表。
冲突时以本表为准；`ACCEPTANCE_CHECKLIST.md`、`DIGITAL_PLAN.md`、`DIGITAL_SPEC.md`
为历史过程记录，不作现行状态。

**每行尽量绑定提交。标 `—` 的行表示尚未绑定，不得据此声称 PASS。**

## 一、当前交付：D3 / D4（RTL）

| 项 | 状态 | 证据 | 提交 |
|:---|:---|:---|:---|
| D3 DEM 物理 post-mask | **PASS** | 133,376 组（521 码 × 8 横 × 8 纵 × 2 coin × 2 half）与参考逐位一致，0 失配 | `612df75` |
| D4 Split Dither pre-mask | **PASS** | 512 组（64 字 × 8 相位），mask 与 `U`/`f` 全比，0 失配 | `612df75` |
| 验收入口 | **PASS** | `tools/verify_d3d4.sh`；空日志/截断/覆盖不足均非零退出 | `612df75` |
| D3/D4 综合 | **NOT_RUN** | — | — |
| D3/D4 顶层集成 | **NOT_RUN** | 活动 slice ID 与相位由外部提供 | — |

复现：

```bash
make verify-d3d4          # 或 bash tools/verify_d3d4.sh
D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh <host>" bash tools/verify_d3d4.sh
```

## 二、其他模块（历史基线，非本阶段交付）

| # | 项 | 状态 | 证据 | 提交 |
|:--:|:---|:---|:---|:---|
| D1 | 18 片调度 | PARTIAL | `slice_pool.sv` 64 组 0 失配；仅稳态，未做启动/排空 | — |
| D2 | Follower 逐事件前缀 | PARTIAL | `follower.sv` 200 组 0 失配；入口仍由最终码反推前缀 | — |
| D3 | 旧二维 DEM（4+3+2） | 已被取代 | 见 `docs/DIGITAL_SPEC.md` 历史横幅 | — |
| D4 | 旧 dither 分路（`k=c−U`） | 已被取代 | 同上 | — |
| D5 | 权重重构（Python） | PASS | K 恒等式 420 组 0 违例 | — |
| D5 | 权重重构（RTL） | PASS | `recon_d5_w2` 420/420 与生产主链逐位一致 | — |
| D6 | 顶层事务 | PASS（仿真） | 见下方笔数口径 | `30976e3` |
| D6 | 9 组倒数表 | PASS | 9 组 G 极差 233M/167M/110M | `30976e3` |
| D6 | 顶层时序 | NOT_RUN | `digital_top_w` 无综合结果 | — |

### 顶层事务笔数口径（原表混用 8509 / 7789）

| 数字 | 口径 |
|:---|:---|
| **8509** | 旧版 `tb_digital_top_w.sv` 的 retired 计数（9 组倒数改造前） |
| **7789** | 当前 `run_topw.sh` 实测 `RESULT: PASS (7789 transactions checked)` |

两者不是同一个 TB 版本，不得互相替代。

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
