#!/usr/bin/env python3
r"""把 ``Config`` 导出为 RTL 参数包、定点寄存器镜像与黄金激励向量（P0 交付物 2/2）。

职责一句话
----------
把行为模型的**唯一参数源** :class:`adi_model.config.Config` 翻译成 RTL 侧需要的三样东西：
``rtl/params/rtl_params.vh``（可综合常量）、``sim/vectors/params.json``（含来源分级的完整清单）、
``sim/vectors/registers.json``（定点寄存器镜像），以及可选的一组黄金激励 + 期望码流。
它只做转录，不引入数值模型，也不生成任何 RTL 逻辑。

物理模型与文献出处
------------------
本脚本不引入新的物理模型，只转录以下既有来源：

* 定点格式与算式：``docs/adr/0014-fixed-point-output-contract.md`` 与 ``src/adi_model/fixed_point.py``；
* 本仓库冻结的 RTL 算术契约：``docs/rtl/RTL_ARITHMETIC_CONTRACT.md``；
* 参数来源分级：``adi_model.provenance.PARAM_GRADES``；
* 阵列/DEM/开关几何：``adi_model.mapper``、``adi_model.dem``、``adi_model.dac_arch``。

单位契约
--------
* ``rtl_params.vh`` 只含**无量纲整数**（位宽、计数、状态数、旋转常数、模式码）。
  模拟量（``fs``、``v_fs``、``delta1``、各类步长）**不进 .vh** —— RTL 内部不出现伏特
  （契约 §1.1），它们只出现在 ``params.json`` 里供测试台与 Spectre 转交使用。
* ``params.json`` 中的模拟量以 ``{"num", "den_exp"}`` 给出**精确**有理数：IEEE754 有限浮点
  本身是二进有理数，故 ``den`` 恒为 2 的幂，这是无损表示；另附 ``float`` 字段仅供人读，
  **不用于任何计算**。
* 激励向量：``inj_q`` 为 V/Vfs 口径的有符号 Q32 整数，其余字段为二进制/整数码。
  列定义写在生成文件头部注释里，格式由 ``tests/unit/test_rtl_export.py`` 实际解析校验。

参数来源分级
------------
* Config **字段**：直接读 :func:`adi_model.provenance.annotate_config`，本脚本不自行判断等级。
* **派生量**（如 ``dac_levels``、``units_per_lsb1``）：按**最弱输入**传播 ——
  输入里只要有一个 ``[假设]``，派生量就是 ``[假设]``。排序
  ``DISCLOSED < DERIVED < FITTED < ASSUMED < RESEARCH_EXTENSION``。
  这条规则是机械的、可复核的，目的是让"从假设推出来的数字"不可能伪装成披露值。
* **RTL 专有量**（如 ``PHASES``）：标 ``[假设]``，并注明它是 RTL 设计选择而非模型参数。

契约与不变量 / 适用域
--------------------
* **单一真相源**：``rtl_params.vh`` 由本脚本生成，禁止手工编辑，也禁止在 RTL 里再写一份常量表。
* **受检产物是 ``Config`` 的纯函数**：``rtl_params.vh``、``params_<stem>.json``、
  ``registers_<stem>.json``、``stimulus|expected_<stem>.hex`` 里**不得**出现任何 git 派生字段
  （``revision`` / ``dirty`` / 40 位 sha / ``describe``）。
  历史缺陷：这些字段曾被写进 ``.vh`` 头部与 ``params.json`` 的 ``meta.revision``，
  于是**提交这个动作本身**就让门禁作废 —— HEAD 一移动，这两个产物立刻被判漂移，
  任何 clone 到这个 commit 的人跑 ``--check`` 都红。见下一节。
* **工作树身份单独记在 manifest 里**：``sim/vectors/export_manifest_<stem>.json`` 记
  ``revision / describe / dirty`` 与各产物的 sha256。它**刻意不进 ``--check``** ——
  它记的就是"运行环境"，天生每次都可能不同（见该文件的 ``why_not_checked`` 字段）。
  产物侧只保留"由 ``Config`` 唯一决定"的内容，身份侧只做人类追溯，两边不混。
* **可复现**：同一 ``Config`` 下载重复运行输出逐字节一致（``tests/unit/test_rtl_export.py`` 强制）。
  文件头写入载荷 SHA256，任何漂移都可 diff 出来；``--check`` 模式直接以此作为门禁。
* **``--check`` 只归一 EOL，不放宽内容判据**：检出时 ``core.autocrlf=true`` 会把盘上文件
  变成 CRLF，而生成器写 LF。比较前两边都做 ``\r\n -> \n`` 归一（**只**归一 EOL，其余字节照比），
  否则门禁会因为"签出的行尾策略"而不是因为内容变化报红。数值/文本任何一处改动仍然会红
  （``test_eol_only_differences_are_not_drift`` 双向验证）。
* **适用域**：定点输出接口只覆盖 ``dac_arch="split"``。``unary`` 拓扑没有
  :class:`~adi_model.weight_calibration.CalibrationSpec`，此时只导出 ``.vh`` 与 ``params.json``，
  并在报告里**显式**写明寄存器镜像与激励被跳过——不静默省略。

Usage::

    python tools/export_rtl_params.py --config paper_literal --emit-stimulus 256
    python tools/export_rtl_params.py --check

``--check`` 是门禁：磁盘产物与当前源码不一致时退出码非零。

需要先 ``python -m pip install -e '.[dev]'``（与 ``tools/`` 下其它脚本同一约定）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from adi_model import Config, FixedPointFormat, FixedPointReconstructor
from adi_model.mapper import _LCG_A, N_DEM_STATES
from adi_model.provenance import SourceGrade, annotate_config
from adi_model.sadc import units_per_first_stage_step
from adi_model.sampler import sine_input

# 最弱输入的传播次序：数字越大越"不可引用"。见模块 docstring「参数来源分级」。
_GRADE_SEVERITY: dict[SourceGrade, int] = {
    SourceGrade.DISCLOSED: 0,
    SourceGrade.DERIVED: 1,
    SourceGrade.FITTED: 2,
    SourceGrade.ASSUMED: 3,
    SourceGrade.RESEARCH_EXTENSION: 4,
}

_DITHER_MODE_CODE = {"off": 0, "analog": 1, "quantizer": 2, "sampling": 3}

# 黄金激励波形：相干频率与幅度。取常量而非就地写死，是为了让
# tests/unit/test_rtl_export.py 能**独立复算**同一记录，而不必复制这些魔数。
GOLDEN_TONE_NUM = 73
GOLDEN_TONE_DEN = 2048
GOLDEN_AMPLITUDE_FRAC = 0.8

_CONFIG_FACTORIES: dict[str, Callable[[], Config]] = {
    "paper_literal": Config.paper_literal,
    "paper_consistent": Config.paper_consistent,
    "legacy_v61": Config.legacy_v61,
    "default": Config,
}


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def exact_rational(x: float) -> dict[str, Any]:
    """Return an exact dyadic representation of a finite float.

    Args:
        x: A finite float.

    Returns:
        ``{"num": int, "den_exp": int, "float": float}`` with ``x == num / 2**den_exp``
        exactly. ``float`` is for human reading only and must not be used downstream.
    """
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"cannot represent non-finite value {x!r} exactly")
    num, den = value.as_integer_ratio()
    den_exp = den.bit_length() - 1
    if den != 1 << den_exp:  # pragma: no cover - as_integer_ratio is always dyadic
        raise AssertionError("float denominator is not a power of two")
    return {"num": num, "den_exp": den_exp, "float": value}


def weakest_grade(grades: list[SourceGrade]) -> SourceGrade:
    """Return the least quotable grade among the inputs.

    Args:
        grades: Input grades; must be non-empty.

    Returns:
        The grade with the largest severity, i.e. the weakest link.

    Raises:
        ValueError: If ``grades`` is empty.
    """
    if not grades:
        raise ValueError("derived quantities must declare at least one input")
    return max(grades, key=lambda g: _GRADE_SEVERITY[g])


def repo_identity(root: Path) -> dict[str, str]:
    """Return the git revision of the working tree, degrading gracefully.

    **只用于 manifest**（``sim/vectors/export_manifest_<stem>.json``），绝不进受检产物：
    这些值取决于运行环境而非 ``Config``，一旦写进 ``.vh`` / ``params.json``，
    "提交"这个动作本身就会让 ``--check`` 失效。

    Args:
        root: Repository root holding ``.git``.

    Returns:
        ``{"commit": ..., "describe": ..., "dirty": "0"|"1"}``; unknown values are
        reported as the string ``"unknown"`` rather than omitted, so a consumer can
        always see that provenance is missing.
    """

    def _run(args: list[str]) -> str:
        try:
            out = subprocess.run(
                args, cwd=root, capture_output=True, text=True, check=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return out.stdout.strip() or "unknown"

    dirty = _run(["git", "status", "--porcelain"])
    return {
        "commit": _run(["git", "rev-parse", "HEAD"]),
        "describe": _run(["git", "describe", "--tags", "--always"]),
        "dirty": "unknown" if dirty == "unknown" else ("1" if dirty else "0"),
    }


MANIFEST_WHY_NOT_CHECKED = [
    "本文件**刻意不进 --check**，也刻意不参与任何比较。",
    "它记的是生成这些产物时的工作树身份（git revision / describe / dirty）与各产物 sha256；"
    "这些值只取决于运行环境，与 Config 无关 —— 任何一次提交、任何未提交改动都会让它变化。",
    "若把它纳入比较，它会在每次 commit 后立刻失效，把 --check 变成对用户恒假的门禁。"
    "这正是原先把 revision 写进 rtl_params.vh 头部与 params.json 的 meta 的缺陷："
    "**提交这个动作本身把门禁作废了**（HEAD 从 517bd2f 移到 384da6a 就触发了两处 DRIFT）。",
    "现在的分工：受检产物（.vh / params_*.json / registers_*.json / stimulus|expected_*.hex）"
    "必须是 Config 的纯函数，不得出现 revision / dirty / 40 位 sha / describe；"
    "工作树身份只集中记在本文件里，供人追溯。",
]


def manifest_text(
    stem: str,
    *,
    invocation: str,
    identity: dict[str, str],
    artifacts: dict[str, str],
) -> str:
    """Render the side-car manifest that records the *environment*, not the Config.

    Args:
        stem: Artifact stem (``<config>[_<dither_mode>]``).
        invocation: Exact command line that produced the artifacts.
        identity: Output of :func:`repo_identity`.
        artifacts: ``{repo-relative path: file text}``; sha256 of each is recorded.

    Returns:
        JSON text. Deliberately **never** compared by ``--check`` — see
        :data:`MANIFEST_WHY_NOT_CHECKED`.
    """
    return _json_text(
        {
            "schema_version": 1,
            "generator": "tools/export_rtl_params.py",
            "stem": stem,
            "invocation": invocation,
            "why_not_checked": MANIFEST_WHY_NOT_CHECKED,
            "revision": identity,
            "artifacts": {
                path: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for path, text in sorted(artifacts.items())
            },
        }
    )


def _json_text(obj: Any) -> str:
    """Serialise deterministically, refusing NaN/Infinity per repository policy."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _display(path: Path, root: Path) -> str:
    """Return a path relative to ``root`` when possible, else the absolute path."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _normalize_eol(text: str) -> str:
    r"""Collapse CRLF to LF **only**; every other byte stays as-is.

    为什么需要：``core.autocrlf=true`` 的检出会把盘上的产物写成 CRLF，而生成器写 LF。
    比较时若不归一，``--check`` 会因为"检出的行尾策略"报红，而不是因为内容变化。
    这里**只**动 ``\\r\\n``，所以任何数值/文本改动仍然照红（见
    ``tests/unit/test_rtl_export.py::test_eol_only_differences_are_not_drift``）。
    注：``Path.read_text`` 的 universal newlines 当前已经做了同样的归一，
    所以这一层是**显式**的防线 —— 若将来有人改成 ``newline=""`` 或二进制读，
    判据不会因此被悄悄放宽。
    """
    return text.replace("\r\n", "\n")


def _write(path: Path, text: str, *, check: bool, drift: list[str]) -> bool:
    """Write ``text`` to ``path``, or record drift when ``check`` is set.

    Returns:
        ``True`` when the artifact is current (or was written), ``False`` on drift.
    """
    if check:
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current is None or _normalize_eol(current) != _normalize_eol(text):
            drift.append(str(path))
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


# ----------------------------------------------------------------------
# 参数清单
# ----------------------------------------------------------------------
def rtl_localparams(cfg: Config, fmt: FixedPointFormat, phases: int) -> list[dict[str, Any]]:
    """Build the RTL-only localparam list (dimensionless integers).

    Args:
        cfg: Resolved configuration.
        fmt: Frozen fixed-point format (ADR 0014).
        phases: RTL phase-tick count per conversion cycle. This is an **RTL design
            choice**, not a model parameter, so it is graded ASSUMED.

    Returns:
        One entry per constant: ``name``, ``value``, ``width``, ``group``, ``grade``,
        ``source`` and an optional ``comment``.

    Raises:
        ValueError: If a value does not fit the declared width, or if
            ``units_per_lsb1`` disagrees with ``dac_levels // 2**b1`` (the single
            source of truth required by ADR 0004).
    """
    # M15（独立审查 2026-09-25 第三轮）：phases 是 raw 里唯一可能为浮点的入口。
    # 必须在 int(phases) 之前校验：非有限或非整数一律给契约内的 ValueError，
    # 否则 int(inf)/int(nan) 会抛裸 OverflowError、int(3.5) 会截断掩盖非整数输入
    # （非契约异常，同类 N1：入口非法输入一律显式 ValueError）。
    if not math.isfinite(phases) or int(phases) != phases:
        raise ValueError(f"phases={phases!r} 必须是有限的整数值（RTL 相位刻度数）")

    # ADR 0004 的教训：两条通路各自推导步长，b1=6 时数值巧合相等而长期掩盖分歧。
    # 导出时就把它变成硬门禁，而不是等到两条 RTL 通路各写一遍。
    from_dac = int(cfg.dac_levels) // (2 ** int(cfg.b1))
    if int(cfg.units_per_lsb1) != from_dac:
        raise ValueError(
            "units_per_lsb1 disagrees with dac_levels//2**b1 "
            f"({cfg.units_per_lsb1} != {from_dac}); refusing to export an ambiguous grid"
        )

    n_main, n_sub = int(cfg.dac_n_main), int(cfg.dac_n_sub)
    rot_width = math.ceil(math.sqrt(n_main))
    rot_height = math.ceil(n_main / rot_width)

    raw: list[tuple[str, int, int, str, str]] = [
        # --- 定点格式（契约 §2）---
        ("W_FRAC", fmt.weight_fraction_bits, 6, "fixed_point", "ADR 0014"),
        ("W_BITS", fmt.coefficient_bits, 7, "fixed_point", "ADR 0014"),
        ("V_FRAC", fmt.voltage_fraction_bits, 6, "fixed_point", "ADR 0014"),
        ("V_BITS", fmt.voltage_bits, 7, "fixed_point", "ADR 0014"),
        ("ACC_BITS", fmt.accumulator_bits, 8, "fixed_point", "ADR 0014"),
        ("OUT_BITS", fmt.output_bits, 6, "fixed_point", "ADR 0014"),
        # --- 阵列几何 ---
        ("N_SLICES", int(cfg.n_slices), 5, "array", "config.n_slices"),
        ("N_ACTIVE", int(cfg.n_active), 4, "array", "config.n_active"),
        ("N_UNIT_MAIN", n_main, 7, "array", "config.dac_n_main"),
        ("N_UNIT_SUB", n_sub, 5, "array", "config.dac_n_sub"),
        ("N_UNIT_TOTAL", n_main + n_sub, 7, "array", "dac_n_main + dac_n_sub"),
        ("DAC_LEVELS", int(cfg.dac_levels), 10, "array", "config.dac_levels"),
        ("DAC_COMPLETE_RANGE", int(bool(cfg.dac_complete_range)), 1, "array", "config"),
        # --- 码栅格（ADR 0004：唯一真相源）---
        ("B1", int(cfg.b1), 4, "grid", "config.b1"),
        ("STAGE1_LEVELS", int(cfg.stage1_levels), 10, "grid", "config.stage1_levels"),
        ("N_UNITS_SIG", int(cfg.n_units_sig), 10, "grid", "config.n_units_sig"),
        ("UNITS_PER_LSB1", int(cfg.units_per_lsb1), 10, "grid", "config.units_per_lsb1"),
        ("UNITS_PER_D1", int(units_per_first_stage_step(cfg, None)), 10, "grid", "sadc"),
        ("N_UNITS_HEADROOM", int(cfg.n_units_headroom), 10, "grid", "config"),
        ("K0", int(cfg.n_units_headroom) // 2, 10, "grid", "mapper.encode"),
        ("ADC2_BITS", int(cfg.adc2_n_bits), 5, "grid", "config.adc2_n_bits"),
        # --- DEM（映射器状态机）---
        ("DEM_STATES", int(N_DEM_STATES), 10, "dem", "mapper.N_DEM_STATES"),
        ("DEM_LCG_A", int(_LCG_A), 32, "dem", "mapper._LCG_A"),
        (
            "DEM_LCG_A_MOD",
            int(_LCG_A) % int(N_DEM_STATES),
            10,
            "dem",
            "mapper._LCG_A mod mapper.N_DEM_STATES - RTL 只需低位，勿手算",
        ),
        ("DEM_ROT_WIDTH", rot_width, 4, "dem", "dem.py: ceil(sqrt(n_main))"),
        ("DEM_ROT_HEIGHT", rot_height, 4, "dem", "dem.py: ceil(n_main/width)"),
        ("DEM_SUB_ROLL", n_sub, 5, "dem", "dem.py: sub_order period"),
        # ⚠️ 这两个是**复位默认值**，不是"DEM 一定开着"。
        # 陷阱：Config.paper_literal() 的 dem_enable 默认是 False，此时
        # split_switch_command 会把 states 强制为 0，全部置换退化为恒等，
        # 而 dem_bridge_enable 却是 True —— 名称看起来"桥接开着"，实际整条 DEM 不工作。
        (
            "DEM_ENABLE",
            int(bool(cfg.dem_enable)),
            1,
            "dem",
            "config.dem_enable - 复位默认值；运行时由寄存器覆盖",
        ),
        (
            "DEM_BRIDGE_ENABLE",
            int(bool(cfg.dem_bridge_enable)),
            1,
            "dem",
            "config.dem_bridge_enable - 复位默认值；仅当 DEM_ENABLE 同时为 1 才生效",
        ),
        # --- dither ---
        (
            "DITHER_MODE",
            _DITHER_MODE_CODE[str(cfg.dither_mode)],
            2,
            "dither",
            "0 off/1 analog/2 quantizer/3 sampling",
        ),
        (
            "DITHER_UNITS_TOTAL",
            int(cfg.dither_units_total),
            4,
            "dither",
            "config.dither_units_total",
        ),
        (
            "DITHER_SPLIT_IS_SUB",
            int(cfg.dither_split_bank == "sub"),
            1,
            "dither",
            "config.dither_split_bank",
        ),
        ("DITHER_DISCRETE", int(bool(cfg.dither_discrete)), 1, "dither", "config.dither_discrete"),
        (
            "DITHER_UNITS_RANGE",
            int(round(float(cfg.dither_units_range))),
            4,
            "dither",
            "config.dither_units_range",
        ),
        # --- RTL 专有（[假设]，非模型参数）---
        ("PHASES", int(phases), 6, "rtl_choice", "RTL 设计选择，见计划 §4.2"),
    ]

    out: list[dict[str, Any]] = []
    for name, value, width, group, source in raw:
        # raw 的全部 value 在构造处即为 int（见 raw 的类型标注：fmt.* 由
        # FixedPointFormat.__post_init__ 保证为正 int，cfg.* 均经 int() 包裹，
        # rot_* 为 math.ceil 返回，phases 已在上文入口校验）——此处只需区间校验；
        # 若将来引入浮点项，须在此恢复有限性校验。
        if value < 0 or value >= 1 << width:
            raise ValueError(f"{name}={value} does not fit in {width} bits")
        grade = SourceGrade.ASSUMED if group == "rtl_choice" else SourceGrade.DERIVED
        out.append(
            {
                "name": name,
                "value": int(value),
                "width": int(width),
                "group": group,
                "grade": grade.value,
                "source": source,
            }
        )
    return out


def analog_references(cfg: Config) -> list[dict[str, Any]]:
    """List the analog-facing reference quantities (never emitted into ``.vh``).

    Args:
        cfg: Resolved configuration.

    Returns:
        One entry per quantity: ``name``, ``exact`` rational, ``unit``, ``input_fields``
        (the Config fields it rests on) and the propagated ``grade``.
    """
    from adi_model.provenance import grade_of

    items: list[tuple[str, float, str, tuple[str, ...]]] = [
        ("FS", float(cfg.fs), "Hz", ("fs",)),
        ("V_FS", float(cfg.v_fs), "V", ("v_fs",)),
        ("N_BITS_TARGET", float(cfg.n_bits_target), "bit", ("n_bits_target",)),
        ("LSB_TARGET", float(cfg.lsb_target), "V", ("v_fs", "n_bits_target")),
        ("DELTA1", float(cfg.delta1), "V", ("v_fs", "b1")),
        (
            "NOMINAL_RDAC_STEP",
            float(cfg.nominal_rdac_step),
            "V",
            ("v_fs", "dac_n_main", "dac_n_sub"),
        ),
        ("ADC2_STEP", float(cfg.delta2), "V", ("adc2_v_min", "adc2_v_max", "adc2_n_bits")),
        ("G0", float(cfg.g0), "1", ("g0",)),
        ("KAPPA_EFF", float(cfg.kappa_eff()), "1", ("ktc_kappa", "ktc_gain_n", "g0")),
        (
            "BETA_EFF",
            float(cfg.beta_eff()),
            "1",
            ("ktc_beta_error", "ktc_kappa", "ktc_gain_n", "g0"),
        ),
        (
            "DITHER_ALPHA",
            float(cfg.dither_alpha),
            "1",
            ("dither_mode", "dither_units_range", "dither_split_bank"),
        ),
        ("RA_V_CLIP", float(cfg.ra_v_clip), "V", ("ra_v_clip",)),
        ("C_ACTIVE_NOMINAL", float(cfg.c_active_nominal()), "F", ("c_total0", "cap_scale")),
    ]

    out: list[dict[str, Any]] = []
    for name, value, unit, inputs in items:
        grades = []
        sources = []
        for field in inputs:
            grade, source = grade_of(field)
            grades.append(grade)
            sources.append(f"{field}: {source}")
        grade = weakest_grade(grades)
        out.append(
            {
                "name": name,
                "exact": exact_rational(value),
                "unit": unit,
                "grade": grade.value,
                "grade_label": grade.label_zh,
                "inputs": list(inputs),
                "sources": sources,
                "note": "derived: weakest-input propagation (see exporter docstring)",
            }
        )
    return out


def config_field_entries(cfg: Config) -> dict[str, Any]:
    """Dump every Config field with its declared provenance.

    Values come from ``Config.to_dict()`` so that nested parameter dataclasses
    (``conversion``, ``input_network``) are serialised by the model's own routine
    instead of a second copy written here. A nested block carries the grade of its
    **parent field** as declared in ``PARAM_GRADES`` — the registry does not grade
    the inner members individually, and this export does not invent grades for them.

    Args:
        cfg: Resolved configuration.

    Returns:
        Mapping ``field -> {"value", "grade", "grade_label", "quotable", "source",
        "unit", "note", "overridden"}``.
    """
    annotated = annotate_config(cfg)
    values = cfg.to_dict()
    out: dict[str, Any] = {}
    for name in sorted(values):
        graded = annotated[name]
        out[name] = {
            "value": values[name],
            "grade": graded.grade.value,
            "grade_label": graded.grade.label_zh,
            "quotable": bool(graded.grade.quotable),
            "source": graded.source,
            "unit": graded.unit,
            "note": graded.note,
            "overridden": bool(graded.overridden),
        }
    return out


# ----------------------------------------------------------------------
# 产物：rtl_params.vh
# ----------------------------------------------------------------------
def render_verilog_header(
    params: list[dict[str, Any]], meta: dict[str, Any], payload_sha: str
) -> str:
    """Render the ``rtl_params.vh`` text.

    头部**只**写由 ``Config`` 决定的东西（config 名、定点格式、载荷 SHA256、契约指针）。
    刻意不写 git revision / dirty：那两个值随每次提交变化，写进来会让"提交"本身把
    ``--check`` 作废（历史缺陷，见模块 docstring 与 ``export_manifest_*.json``）。

    Args:
        params: Entries from :func:`rtl_localparams`.
        meta: Header metadata (config name, fixed-point format, guard).
        payload_sha: SHA256 of the rendered parameter block, for drift detection.

    Returns:
        The complete header text, LF-terminated.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in params:
        groups.setdefault(str(entry["group"]), []).append(entry)

    titles = {
        "fixed_point": "定点格式 —— docs/rtl/RTL_ARITHMETIC_CONTRACT.md §2",
        "array": "阵列几何",
        "grid": "码栅格（唯一真相源，勿在 RTL 内重复推导：ADR 0004）",
        "dem": "DEM 状态机与开关置换几何（dem.py 为开关译码主路径）",
        "dither": "dither 模式与掩码",
        "rtl_choice": "RTL 专有量 [假设] —— 非模型参数，改动需同步计划与 ADR",
    }

    lines = [
        "// ---------------------------------------------------------------------------",
        "// rtl_params.vh —— 由 tools/export_rtl_params.py 自动生成，请勿手工编辑。",
        f"// config      : {meta['config']}",
        f"// fixed point : W_FRAC/Q{meta['fixed_point']['weight_fraction_bits']}"
        f" W_BITS/{meta['fixed_point']['coefficient_bits']}"
        f" V_FRAC/Q{meta['fixed_point']['voltage_fraction_bits']}"
        f" V_BITS/{meta['fixed_point']['voltage_bits']}"
        f" ACC_BITS/{meta['fixed_point']['accumulator_bits']}"
        f" OUT_BITS/{meta['fixed_point']['output_bits']}",
        f"// payload sha : {payload_sha}",
        "// 算术契约    : docs/rtl/RTL_ARITHMETIC_CONTRACT.md（冻结；改动须走新 ADR）",
        "// 本文件是 config 的纯函数：不含任何随提交变化的来源标识；",
        "// 工作树身份见 sim/vectors/export_manifest_*.json（那份刻意不进 `--check`）。",
        "// ---------------------------------------------------------------------------",
        f"`ifndef {meta['guard']}",
        f"`define {meta['guard']}",
        "",
    ]
    for group, title in titles.items():
        entries = groups.get(group, [])
        if not entries:
            continue
        lines.append(f"// ---- {title} ----")
        for entry in entries:
            comment = f"  // {entry['grade']}: {entry['source']}"
            lines.append(
                f"localparam [{entry['width'] - 1}:0] {entry['name']}"
                f" = {entry['width']}'d{entry['value']};{comment}"
            )
        lines.append("")
    lines.append(f"`endif  // {meta['guard']}")
    lines.append("")
    return "\n".join(lines)


