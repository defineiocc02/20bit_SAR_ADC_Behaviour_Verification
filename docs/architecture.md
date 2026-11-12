# 架构（W8 候选）

> 目标：一个可安装主包 + 隔离的 CAAZ 扩展 + 可复核的验证/证据层。
> 当前状态：W1–W3 已落地并通过测试；W4–W7 为合同/内核/编译级，主链集成仍未闭合。

## 1. 分层

```
src/adi_model/
├── contracts/        数据结构与拒绝规则（TopologySpec/EventRecord/SampleContext/
│                     SwitchCommand/FrozenCalibration/Profile/SwitchState/MapperTable）
├── control/          调度、DEM（Fig.11/Fig.21）、dither 字、码本
├── behavioral/       普通逐笔 RA AZ（ra_az.py）
├── codes.py          码宽/码-电压换算
├── capacitors.py     任务几何与电荷（17+5、1 pF）
├── noise.py          kT/C、白噪、1/f、前端噪声
├── reference.py      参考建立/预充/RA 非线性
├── reference_mux.py  两轨参考切换
├── timing_jitter.py  抖动/极点/带宽
├── transients.py     连续激励/瞬态
├── impairments.py    故障注入集
├── metrics_lsb20.py  LSB20 逐码指标
└── mechanisms.py     上述内核的统一入口（默认全关）
extensions/caaz/      CAAZ 模型、RTL、tb、测试、说明
rtl/                  主数字 RTL（无 CAAZ）
```

## 2. 依赖方向

```
contracts  ←  control / behavioral / kernels  ←  simulation  ←  cli / experiments
extensions/caaz  →  只依赖 contracts + 主包稳定 API；主包不反向依赖扩展
```

主包不 import `task*`、不 import `extensions`；`tools/check_caaz_isolation.py` 与
`tests/unit/test_caaz_isolation.py` 守住 CAAZ 边界。

## 3. 三个 profile

| profile | 用途 | 关键点 |
|:---|:---|:---|
| `adi_isscc2024_40m_6vpp` | ADI 主复现 | 40 MS/s、±3 V、18/8 片池、**17/5 阵列**、19.88 pF（2026-11-13 起；原 20.5 pF / 63+8 已废弃）|
| `engineering_10m_10vpp` | 工程变体 | 10 MS/s、±5 V、17+5、1 pF |
| `patent_fig19_7slice` | dither 机制例 | 7 片、6-bit 字 |

`load_profile()` 未知名字报错；`assert_caaz_free()` 保证主 profile 不带 CAAZ。

## 4. 已接入 vs 可用但未接入

| 能力 | 状态 |
|:---|:---|
| main `DefaultModel.simulate`（40 MS/s 名义） | 已接入，CLI 可跑 |
| 普通逐笔 AZ（`behavioral/ra_az.py`） | 已提供，未强制接入主 simulate |
| 调度/DEM/dither/码本（`control/`） | 已迁入并通过 41 项测试；未替换 main 旧调度 |
| 噪声/参考/时偏/瞬态内核 | 已迁入并通过 78 项测试；未接主链 |
| CAAZ | 完全隔离在 `extensions/caaz/` |
| RTL 主顶层 | 已去 CAAZ，VCS 编译 0 error；未跑对拍 |

> 因此当前只能说"内核与合同可用"，不能说"ADI 全机制已复现"。
