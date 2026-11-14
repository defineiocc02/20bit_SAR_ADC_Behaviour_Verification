# 20bit SAR ADC 数字 IP

依据 ISSCC 2024 Session 9.8 公开机制、按明确工程假设实现的**可综合数字 IP**。
不是 ADI 硅片的还原（公开资料不足），也不声称与该芯片逐位一致。

## 当前交付：D3 DEM 与 D4 Split Dither

| 文件 | 职责 |
|:---|:---|
| `src/adi_model/dem_dither.py` | 参考算法（唯一实现，纯函数） |
| `rtl/dem_mask_v3.sv` | D3：粗码 → 8×20 物理 post-mask |
| `rtl/dither_pre.sv` | D4：6 位扰动字 → 8 片 × 4 位采样预置开关 |
| `tb/tb_dem_dither.sv` | 遍历输入，打印 RTL 输出 |
| `tools/cmp_dem_dither.py` | 与参考算法逐位比对（严格：缺一条即 FAIL） |

## 一条命令验证

```bash
bash tools/verify_d3d4.sh
```

预期输出：

```
DEM    133376/133376 组  失配 0
Dither 512/512 组  失配 0
RESULT: PASS
```

远端 VCS：

```bash
D3D4_WORKDIR=/root/pg D3D4_RUNNER="ssh your-host" bash tools/verify_d3d4.sh
```

脚本会因空日志、截断日志、覆盖不足、仿真失败而返回非零。

## 阅读顺序

1. 本文（交付什么、怎么跑）
2. `docs/CONTRACT_CURRENT.md`（适用哪份合同、版本优先级）
3. `docs/DIGITAL_ALGORITHM.md`（DEM / Dither 原理与源码对应）
4. `docs/ACCEPTANCE_STATUS.md`（哪个模块在哪个提交通过了什么）

## 已知边界

| 项 | 说明 |
|:---|:---|
| 范围 | 只有 D3/D4；D1/D2/D5/D6 不在本阶段 |
| 综合 | 未做 28nm 综合与 STA |
| 集成 | 未接顶层；活动 slice ID 与相位由外部提供 |
| 来源 | `3+3+3`、8 宏列×16 半单元、β=1/8、guard 512–520 均为 `[E]` 工程补全 |
| 历史文档 | `docs/DIGITAL_SPEC.md` 等早于合同 v2.1，仅作追溯，不作现行规格 |

## 状态表

见 `docs/ACCEPTANCE_STATUS.md`（每条绑定提交与证据路径）。
