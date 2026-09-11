"""硬性验收记录的判定规则 —— 把"报告里写着 FAIL"接到"进程退出码"上。

为什么单独一个模块
------------------
`tools/run_all.py` 逐条打印 PASS/FAIL 并写进 `results.json`，但外部复核
（2026-09-11）指出：脚本跑完就返回 0，于是 **CI 的 sweep 步骤只看退出状态，
数值判据 FAIL 并不会让 CI 变红**。一份写着 FAIL 的报告和一次绿色的 CI 是同
时存在的，而这正是"重复运行得到完全相同的 JSON 只证明确定性、不证明正确性"
那句批评的落点。

规则之所以不写成"递归扫描所有布尔值"就退出非零，是因为配置里的 `False`
与设计上**就该失败**的反例实验（例如"未校准 vs 校准"的对照行）都是合法的
`False`，把它们算成验收失败会把门禁变成噪声。因此判定范围是显式列举的：

* `results[name]` 是 dict 且含 ``"PASS"`` 键 → 一条验收记录；
* `results[name]` 的键名以 ``"_summary"`` 结尾 → 其每个 value 是一条记录。

这两类恰好就是 `run_all.py` 上方逐条打印出来的那些，与报告一一对应。

独立成模块的直接理由是**可测性**：`run_all.py` 是脚本，import 它会执行全部
实验；规则放在这里，`tests/audit/test_review_contracts.py` 才能用一个合成
字典验证它（含"反例实验的 False 不计入"这条边界）。

单位：无（纯结构判定）。
来源分级：不适用 —— 本模块不产生物理量。
"""

from __future__ import annotations

from typing import Any

__all__ = ["hard_failures"]

_SUMMARY_SUFFIX = "_summary"


def hard_failures(results: dict[str, Any]) -> list[str]:
    """列出被判定为硬性验收、且未通过的记录名。

    Args:
        results: ``tools/run_all.py`` 构建的 ``R`` 映射。允许任意嵌套，但只
            检查上述两种**顶层**结构；嵌套子字典（如 ``results["s10"]`` 内部
            的 ``["noise_vs_pool"]["PASS"]``）由对应的 ``*_summary`` 记录代表，
            不重复计入。

    Returns:
        list[str]: 未通过的记录名，形如 ``"pipeline.PASS"`` 或
        ``"s12_summary.电荷口径闭合：…"``，已排序。空列表表示全部通过。

    Examples:
        >>> hard_failures({"pipeline": {"PASS": True}, "s12_summary": {"a": True}})
        []
        >>> hard_failures({"pipeline": {"PASS": False}})
        ['pipeline.PASS']
        >>> hard_failures({"s12_summary": {"x": True, "y": False}})
        ['s12_summary.y']
        >>> hard_failures({"反例对照": {"loss_db": -6.0}})   # 无 PASS 键 -> 非验收记录
        []
    """
    bad: list[str] = []
    for name, val in results.items():
        if not isinstance(val, dict):
            continue
        if "PASS" in val:
            if not val["PASS"]:
                bad.append(f"{name}.PASS")
        elif name.endswith(_SUMMARY_SUFFIX):
            bad += [f"{name}.{k}" for k, ok in val.items() if not ok]
    return sorted(bad)
