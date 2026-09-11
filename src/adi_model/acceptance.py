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
纳入配置自检后立刻暴露出一条记录为 FAIL（``validate_ktc`` 的 KTC 带宽判据），
当时把它登记进 :data:`KNOWN_LIMITS`，理由写作"KTC 架构限制"。

第三份复核（2026-09-11，针对 v7.0.2）指出该判据用的是 **ADC2 量程**口径，而
校正量早已按 ADR 0006 改在**数字域**扣除 —— 节点取错，2.5465 MHz 是**假失败**。
复核复算一致（本项目独立复算：同一参数下观测通路口径为 134.45 MHz，通过）。
判据已改为绑定观测通路，**账本因此清空**。

这段历史留在这里，因为它说明账本的适用边界：账本登记的是"已知、已解释、
暂不阻塞"的限制，**不能用来固化一条本身取错对象的判据** —— 那是把缺陷升级
成豁免。发现判据本身错了就修判据，而不是继续给它记账。

账本机制保留（非空时仍带两道守卫，语义等价于测试里的 ``xfail(strict=True)``）：

* 账本里的名字**必须存在**于结果中（否则是过期条目）；
* 账本里的名字**必须当前正在失败**（一旦修好，条目变成过期，门禁报错，
  逼人摘掉它）。

豁免项会被逐条打印，不作为"通过"呈现。

必需判据
--------
覆盖度下限（:data:`MIN_RECORDS`）只能防"条数变少"：删掉一条关键判据、再往
别处加一条无关记录，总数仍是 50，下限一声不响。因此另维护一份**必需的判据
ID 集合**（:data:`REQUIRED_RECORDS`），只收录"删掉它，某个结论就失去证据"
的那些条。它刻意**不**覆盖全部 50 条 —— 那会退化成第二份需要手工同步的
schema，维护不好就是新债；这里的每条都对应一个具体结论。

取值必须是真的布尔。此前用 ``bool(val)`` 归一化，而 ``bool("False")`` 是
``True``：一次序列化改动就能把失败读成通过。现在遇到非布尔值直接报错，
不猜字符串的含义。

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
    "REQUIRED_RECORDS",
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
    # 当前为空。上一条（"validate_ktc.KTC 校正项带宽上限 f_max (满幅)"）不是
    # 被删除的，而是**判据本身取错了节点**：它按 ADC2 量程口径算，而校正量
    # 按 ADR 0006 在数字域扣除。判据已改为观测通路口径并通过，因此在这里
    # 登记的那条限制不再存在（见模块 docstring 的"已知限制账本"）。
    #
    # 未来若出现"确实失败、且已解释、暂不阻塞"的判据，在这里逐条登记，
    # 并写清解除条件。理由为空字符串会被 gate() 直接拒绝。
}

# --------------------------------------------------------------------------
# 覆盖度下限：显式列举的判据条数。若某次重构让某类记录悄悄消失，条数会掉到
# 下限以下，门禁报错 —— 否则"漏检"可以伪装成"全通过"。
# 构成：10 条顶层 PASS 记录 + 5 个 _summary 组下的 25 个布尔项
#       + validate 的 11 项 + validate_ktc 的 4 项 = 50。
# --------------------------------------------------------------------------
MIN_RECORDS = 50

# --------------------------------------------------------------------------
# 必需判据 ID：删掉它、某个结论就失去证据的那些条。
#
# 与 MIN_RECORDS 的分工：MIN_RECORDS 只数条数，"删一条 + 加一条"骗得过它；
# 这份集合钉住的是具体的结论载体。刻意不列全 50 条（那会变成第二份手工
# schema），只列"唯一证据"型的条目。
#
# 维护约定：改判据名字（例如本轮的 f_max 口径修正）时必须同步这里 —— 名字
# 对不上就会报 missing，正好逼人确认"改的这条还是不是同一件事"。
# --------------------------------------------------------------------------
REQUIRED_RECORDS: frozenset[str] = frozenset(
    {
        # 电荷口径闭合 —— 分段 DAC 物理求值 = 独立节点方程的唯一机器证据。
        "s12_summary.电荷口径闭合：闭式解 = 节点矩阵求解器（fV 级）",
        "s12_summary.电荷口径闭合：evaluate_physical = 独立节点方程（pV 级）",
        "s12_summary.电荷口径闭合：输入=输入等效DAC电压 -> 残差为零（nV 级）",
        "s12_summary.DEM 对 C_C 锯齿无效（结构性质，DEM 开/关偏差 ≤5%）",
        "s12_summary.分段拓扑 SNDR 与 unary 同量级",
        # 增益/β 校准链
        "s10_summary.C_F->增益(电荷一致)",
        "s10_summary.beta校准",
        "s10_summary.指标已知答案",
        # KTC 支路
        "s11_summary.KTC 双 beta 模型吻合",
        "s11_summary.eta_n/eta_x 三激励验证",
        "s11_summary.校准可观测性 rank(U)=64",
        # 动态误差 / INL
        "s13_summary.base 行确定性 INL 与其解析预测吻合（子阵列节点寄生）",
        "s13_summary.完整 DEM 周期覆盖后 INL 不随 rep 变化（确定性协议收敛）",
        # 交织映射与校准
        "s14_summary.permute 校准显著改善（整数码，改善 ≥3 倍）",
        "s14_summary.随机置换映射接近满秩",
        "s14_summary.fixed 交织映射秩不足",
        # 配置自检里与结论直接挂钩的三条
        "validate.第一级读数与 DAC 拓扑自洽（2**b1 <= DAC 电平数）",
        "validate.dither 增强位数 == log2(DAC 电平数 / 2**b1)",
        "validate.RA 增益口径 ra_gain_model ∈ {charge, fixed}",
        # 本轮修正口径的那条 —— 钉住它，防止"改回 ADC2 口径"或名字漂移
        "validate_ktc.KTC 观测通路摆幅上限 f_max (满幅)",
    }
)


