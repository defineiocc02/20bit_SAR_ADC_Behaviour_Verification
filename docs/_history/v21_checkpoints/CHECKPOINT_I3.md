# I3 检查点：13 位 ADC2 捕获与返回

时间：2026-11-15　方案：v1.4 §4.1/§4.3、§9.2、§11.2 I3、§11.3
**状态：PASS（13 项检查，0 错误）**

## 当前 SHA

- I1+I2 交付：`edc71f5`
- 本检查点：见本文件所在 commit

## 实际补了什么

| 文件 | 内容 |
|:---|:---|
| `rtl/v21/adc2_capture.sv` | 捕获归属、arm、hold 回报、整字返回、迟到隔离 |
| `rtl/v21/adc2_node_macro.sv` | 独立电荷节点行为宏：post/pre → y → 13 位 raw |
| `rtl/v21/adc_digital_ip_v21.sv` | 加 `sched18` 内部例化 + ADC2 通路 + final_post 锁存 |
| `tb/v21/tb_i3_adc2.sv` | A/B 两笔 raw、越窗、迟到、复位 |

## 名义 raw 精确命中（核心验收）

| 笔 | x | word | v_pre | k | 期望 raw | 实测 | y |
|:---|:---|--:|--:|--:|--:|:---|:---|
| A | 3−2048·LSB20/8 = 2.998535156 | 63 | 0 | 519 | **5120** | **5120** | 0.281250 V |
| B | 0 | 17 | 7 | 258 | **2560** | **2560** | 0.046875 V |

与方案 §7 行400、§11.3 的表逐值一致。

## 五个真实缺陷（都已修，全部写进注释）

### 1. `Wm` 多算了 j=19（平衡/guard 位）

方案说 A 在该 k 下"每片 post 均为 `0x7FFFF`"，且 y 必须是 0.28125 V。
- 把 j=19（子阵列平衡 1Cu）也算进 Wm：每片 4.0625 → 8 片 **32.5** → y = **−0.09375**（错）
- 排除 j=19：每片 4.0546875 → 8 片 **32.4375** → y = **0.28125**（对）

合同 §3.2 把 j=19 定义为"子阵列平衡/guard"，只为差分平衡存在，
不贡献信号电荷。B 笔排除后同样精确命中 2560。

### 2. 定点格式 Q24 装不下 ±3 V

`x_q24` 声明 24 位，但 Q24 表示 ±3 V 需 24+2+1 = **27 位**。
x=2.9985 的 Q24 值 50307072 有 26 位，被截断后符号位变 1 →
宏算出 x = **−3 V**、y = −191.7 V、raw = 0。

改 **Q22**：±3 V 需 25 位，用 26 位有符号。分辨率 0.042 LSB20，
远优于 ADC2 折算的 0.5 LSB20。

### 3. `y_wide[63:0]` / `y_wide[39:0]` 位选丢符号

位选结果在 SystemVerilog 里是**无符号**的。负数 y 被当成巨大正数，
`in_range` 永远为假 → raw 恒 0、range_err 恒 1。
必须显式 `$signed(...)`。

### 4. `G_q30_w` 未声明，被推断成 1 位 wire

`assign G_q30_w = G_q30_i;` 只传了 bit0（=0），节点宏拿到 G=0，
`term_gx` 恒 0，y 退化成纯偏置项。VCS 只报 warning，极易漏。

### 5. `final_post_q` 锁存时机错

用 `final_applied_w`（sar_evt_v21 的**寄存器**输出，比 apply 晚整拍）锁存，
那一拍 k 已不是本笔的 final k，锁到全 0。
必须用 `apply_fire_w & last_evt_w`（组合）在**应用那一拍**锁。

方案 §8.2 原文："final apply 置 final_applied，并保存 D5 的 final_post；
禁止 D5 从后来相位重算。"

## 验收覆盖（13 项）

| 项 | 结果 |
|:---|:---|
| A 的 node raw = 5120 | OK |
| A 的 y = 0.281250 V | OK |
| A 返回 valid / tag=A1 / raw=5120 | OK |
| 迟到返回被丢弃 + late_err | OK |
| 越窗报 range（x=−3 V） | OK |
| B 的 node raw = 2560 | OK |
| B 的 final_k = 258 | OK |
| B 返回 tag = B2 | OK |

## 窗口的物理意义（顺带验证了 §7 的推导）

y = 32·x − 95.671875，要求 −0.1875 ≤ y < 0.5625
⇒ x 必须落在 **[2.983887, 3.007324)**，宽度仅 23.4 mV。

这印证了合同 §8.1：ADC2 只覆盖一级量化余差经 32 倍放大后的那一段，
不是全域输入。**测试里不能想当然用 x=3.0 去"越窗"** —— 它仍在窗内
（y=0.328 V），必须用 x=−3 V 才会确定性越窗。

## 综合（28HPC+，25 ns）

```
CHK total=965 clocked=965
CHK unclocked=0
```

`compile -map_effort medium`：**34714 cells / 28372.96 µm²**。

**注意**：`compile_ultra` 在 `adc2_node_macro` 上**超时未完成**（>20 min 被 kill）。
原因是 96 位除法（`num / 96'sd3`）�� 8×19 权重表读取的组合爆炸。
这是真实的 PPA 问题，方案 §9.1 已预留处理路径：

> "可先用可综合除法保证功能，后续等价替换为倒数迭代，不在此轮用 PPA 优化拖延正确性。"

所以本轮**如实记录**：功能已通过，PPA 未闭合 → 列入 I4/I5。

## 可复跑命令

```bash
ssh vm-yian 'cd /tmp/ip && export SNPSLMD_LICENSE_FILE=27080@localhost \
  PATH=/opt/synopsys/vcs/W-2024.09-SP1/bin:$PATH VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1 \
  && vcs -full64 -sverilog -timescale=1ns/1ps \
     dem_phase_rr.sv dem_phase_gen.sv dither_pre.sv dem_mask_v3.sv sched18.sv follower_rt.sv \
     rtl/v21/acq_conv_ctx.sv rtl/v21/sar_evt_v21.sv rtl/v21/adc2_capture.sv \
     rtl/v21/adc2_node_macro.sv rtl/v21/adc_digital_ip_v21.sv tb_i3_adc2.sv -o s3 && ./s3'
```

期望：`RESULT: PASS (13 checks)`

## 剩余问题

| # | 问题 | 去向 |
|:--:|:---|:---|
| 1 | **`compile_ultra` 超时**（96 位除法） | I4：换倒数迭代 |
| 2 | 面积 28373 µm² 偏大 | I4/I5 |
| 3 | 权重表仍由 TB 直接赋值，未做配置接口 | I4（§9.4 配置/epoch） |
| 4 | 无 20 位输出 | I4 |
| 5 | 波形仍未 dump | I5 |
| 6 | hold/quiet 仍由 TB 桩 | 真实宏绑定属 §12 |

## 恢复时第一条命令

```bash
cd /Users/zhaoyi/src/20bit_SAR_ADC_Behaviour_Verification && git log --oneline -2 && git status --short
```
