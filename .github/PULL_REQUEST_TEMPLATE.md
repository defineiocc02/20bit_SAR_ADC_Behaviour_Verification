# Pull Request

## 1. 改动类型

- [ ] 缺陷修复（对应 issue #____）
- [ ] 新增物理机制 / 新增 stage
- [ ] 参数或口径调整
- [ ] 文档 / 注释
- [ ] 工具链 / CI
- [ ] 重构（**必须声明：不改变任何数值**）

## 2. 来源分级影响

本仓库的每个参数都带来源分级（[披露]/[拟合]/[假设]/[派生]/[研究扩展]）。
本次改动是否触碰了它？

- [ ] 未触碰
- [ ] 新增/修改了 `PARAM_GRADES` 或 `provenance` 的标注 —— 请在下方说明
- [ ] 新增了**研究扩展**（非 ADI 披露特性）—— 必须同步更新 `NOTICE`
      与 `docs/adr/0007-provenance-as-a-runtime-value.md`

## 3. 数值影响（必答）

改动是否改变 `tools/results/results.json`？

- [ ] 不改变 —— 已用 `cmp` 验证逐字节相同
- [ ] 预期改变 —— 请在下方说明哪些键变了、为什么、新旧值各是多少

```bash
cp tools/results/results.json /tmp/before.json
python tools/run_all.py
cmp /tmp/before.json tools/results/results.json
```

## 4. 门禁自检

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest -q`（当前基线：134 passed）
- [ ] 若为新增物理机制：补了一条**对抗性**回归测试（能抓住"回退到旧口径"）
- [ ] 若新增了"勿回退"教训：写进了 ADR 或 `docs/audit_response.md`

## 5. 描述

<!-- 做了什么、为什么这么做、 reviewers 应该重点看哪里。 -->

## 6. 适用域边界

<!-- 本结论在什么条件下成立？什么条件下不成立？（本仓库要求所有结论有界。） -->
