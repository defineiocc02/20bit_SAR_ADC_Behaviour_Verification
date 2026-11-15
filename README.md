# 20bit SAR ADC 数字 IP

依据 ISSCC 2024 Session 9.8 公开机制、按明确工程假设实现的**可综合数字 IP**。
不是 ADI 硅片的还原（公开资料不足），也不声称与该芯片逐位一致。

## 当前交付：D3/D4 数字 IP

| 文件 | 职责 |
|:---|:---|
| `src/adi_model/dem_dither.py` | 参考算法（唯一实现，纯函数） |
| `rtl/dem_mask_v3.sv` | D3：粗码 → 8×20 物理 post-mask（打包端口） |
| `rtl/dither_pre.sv` | D4：6 位扰动字 → 8 片 × 4 位采样预置开关（打包端口） |
| `rtl/dem_phase_gen.sv` | D3 三维相位发生器（xorshift32，三轴满覆盖 1920） |
| `rtl/d3d4_ip.sv` | **IP 顶层**：把上面三个接成单时钟域、单拍输出的通路 |
| `rtl/sched18.sv` | D1 random18 片组调度（ACQ/CONV/IDLE 三态） |

**关键点**：`dem_mask_v3` 与 `dither_pre` 原本是纯组合模块，相位由端口外部喂，
所以"三维随机化"在叶子层并不存在。`d3d4_ip` 把 `dem_phase_gen` 接进来之后，
随机化才第一次真正落在 RTL 里。

## 一条命令验证（三层）

```bash
bash tools/verify_d3d4_ip.sh
```

预期输出：

```
[1/4] L1 叶子级逐位对拍（133376 + 512 组）
      DEM    133376/133376 组  失配 0
      Dither 512/512 组  失配 0
      RESULT: PASS
[2/4] L2 IP 级逐位对拍（含相位发生器，512 笔）
      RESULT: PASS (512 tx bit-exact)
[2.5] sched18 随机性统计（20000 拍）
      RESULT: PASS
[3/4] L3 门级等价
      RESULT: PASS (512 tx bit-exact)
```

远端 VCS（本机无仿真器时）：

```bash
D3D4_RUNNER=vm-yian bash tools/verify_d3d4_ip.sh
```

含门级（需要先做过综合，网表路径自定）：

```bash
D3D4_RUNNER=vm-yian \
D3D4_NETLIST=/path/to/d3d4_ip_net.v \
D3D4_SCMODEL=/path/to/tcbn28hpcplusbwp7t40p140.v \
bash tools/verify_d3d4_ip.sh
```

脚本会因空日志、截断日志、覆盖不足、仿真失败、任一层未出现 `RESULT: PASS`
而返回非零。L3 未提供网表时会显式打 `SKIPPED` 并提示"本次只证明 RTL 层正确"。

## 综合

见 `docs/SYNTH_D3D4.md`。摘要（TSMC 28HPC+ SS）：

| 项 | 值 |
|:---|:---|
| 面积 | 1040.86 µm²（1516 cells，251 FF） |
| 真实组合延迟 | **7.91 ns（≈126 MHz）**，相对 40 MS/s 规格有约 3 倍余量 |
| 25 ns 下 setup | +17.29 ns |
| 25 ns 下 hold | +0.013 ns —— **不构成签核结论**，见 SYNTH_D3D4.md §4 |

## 阅读顺序

1. 本文（交付什么、怎么跑）
2. `docs/CONTRACT_CURRENT.md`（适用哪份合同、版本优先级）
3. `docs/DIGITAL_ALGORITHM.md`（DEM / Dither 原理与源码对应）
4. `docs/ACCEPTANCE_STATUS.md`（哪个模块在哪个提交通过了什么）

## 已知边界

| 项 | 说明 |
|:---|:---|
| 范围 | D3/D4 叶子 + `d3d4_ip` 顶层 + `sched18`（D1 调度器）。D2/D5/D6 不在本阶段 |
| 综合 | **已做**块级综合 + 门级等价（`docs/SYNTH_D3D4.md`）。未做 P&R / CTS / DRC / LVS |
| 形式等价 | **未做**。门级仿真覆盖功能，不等价于形式证明 |
| hold | **不能签核**。零线载假设下的 +0.013 ns 不构成结论，见 `SYNTH_D3D4.md` §4 |
| 功耗 | **不能签核**。`report_power` 无色切换率输入，只作量级参考 |
| 接主链 | **未接**。`digital_top_w` 仍走 `dem_shuffle` + `dither_split`（4+3+2 / `c−U`），不含本 IP |
| 来源 | `3+3+3`、8 宏列×16 半单元、β=1/8、guard 512–520 均为 `[E]` 工程补全 |
| 历史文档 | `docs/DIGITAL_SPEC.md` 等早于合同 v2.1，仅作追溯，不作现行规格 |
| 笔数口径 | 仓库里 `8509` 笔那条走旧链，**不是本 IP 的证据** |

## 状态表

见 `docs/ACCEPTANCE_STATUS.md`（每条绑定提交与证据路径）。
