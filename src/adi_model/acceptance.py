"""硬性验收记录的判定规则 —— 把"报告里写着 FAIL"接到"进程退出码"上。

为什么单独一个模块
------------------
`tools/run_all.py` 逐条打印 PASS/FAIL 并写进 `results.json`，但外部复核
（2026-09-11）指出：脚本跑完就返回 0，于是 **CI 的 sweep 步骤只看退出状态，
数值判据 FAIL 并不会让 CI 变红**。一份写着 FAIL 的报告和一次绿色的 CI 是同
时存在的，而这正是"重复运行得到完全相同的 JSON 只证明确定性、不证明正确性"
那句批评的落点。

第一版修复只覆盖了两类顶层结构，第二份复核（2026-09-11，针对 v7.0.1）
指出它**漏掉了配置自检**：`run_all.py` 还写出
``results["validate"][<检查名>]["PASS"]`` 与
``results["validate_ktc"][<检查名>]["PASS"]``，这两张表此前完全不进门禁 ——
用与真实结构相同的反例验证过：``hard_failures({"validate": {"x": {"PASS": False}}})``
返回 ``[]``。也就是说"配置不合格时流程一定被阻止"这项保证并不存在。
本版把这两张表显式纳入（见 :data:`ACCEPTANCE_CONTAINERS`）。

规则之所以不写成"递归扫描所有布尔值"就退出非零，是因为配置里的 `False`
与设计上**就该失败**的反例实验（例如"未校准 vs 校准"的对照行）都是合法的
`False`，把它们算成验收失败会把门禁变成噪声。因此判定范围是显式列举的：

* ``results[name]`` 是 dict 且含 ``"PASS"`` 键 → 一条验收记录；
* ``results[name]`` 的键名以 ``"_summary"`` 结尾 → 其每个 value 是一条记录；
* ``results[name]`` 的键名在 :data:`ACCEPTANCE_CONTAINERS` 中 → 其每个
  ``value["PASS"]`` 是一条记录（配置自检）。

独立成模块的直接理由是**可测性**：`run_all.py` 是脚本，import 它会执行全部
实验；规则放在这里，`tests/audit/test_review_contracts.py` 才能用合成
字典验证它（含"反例实验的 False 不计入"与"配置自检的 False 计入"两条边界）。

已知限制账本
------------
纳入配置自检后立刻暴露出一条**当前确实为 FAIL** 的记录（见
:data:`KNOWN_LIMITS`）。"把 FAIL 计入验收"与"发布一个红色的 CI"不能同时
成立，除非把该条明确登记为已知限制。账本因此带两道守卫，语义等价于测试里的
``xfail(strict=True)``：

* 账本里的名字**必须存在**于结果中（否则是过期条目）；
* 账本里的名字**必须当前正在失败**（一旦修好，条目变成过期，门禁报错，
  逼人摘掉它）。

豁免项会被逐条打印，不出现在"通过"的数量里 —— 不会被静默吞掉。

单位：无（纯结构判定）。
来源分级：不适用 —— 本模块不产生物理量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ACCEPTANCE_CONTAINERS",
    "KNOWN_LIMITS",
    "MIN_RECORDS",
    "GateResult",
    "gate",
    "hard_failures",
    "acceptance_records",
]

_SUMMARY_SUFFIX = "_summary"

# --------------------------------------------------------------------------
# 配置自检表：`run_all.py` 把 Config.validate() 的结果命名为这两张表。
# 它们的结构是 {检查名: {"实际":…, "门限":…, "PASS":bool, "单位":…}}，
# 与 `*_summary` 不同，因此需要单独列举，不能靠"名字以 _summary 结尾"命中。
# --------------------------------------------------------------------------
ACCEPTANCE_CONTAINERS: tuple[str, ...] = ("validate", "validate_ktc")

# --------------------------------------------------------------------------
# 已知限制账本：名字 -> 为什么它当前允许失败、以及解除条件。
#
# 这不是"把碍事的 FAIL 藏起来"的开关：条目必须存在、且必须正在失败，否则
# 门禁报错（见 gate）。空字符串或不写理由是不允许的 —— 每条都要能回答
# "为什么这不是一个必须在合并前修好的缺陷"。
# --------------------------------------------------------------------------
KNOWN_LIMITS: dict[str, str] = {
    "validate_ktc.KTC 校正项带宽上限 f_max (满幅)": (
        "已知架构限制，非配置笔误。论文 [00_1] 披露的信号带是 DC–5 MHz，而"
        "当前参数下 KTC 校正项在满幅（A = v_fs = 3 V）时只在 f ≤ 2.5465 MHz "
        "内不越出 ADC2 量程（margin = min(adc2_v_max − G0·Δ1, −adc2_v_min) = "
        "0.15 V，Δt = Ts/256，G0 = 32）。config.py 顶部注释按更早的参数写的是"
        "「≈5.6 MHz」，已过期。本仓库把 KTC 定位为机制研究（观测通路的量化、"
        "编码与延迟尚未建模），所以这条按「上界报告」读，不计为硬性验收。"
        "解除条件二选一：(a) 把 f_max 抬到 ≥ 5 MHz（改 adc2_v_min / Δt / G0，"
        "会移动已发布数值）；(b) 确认本项是信息性记录而非验收判据，把它从"
        "validate_ktc 的 PASS/FAIL 语义中移出。"
    ),
}

# --------------------------------------------------------------------------
# 覆盖度下限：显式列举的判据条数。若某次重构让某类记录悄悄消失，条数会掉到
# 下限以下，门禁报错 —— 否则"漏检"可以伪装成"全通过"。
# 构成：10 条顶层 PASS 记录 + 5 个 _summary 组下的 25 个布尔项
#       + validate 的 11 项 + validate_ktc 的 4 项 = 50。
# --------------------------------------------------------------------------
MIN_RECORDS = 50


def acceptance_records(results: dict[str, Any]) -> dict[str, bool]:
    """Collect every explicitly-enumerated acceptance record and its verdict.

    Args:
        results: ``tools/run_all.py`` 构建的 ``R`` 映射。允许任意嵌套，但只
            检查上述三种**顶层**结构；嵌套子字典（如 ``results["s10"]`` 内部
            的 ``["noise_vs_pool"]["PASS"]``）由对应的 ``*_summary`` 记录代表，
            不重复计入。

    Returns:
        dict[str, bool]: 记录名 -> 是否通过。记录名形如 ``"pipeline.PASS"``、
        ``"s12_summary.电荷口径闭合：…"`` 或 ``"validate_ktc.KTC …"``。

    Examples:
        >>> acceptance_records({"pipeline": {"PASS": True}})
        {'pipeline.PASS': True}
        >>> sorted(acceptance_records(
        ...     {"s12_summary": {"a": True, "b": False},
        ...      "validate": {"x": {"PASS": False}}}
        ... ))
        ['s12_summary.a', 's12_summary.b', 'validate.x']
        >>> acceptance_records({"反例对照": {"loss_db": -6.0}})   # 无 PASS 键 -> 不计
        {}
    """
    out: dict[str, bool] = {}
    for name, val in results.items():
        if not isinstance(val, dict):
            continue
        if "PASS" in val:
            out[f"{name}.PASS"] = bool(val["PASS"])
        elif name.endswith(_SUMMARY_SUFFIX):
            out.update({f"{name}.{k}": bool(ok) for k, ok in val.items()})
        elif name in ACCEPTANCE_CONTAINERS:
            out.update(
                {
                    f"{name}.{k}": bool(sub["PASS"])
                    for k, sub in val.items()
                    if isinstance(sub, dict) and "PASS" in sub
                }
            )
    return out


def hard_failures(results: dict[str, Any]) -> list[str]:
    """列出被判定为硬性验收、且未通过的记录名（不含已知限制账本）。

    Args:
        results: ``tools/run_all.py`` 构建的 ``R`` 映射。

    Returns:
        list[str]: 未通过的记录名，已排序。空列表表示全部通过。
        注意：本函数是**纯规则**，不知道 :data:`KNOWN_LIMITS` 的存在；
        需要"扣除已登记限制、并检查账本是否过期"时用 :func:`gate`。

    Examples:
        >>> hard_failures({"pipeline": {"PASS": True}, "s12_summary": {"a": True}})
        []
        >>> hard_failures({"pipeline": {"PASS": False}})
        ['pipeline.PASS']
        >>> hard_failures({"validate": {"x": {"PASS": False}}})
        ['validate.x']
        >>> hard_failures({"反例对照": {"loss_db": -6.0}})   # 非验收记录 -> 不计
        []
    """
    return sorted(name for name, ok in acceptance_records(results).items() if not ok)


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluating the acceptance rule against one results mapping.

    Attributes:
        failures: 未通过、且**不在**已知限制账本里的记录名。
        exempted: 未通过、但已在账本中登记并说明理由的记录名。
        stale_exemptions: 账本中"结果里不存在"或"当前已经通过"的条目 ——
            两者都说明账本过期了，必须报错，否则豁免会永久残留。
        record_count: 实际收集到的判据条数，与 :data:`MIN_RECORDS` 比较。
    """

    failures: list[str] = field(default_factory=list)
    exempted: list[str] = field(default_factory=list)
    stale_exemptions: list[str] = field(default_factory=list)
    record_count: int = 0

    @property
    def coverage_shortfall(self) -> int:
        """判据条数距下限还差多少（0 表示覆盖度达标）。"""
        return max(0, MIN_RECORDS - self.record_count)

    def ok(self) -> bool:
        """True if the command may exit zero.

        Returns:
            bool: 没有未登记的失败、没有过期豁免、覆盖度达标。
        """
        return not (self.failures or self.stale_exemptions or self.coverage_shortfall)

    def lines(self) -> list[str]:
        """Human-readable report lines, most important first.

        The verdict line is emitted **whenever the command may exit zero** — even
        when a known limit is being printed. Otherwise a log that contains only a
        ``KNOWN`` line has no explicit "this passed" statement in it, and a reader
        (or a CI log search) cannot tell a clean run from a truncated one.

        Returns:
            list[str]: 供 ``run_all.py`` 逐行打印。

        Examples:
            >>> [ln for ln in gate({"pipeline": {"PASS": False}}).lines()
            ...  if ln.startswith("硬性验收")]
            ['硬性验收: FAIL（1 项未通过）']
        """
        out: list[str] = []
        if self.failures:
            out.append(f"硬性验收: FAIL（{len(self.failures)} 项未通过）")
            out += [f"  FAIL {n}" for n in self.failures]
        if self.exempted:
            out.append(f"已知限制（计为不通过，但已登记理由，不阻塞）: {len(self.exempted)} 项")
            out += [f"  KNOWN {n}" for n in self.exempted]
        if self.stale_exemptions:
            out.append(f"已知限制账本过期: {len(self.stale_exemptions)} 项（条目已不存在或已通过）")
            out += [f"  STALE {n}" for n in self.stale_exemptions]
        if self.coverage_shortfall:
            out.append(
                f"验收覆盖度不足: 只收集到 {self.record_count} 条判据，"
                f"下限 {MIN_RECORDS} 条 —— 有记录类别消失了"
            )
        if self.ok():
            tail = f"，其中 {len(self.exempted)} 条为已登记的已知限制" if self.exempted else ""
            out.append(f"硬性验收: 全部通过（{self.record_count} 条判据{tail}）")
        return out


