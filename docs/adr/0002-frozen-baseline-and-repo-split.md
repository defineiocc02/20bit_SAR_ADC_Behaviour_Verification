# ADR 0002 — 冻结 v6.1 基线，另建仓库重构

- 状态：**已采纳**
- 关联：审计的**前置条件**（全部条目）
- 影响：仓库布局、`MANIFEST.txt`、所有后续 ADR

---

## 1. 问题

外部审计的对象是 `adi_model_release_v6.1` 这个**已发布**的包。它带一份
`MANIFEST.txt`，列出 37 个文件的 SHA-256。这带来一个两难：

- 若**就地修改** v6.1：审计报告的每一条引用（行号、数值、结论）都会失锚，
  无法再核对"这条指控是不是真的成立"；而且无法回答"修复后到底改了什么"。
- 若**只写一份补丁**：修复会散落在 review 评论里，没有一个可运行的、
  自洽的产物。

而且审计的结论不是"某几行写错了"，是**治理方式**错了：

> "each stage validated its output against its own assumptions"
> —— 每个 stage 拿自己的假设去验自己的输出。

这类问题不可能靠就地打补丁解决，必须换一套治理结构。

## 2. 决定

**冻结基线，另建仓库。**

| 仓库 | 角色 | 修改策略 |
|:---|:---|:---|
| `adi_model_release_v6.1/` | 审计对象，**只读** | 永不修改；`MANIFEST.txt` 的 37 项 SHA-256 保持有效 |
| `adi_sar_model/` | 重构产物，本仓库 | 全部工作在此进行 |

工程约束（已在本会话中执行过一次并校验）：

- 任何针对基线的写入都是**事故**。本会话中曾误改
  `adi_model_release_v6.1/src/adi_model/config.py`，随即回滚并逐文件校验
  37 项 SHA-256 全部匹配 —— 这条约束是**被执行过的**，不是纸面声明。
- 基线包的存在方式是**参照物**，不是依赖。新仓库不 import 它，
  不复用它的结果文件作为"基线数值来源"（对比时可以读，但必须注明是读的）。

## 3. 后果

1. **审计的每一条指控都可以被独立复算。** 引用 v6.1 的行为时，直接跑
   冻结的包；`tests/audit/test_audit_findings.py` 里每条测试的 docstring
   都写出该条在 v6.1 下的**实际数值**，读者可以回到基线包里验证。
2. **"修复前 / 修复后"的对照是结构化的。** 例如：

   | 指标 | v6.1（冻结基线） | 本仓库 |
   |:---|--:|--:|
   | slice 平均覆盖率 | 3.5572 / 8 | **8.0 / 8** |
   | 跨周期违规次数 | 8180 | **0** |
   | 8/18 选择对 DAC 误差的影响 | 0.0 µV | **23.64 µV** |
   | dither 码值单位 | 缺 `gran/step0` 因子 | 显式换算（F3） |

3. **基线不会被"顺手修好"。** 这是刻意的：一个会被悄悄修改的参照物，
   不是参照物。
4. **代价：双份磁盘占用与两份文档。** 接受。基线仅占数百 KB，而
   "审计证据可复算"的价值远大于此。

## 4. 未闭合的部分

- **基线包没有被纳入 CI。** 它是一份快照，不是受测代码。若将来要自动化
  "基线 vs 新版"的对照，需要额外写一个比对脚本 —— 目前"对照"是人工的、
  写在测试 docstring 与 `docs/audit_response.md` 里的。
- **`MANIFEST.txt` 的校验是手动的。** 本会话用脚本逐项核对过一次；
  没有常驻的 `make verify-baseline` 目标。

## 5. 复核方式

```bash
# 基线必须与 MANIFEST 完全一致（37 项 SHA-256，清单在 docs/MANIFEST.txt）
# 路径按本地实际冻结基线的位置调整；`$FROZEN_BASELINE` 指向 v6.1 快照根目录
cd "$FROZEN_BASELINE"
python - <<'PY'
import hashlib, pathlib
man = pathlib.Path("docs/MANIFEST.txt")
bad, n = [], 0
for line in man.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    digest, _, rel = line.partition("  ")
    n += 1
    p = pathlib.Path(rel)
    if not p.exists():
        bad.append((rel, "missing")); continue
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != digest:
        bad.append((rel, "mismatch"))
print("baseline files:", n, "| violations:", bad or "none")
PY
# -> baseline files: 37 | violations: none
```