def _verdict(raw: Any, where: str) -> bool:
    """把一条验收记录的取值收成真布尔，拒绝一切需要"猜"的输入。

    Args:
        raw: 记录里 ``"PASS"`` 字段的原始值。
        where: 记录名；只用于错误消息。

    Returns:
        bool: ``raw`` 本身，要求它已经是 ``bool``。

    Raises:
        ValueError: ``raw`` 不是 ``bool``。**不做** ``bool(raw)`` 归一化 ——
            ``bool("False")`` 为真，一次序列化改动就能把失败读成通过；
            宁可在这里响亮地失败。

    Examples:
        >>> _verdict(True, "x")
        True
        >>> _verdict(False, "x")
        False
        >>> _verdict("False", "x")
        Traceback (most recent call last):
            ...
        ValueError: acceptance record 'x' has PASS='False' (str), not a bool — refusing to read a non-boolean verdict
    """
    if not isinstance(raw, bool):
        raise ValueError(
            f"acceptance record {where!r} has PASS={raw!r} ({type(raw).__name__}), "
            f"not a bool — refusing to read a non-boolean verdict"
        )
    return raw


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

    Raises:
        ValueError: 某条记录的 ``PASS`` 不是真布尔（见 :func:`_verdict`）。

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
        >>> acceptance_records({"validate": {"x": {"PASS": "False"}}})  # 字符串 -> 报错
        Traceback (most recent call last):
            ...
        ValueError: acceptance record 'validate.x' has PASS='False' (str), not a bool — refusing to read a non-boolean verdict
    """
    out: dict[str, bool] = {}
    for name, val in results.items():
        if not isinstance(val, dict):
            continue
        if "PASS" in val:
            out[f"{name}.PASS"] = _verdict(val["PASS"], f"{name}.PASS")
        elif name.endswith(_SUMMARY_SUFFIX):
            out.update({f"{name}.{k}": _verdict(ok, f"{name}.{k}") for k, ok in val.items()})
        elif name in ACCEPTANCE_CONTAINERS:
            for k, sub in val.items():
                if isinstance(sub, dict) and "PASS" in sub:
                    out[f"{name}.{k}"] = _verdict(sub["PASS"], f"{name}.{k}")
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
        missing_required: :data:`REQUIRED_RECORDS` 里在结果中找不到的条目。
            条数可能没变，但某个结论已经失去了证据。
        record_count: 实际收集到的判据条数，与 :data:`MIN_RECORDS` 比较。
    """

    failures: list[str] = field(default_factory=list)
    exempted: list[str] = field(default_factory=list)
    stale_exemptions: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    record_count: int = 0

    @property
    def coverage_shortfall(self) -> int:
        """判据条数距下限还差多少（0 表示覆盖度达标）。"""
        return max(0, MIN_RECORDS - self.record_count)

    def ok(self) -> bool:
        """True if the command may exit zero.

        Returns:
            bool: 没有未登记的失败、没有过期豁免、没有必需判据缺失、
            覆盖度达标。
        """
        return not (
            self.failures
            or self.stale_exemptions
            or self.missing_required
            or self.coverage_shortfall
        )

    def lines(self) -> list[str]:
        """Human-readable report lines, most important first.

        The verdict line is emitted **whenever the command may exit zero** — even
        when a known limit is being printed. Otherwise a log that contains only a
        ``KNOWN`` line has no explicit "this passed" statement in it, and a reader
        (or a CI log search) cannot tell a clean run from a truncated one.

        When something is exempted, the verdict line says so in the main clause
        ("通过 —— 存在 N 项已登记、未满足的限制") rather than opening with
        "全部通过" and qualifying it in brackets: a reader scanning for the
        verdict should not come away thinking every criterion was met.

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
        if self.missing_required:
            out.append(f"必需判据缺失: {len(self.missing_required)} 项（结论失去证据）")
            out += [f"  MISSING {n}" for n in self.missing_required]
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
            if self.exempted:
                out.append(
                    f"硬性验收: 通过 —— 存在 {len(self.exempted)} 项已登记、"
                    f"未满足的限制（见上，不计入达标）"
                )
            else:
                out.append(f"硬性验收: 全部通过（{self.record_count} 条判据）")
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
        >>> gate({"pipeline": {"PASS": True}}).missing_required[:1]  # 判据缺失也不放行
        ['s10_summary.C_F->增益(电荷一致)']
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
    missing = sorted(REQUIRED_RECORDS - set(recs))
    return GateResult(
        failures=failures,
        exempted=exempted,
        stale_exemptions=stale,
        missing_required=missing,
        record_count=len(recs),
    )