def gate(results: dict[str, Any]) -> GateResult:
    """Evaluate the acceptance rule, the known-limits ledger and coverage.

    Args:
        results: ``tools/run_all.py`` 构建的 ``R`` 映射。

    Returns:
        GateResult: 见该类。``ok()`` 为真时命令应退出 0。

    Raises:
        ValueError: 账本里存在**空白理由**的条目（不允许无理由豁免）。

    Examples:
        >>> gate({"pipeline": {"PASS": True}, "validate": {"x": {"PASS": True}}}).failures
        []
        >>> gate({"pipeline": {"PASS": False}}).failures
        ['pipeline.PASS']
        >>> gate({"pipeline": {"PASS": True}}).stale_exemptions != []
        True
    """
    for name in KNOWN_LIMITS:
        if not KNOWN_LIMITS[name].strip():
            raise ValueError(
                f"known-limit entry {name!r} has no reason; an unexplained exemption is not allowed"
            )

    recs = acceptance_records(results)
    failing = {n for n, ok in recs.items() if not ok}
    failures = sorted(failing - set(KNOWN_LIMITS))
    exempted = sorted(failing & set(KNOWN_LIMITS))
    stale = sorted(n for n in KNOWN_LIMITS if n not in failing)
    return GateResult(
        failures=failures,
        exempted=exempted,
        stale_exemptions=stale,
        record_count=len(recs),
    )
