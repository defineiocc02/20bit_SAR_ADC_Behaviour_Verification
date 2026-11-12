# P3/P4 交付索引（2026-11-13）

> 分支 `merge/adi-repro`，基线 `main bc9a16a`。本轮只做 **P3 机制补全 + P4 消融对标**，
> 未合并 `main`，按用户指示推送分支供审阅。
> 证据等级：**L1 行为级（浮点）+ L2 数字 RTL 编译**；无后仿、无 PEX、无硅测。

---

## 1. 本轮新增的文件

| 类型 | 文件 | 作用 |
|:---|:---|:---|
| 模型 | `src/adi_model/control/follower.py` | RDAC follower 逐事件语义、因果审计、参考开关代价 |
| 模型 | `src/adi_model/control/dem3d.py` | 三维 DEM 两套掩码绑定 + 守恒/均匀性审计 + 轮转计划 |
| 模型 | `src/adi_model/control/dither_port.py` | dither 独立电荷口、码偏移残余、范围预算 |
| 模型 | `src/adi_model/control/track_aux.py` | TRACK 预置与 AUX 的输入电荷记账、因果审计 |
| 模型 | `src/adi_model/calibration/mask_calibration.py` | 逐码输出校准（可用）+ 逐单元权值拟合（负结果） |
| 测试 | `tests/unit/test_{follower,dem3d,dither_port,track_aux,mask_calibration}.py` | 46 项，全部通过 |
| 工具 | `tools/run_p3_mechanisms.py` | 生成 `data/p3_mechanisms.json` |
| 工具 | `tools/run_p4_ablation.py` | 生成 `data/p4_ablation.{json,csv}` |
| 文档 | `docs/reviews/20261113_p3p4/0*.md` | 六份，逐条给证据与"不能声称" |

## 2. 六份文档读什么

| 文档 | 一句话 |
|:---|:---|
| [01_rdac_follower](01_rdac_follower.md) | follower 不省静态电荷，省的是传统 SAR 的 **2.99×** 试探电荷；因果门禁用前缀截断不变性，能抓住偷看最终码的实现 |
| [02_dem3d](02_dem3d.md) | 三维 DEM 拆成两套具名掩码；名义守恒与使用均匀性是**两个**审计；横向必须按物理 ID 旋转 |
| [03_dither](03_dither.md) | 非法早期码 6 368 → 0（改用独立电荷口）；Fig.11 码偏移残余 **6.70 mV = 1170 LSB20**；输入下界缺口 **35.16 mV = 6144 LSB20** |
| [04_track_aux](04_track_aux.md) | TRACK（回看 2 笔，因果）使输入电荷降 **3.20×**；"350 µA 反解频率"已撤回——论文横轴是直流差分输入 |
| [05_p4_ablation](05_p4_ablation.md) | 6 条实跑 + 2 条 NOT_RUN（32 次重复）；热噪声 **8.60 nV/√Hz vs 论文 9.3**（0.92×）；DEM 把 INL 打到 **6.2 LSB20（3.9×）**、代价 NSD +29%；校准 SFDR +10.5 dB、INL 10.5 |
| [07_calibration](07_calibration.md) | 逐单元权值在残差观测下**不可辨识**（残差 14 V、权重 −43 V）；可用的是逐码修正（2.31× INL、+10.5 dB SFDR，SNR/DR 不动） |
| [08_mismatch_budget](08_mismatch_budget.md) | 失配 σ=0.1%（假设值）；INL = 24 376×σ ppm（线性）；论文 2.2 ppm ↔ σ_eff 0.009%；校准有 ~5 ppm 地板，σ≤1e-4 时反而变差 |
| [09_audit_response](09_audit_response.md) | 外部审查 A01–A07 逐条处置：**撤回** TRACK 6.36×（非因果）、"350 µA ↔ 0.907 MHz"、"使用极差全 0"，以及"8 次重复的 INL"（测量噪声） |
| [figures/](figures/README.md) | **12/12 张图**（F01–F12）+ 逐图来源（配置/seed/SHA/重绘命令与"不能声称"） |
| [10_dynamic_closure](10_dynamic_closure.md) | A7 全动态跑通：参数表全部 ASSUMED，`tau_ref` 由论文 65%/20b 反解；动态路径尚无数字重构（out=NaN） |
| [06_paper_anchor_40nm](06_paper_anchor_40nm.md) | 40 nm / 3.3 V 器件 / 6 Vpp / 20.5 pF / 1.8 mA 的逐条出处；模型只有差分等效电压坐标，不能做器件耐压检查 |

## 3. 四条硬结论（与上一轮相比的变化）

1. **"follower 比直接加载费电"是错的**：两者最终电荷逐位相同（`mismatch = 0.0 C`），比值 1.000。
   真正被消除的是试探-撤销电荷（比值 2.99）。
2. **"dither 只能靠码偏移实现"是错的**：码偏移在 9 位满码域会造出 6368 个非法事件；
   独立电荷口没有这个约束，把"上段残余"变成一个可测的量（6.70 mV）。
3. **DEM 的收益不是单调的**：横向+纵向把 INL 打到 0.57×，噪声涨 21%；补片选维度才把噪声收回 19%。
4. **DEM 与校准各修一半，且不能互相替代**：32 次重复下 DEM 把 INL 打到 6.2 LSB20（3.9×）、
   代价 NSD +29%；逐码校准 SFDR +10.5 dB、NSD 不动，但 INL 只 2.3×（10.5）。
   8 次重复时的"14.5/11.0"是测量噪声（地板 5.3 LSB20），已作废。

## 4. 判定（五条分开，不合并）

| 判定 | 结论 |
|:---|:---|
| 工程整合 | **PASS**（46 项新测试全绿，无反向依赖） |
| 数字机制复现 | **PARTIAL**（五机制有模型+门禁；dither 未进主循环、AUX 未接、逐单元权值不可辨识；A01/A03/A07 已按审查修正） |
| 系统行为复现 | **PARTIAL**（仅 L1） |
| 论文性能对标 | **PARTIAL**（噪声对上 0.92×；INL 校准后仍差 4.8×） |
| RTL/物理验证 | **NOT CLAIMED**（本轮无新增 RTL 证据） |

## 5. 复现命令

```bash
# 机制证据（约 15 s）
PYTHONPATH=src python3 tools/run_p3_mechanisms.py --samples 4096 \
  --out docs/reviews/20261113_p3p4/data

# P4 矩阵（约 60 s）
PYTHONPATH=src python3 tools/run_p4_ablation.py \
  --dynamic-n 8192 --static-points 256 --static-repeats 8 \
  --out docs/reviews/20261113_p3p4/data

# 新增单元测试
python3 -m pytest tests/unit/test_follower.py tests/unit/test_dem3d.py \
  tests/unit/test_dither_port.py tests/unit/test_track_aux.py -q
```

## 6. 不能声称（整轮汇总）

* 不能声称 ADI 论文性能已复现：INL 6.2 LSB20（DEM）vs 论文 2.3 LSB20。
* 不能声称任何 RTL/后仿结论：本轮没有新增 RTL 证据。
* 不能声称 40 nm 器件条件被建模：只有差分等效电压坐标。
* 不能声称 dither 已改善 DNL：本轮只给出范围/残余/解扰，未接主循环。
* 不能声称"已实现论文的 DAC 权值校正"：做的是逐码输出修正，不是逐单元权值辨识。