def _payload_sha(params: list[dict[str, Any]]) -> str:
    """Return the SHA256 of the canonical parameter payload."""
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ----------------------------------------------------------------------
# 产物：黄金激励
# ----------------------------------------------------------------------
def _stimulus_files(
    cfg: Config, result: Any, data: Any, limit: int, name: str
) -> tuple[str, str, dict[str, Any]]:
    """Render the golden stimulus and expected-code streams as fixed-width hex text.

    Args:
        cfg: Resolved configuration that produced ``result``.
        result: A :class:`adi_model.sim.SimResult`.
        data: The :class:`~adi_model.weight_calibration.DigitalObservation` built from
            ``result``. The known injection is read from it rather than re-derived, so
            the stimulus cannot disagree with what the reconstruction actually consumed.
        limit: Number of leading samples to emit.
        name: Config label used in the file header.

    Returns:
        ``(stimulus_text, expected_text, summary)``. ``summary`` records the sample
        count, the accumulator peak and the clipping/overflow counts so the two files
        cannot be silently paired with the wrong register image.

    Raises:
        ValueError: If the RDAC command is not integral (fractional masks have no
            accepted integer interface — ADR 0014) or if the known injection overflows
            its declared voltage register (contract §4.2).
    """
    n = min(int(limit), len(result.k))
    k = np.asarray(result.k[:n], dtype=float)
    if not np.allclose(k, np.rint(k), atol=0.0, rtol=0.0):
        raise ValueError("RDAC command is fractional; no accepted integer interface (ADR 0014)")
    k_i = np.rint(k).astype(np.int64)
    sid = np.asarray(result.sid[:n], dtype=np.int64)
    bank = np.asarray(result.bank[:n], dtype=np.int64)
    adc2 = np.asarray(result.adc2_code[:n], dtype=np.int64)
    slices = np.asarray(result.conv_slice_ids[:n], dtype=np.int64)

    fmt = FixedPointFormat()
    vscale = 2**fmt.voltage_fraction_bits
    limit_q = 2 ** (fmt.voltage_bits - 1)
    normalized = np.asarray(data.common_injection_v[:n], dtype=float) / float(cfg.v_fs)
    inj_q = np.rint(normalized * vscale).astype(np.int64)
    if any(not -limit_q <= int(x) < limit_q for x in inj_q):
        raise ValueError("digital injection exceeds its voltage register (contract §4.2)")

    stream = result.to_codes()
    code = np.asarray(stream.code[:n], dtype=np.int64)
    n_act = int(cfg.n_active)
    stim_header = [
        "// sar20 golden stimulus  (generated by tools/export_rtl_params.py)",
        f"// config={name}  samples={n}  encoding=fixed-width hex",
        f"// columns: k[{9}] sid[{9}] bank[{1}] adc2_code[{int(cfg.adc2_n_bits)}] inj_q[q32]"
        f" slice[{n_act}]",
        "// k        : RDAC unit-step command, integral (contract §3.2)",
        "// inj_q    : known injected voltage, V/Vfs, signed Q32 two's complement (16 hex digits)",
        "// slice[]  : converting physical slice IDs, one per active slice",
        "// 标记行 #DATA 之下的纯数字数据；解析方必须跳过它自己（与 P1 向量同一约定）。",
        "#DATA",
    ]
    exp_header = [
        "// sar20 expected output  (generated by tools/export_rtl_params.py)",
        f"// config={name}  samples={n}  encoding=fixed-width hex",
        "// columns: code[20b offset-binary] clip_low clip_high analog_ovf",
        "#DATA",
    ]

    stim_lines = list(stim_header)
    exp_lines = list(exp_header)
    for i in range(n):
        stim_lines.append(
            f"{k_i[i] & 0x1FF:03x} {sid[i] & 0x1FF:03x} {bank[i] & 0x1:01x} "
            f"{adc2[i] & 0xFFF:03x} {int(inj_q[i]) & 0xFFFFFFFFFFFFFFFF:016x} "
            + " ".join(f"{int(s) & 0x1F:02x}" for s in slices[i])
        )
        exp_lines.append(
            f"{code[i] & 0xFFFFF:05x} {int(stream.clipped_low[i])} "
            f"{int(stream.clipped_high[i])} {int(stream.analog_overflow[i])}"
        )

    summary = {
        "samples": n,
        "integral_rdac_command": True,
        "peak_accumulator_bits": int(stream.peak_accumulator_bits),
        "analog_overflow_count": int(np.count_nonzero(stream.analog_overflow[:n])),
        "clipped_count": int(np.count_nonzero(stream.clipped_low[:n] | stream.clipped_high[:n])),
    }
    return "\n".join(stim_lines) + "\n", "\n".join(exp_lines) + "\n", summary


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def build(
    cfg_name: str,
    *,
    phases: int,
    dither_mode: str | None,
    stimulus: int,
    n_image: int,
    invocation: str = "",
) -> dict[str, Any]:
    """Produce every artifact in memory, without touching the filesystem.

    Args:
        cfg_name: Key of :data:`_CONFIG_FACTORIES`.
        phases: RTL phase-tick count per cycle (graded ASSUMED).
        dither_mode: Optional override of ``cfg.dither_mode``.
        stimulus: Number of golden-stimulus samples; 0 disables the stimulus.
        n_image: Record length used to obtain the register image.
        invocation: Exact command line that reproduces these artifacts. Recorded in
            ``params.json`` so a reader never has to guess which flags produced them.

    Returns:
        ``{"vh": str, "params_json": str, "registers_json": str | None, ...}``
        plus the metadata used to build the report.

    Raises:
        KeyError: Unknown config name.
        ValueError: ``dither_mode`` outside the legal enum.
    """
    from dataclasses import replace

    from adi_model.pipeline import run_pipeline
    from adi_model.weight_calibration import DigitalObservation

    factory = _CONFIG_FACTORIES[cfg_name]
    cfg = factory()
    if dither_mode is not None:
        if dither_mode not in _DITHER_MODE_CODE:
            raise ValueError(f"unknown dither mode {dither_mode!r}")
        cfg = replace(cfg, dither_mode=dither_mode)
    cfg.check_legal()

    fmt = FixedPointFormat()
    params = rtl_localparams(cfg, fmt, phases)
    sha = _payload_sha(params)
    meta = {
        "config": cfg_name,
        "guard": "RTL_PARAMS_VH",
        # 刻意不放 revision / dirty：它们取决于运行环境而非 Config，
        # 写进受检产物会让"提交"这个动作本身把 --check 作废。见 export_manifest_*.json。
        "fixed_point": {
            "weight_fraction_bits": fmt.weight_fraction_bits,
            "coefficient_bits": fmt.coefficient_bits,
            "voltage_fraction_bits": fmt.voltage_fraction_bits,
            "voltage_bits": fmt.voltage_bits,
            "accumulator_bits": fmt.accumulator_bits,
            "output_bits": fmt.output_bits,
        },
        "payload_sha256": sha,
        "invocation": invocation or "unknown",
    }

    out: dict[str, Any] = {
        "meta": meta,
        "params": params,
        "vh": render_verilog_header(params, meta, sha),
        "analog": analog_references(cfg),
        "config_fields": config_field_entries(cfg),
        "skipped": [],
        "notes": [],
    }

    # 刻意**不**导出 dac_arch._A_MAIN / _A_SUB。它们是 dac_arch.SplitDAC 那条
    # 物理通路的 DEM 轮转常数（用于名义 LUT 的物理求值与串扰顺序），而 RTL 的
    # 开关译码必须跟随 dem.py 的 split_switch_command —— 后者才是
    # slice_pool.split_dac_voltage 实际调用的那一个。把两套常数一起交给 RTL，
    # 等于给它一次接错线的机会；如实登记在此，而不是静默省略。
    out["notes"].append(
        "dac_arch._A_MAIN/_A_SUB are deliberately not exported: they belong to the "
        "secondary physical path (dac_arch.SplitDAC nominal/physical evaluation and "
        "crosstalk ordering). The RTL switch decode must follow dem.split_switch_command, "
        "which is what slice_pool.split_dac_voltage actually calls."
    )

    # 寄存器镜像与激励都必须来自同一次真实运行 —— 不复制 from_result 的名义分支，
    # 避免留下第二份会漂移的权重推导（见计划 §9.1 硬约束 ①）。
    n_run = max(int(n_image), int(stimulus), 2)
    register_image: dict[str, Any] | None = None
    if cfg.dac_arch != "split":
        out["skipped"].append(
            "registers.json / stimulus: CalibrationSpec requires dac_arch='split'; "
            f"this config is {cfg.dac_arch!r}"
        )
    else:
        result = run_pipeline(
            cfg,
            sine_input(
                GOLDEN_AMPLITUDE_FRAC * cfg.v_fs, cfg.fs * GOLDEN_TONE_NUM / GOLDEN_TONE_DEN
            ),
            n_run,
        )
        decoder = FixedPointReconstructor.from_result(result, format=fmt)
        register_image = decoder.to_dict()
        # 独立通道：镜像必须能被重新装载并复现同一输出（ADR 0014 的不可变镜像要求）。
        restored = FixedPointReconstructor.from_dict(
            json.loads(json.dumps(register_image, allow_nan=False))
        )
        data = DigitalObservation.from_result(result)
        probe = decoder.reconstruct(data).code
        if not np.array_equal(restored.reconstruct(data).code, probe):
            raise AssertionError("register image does not round-trip to identical words")
        out["registers"] = register_image
        out["register_probe"] = {
            "record_length": int(n_run),
            "weights_shape": list(decoder.weights_q.shape),
            "max_weight": int(decoder.weights_q.max()),
            "sum_weights": int(decoder.weights_q.sum()),
            "adc2_min_q": int(decoder.adc2_min_q),
            "adc2_max_q": int(decoder.adc2_max_q),
            "offset_q": int(decoder.offset_q),
        }
        if stimulus > 0:
            stim_text, exp_text, summary = _stimulus_files(cfg, result, data, stimulus, cfg_name)
            summary["register_payload_sha256"] = sha
            out["stimulus_text"] = stim_text
            out["expected_text"] = exp_text
            out["stimulus_summary"] = summary

    out["params_json"] = _json_text(
        {
            "schema_version": 1,
            "generator": "tools/export_rtl_params.py",
            "meta": meta,
            "localparams": params,
            "analog_references": out["analog"],
            "config_fields": out["config_fields"],
            "register_probe": out.get("register_probe"),
            "stimulus_summary": out.get("stimulus_summary"),
            "skipped": out["skipped"],
            "notes": out["notes"],
            "grade_propagation": {
                "rule": "derived quantities take the weakest input grade",
                "order": [
                    g.value for g in sorted(_GRADE_SEVERITY, key=lambda k: _GRADE_SEVERITY[k])
                ],
            },
        }
    )
    if register_image is not None:
        out["registers_json"] = _json_text(register_image)
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the exporter.

    Args:
        argv: Command-line arguments; ``None`` means ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 on success, 1 when ``--check`` reports drift.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="paper_literal", choices=sorted(_CONFIG_FACTORIES))
    ap.add_argument("--out-rtl", default="rtl/params", help="directory for rtl_params.vh")
    ap.add_argument("--out-vectors", default="sim/vectors", help="directory for JSON + hex")
    ap.add_argument("--phases", type=int, default=16, help="phase ticks per conversion [ASSUMED]")
    ap.add_argument("--dither-mode", default=None, choices=sorted(_DITHER_MODE_CODE))
    ap.add_argument("--emit-stimulus", type=int, default=0, help="golden-stimulus sample count")
    ap.add_argument(
        "--image-samples", type=int, default=32, help="record length for the register image"
    )
    ap.add_argument("--check", action="store_true", help="fail if on-disk artifacts differ")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    # 记录**决定数值内容**的选项，而不是原始 argv：argv 里混进 --check / --quiet /
    # 输出目录会让"可复现命令"随检查方式变化，从而让 --check 自己把自己判为漂移。
    invocation = f"python tools/export_rtl_params.py --config {args.config} --phases {args.phases}"
    invocation += f" --image-samples {args.image_samples} --emit-stimulus {args.emit_stimulus}"
    if args.dither_mode is not None:
        invocation += f" --dither-mode {args.dither_mode}"
    built = build(
        args.config,
        phases=args.phases,
        dither_mode=args.dither_mode,
        stimulus=args.emit_stimulus,
        n_image=args.image_samples,
        invocation=invocation,
    )

    stem = f"{args.config}" + (f"_{args.dither_mode}" if args.dither_mode else "")
    targets: list[tuple[Path, str]] = [
        (root / args.out_rtl / "rtl_params.vh", built["vh"]),
        (root / args.out_vectors / f"params_{stem}.json", built["params_json"]),
    ]
    if "registers_json" in built:
        targets.append(
            (root / args.out_vectors / f"registers_{stem}.json", built["registers_json"])
        )
    if "stimulus_text" in built:
        targets.append((root / args.out_vectors / f"stimulus_{stem}.hex", built["stimulus_text"]))
        targets.append((root / args.out_vectors / f"expected_{stem}.hex", built["expected_text"]))

    drift: list[str] = []
    for path, text in targets:
        _write(path, text, check=args.check, drift=drift)

    # manifest 只记身份与哈希，**不在 targets 里**：它不进 --check（理由见其 why_not_checked）。
    # 也刻意只在写模式产出：--check 必须做到零副作用，否则门禁自己会改工作树。
    manifest_path = root / args.out_vectors / f"export_manifest_{stem}.json"
    identity = repo_identity(root)
    if not args.check:
        manifest = manifest_text(
            stem,
            invocation=invocation,
            identity=identity,
            artifacts={_display(path, root).replace("\\", "/"): text for path, text in targets},
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(manifest, encoding="utf-8", newline="\n")

    if not args.quiet:
        print(f"[export_rtl_params] config={args.config} stem={stem}")
        print(f"  rtl_params.vh payload sha256 = {built['meta']['payload_sha256']}")
        print(
            f"  worktree = {identity['describe']} ({identity['commit']}) "
            f"dirty={identity['dirty']}  -> {_display(manifest_path, root)}"
        )
        for path, _ in targets:
            state = "DRIFT" if str(path) in drift else ("checked" if args.check else "written")
            print(f"  [{state}] {_display(path, root)}")
        print(
            f"  [{'skipped (--check)' if args.check else 'written'}] "
            f"{_display(manifest_path, root)}  -- manifest 刻意不进 --check"
        )
        for note in built["skipped"]:
            print(f"  [skipped] {note}")
        if "stimulus_summary" in built:
            s = built["stimulus_summary"]
            print(
                f"  stimulus: n={s['samples']} peak_acc_bits={s['peak_accumulator_bits']} "
                f"analog_ovf={s['analog_overflow_count']} clipped={s['clipped_count']}"
            )

    if drift:
        print(
            f"[export_rtl_params] DRIFT: {len(drift)} artifact(s) differ from source",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
