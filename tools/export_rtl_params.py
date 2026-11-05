#!/usr/bin/env python3
"""将主线 Config 导出为 RTL 参数、定点寄存器及可选参考向量（P0）。

只转录现有模型，不生成 RTL 逻辑或另建物理模型。数值格式来源为 ADR 0014、
fixed_point.py 和 RTL_ARITHMETIC_CONTRACT.md；来源分级读取 provenance，
阵列及 DEM 几何读取 mapper、dem、dac_arch。

rtl_params.vh 只包含无量纲整数：位宽、计数、状态数、旋转常数和模式码。
fs、v_fs、delta1 等模拟量只写入 params.json，其有限浮点值用 num/2**den_exp
精确表示，附带 float 仅供阅读。inj_q 为归一化 V/Vfs 的有符号 Q32，其他向量
字段为整数码；列格式由文件头及 test_rtl_export.py 核对。

直接字段采用实际值的 effective_grade，另记 declared_grade；派生量按最弱
输入传播，次序为 DISCLOSED、DERIVED、FITTED、ASSUMED、RESEARCH_EXTENSION，
算术结果至少为 DERIVED。格式、LCG、编码和 PHASES 等 RTL 选择至少为 ASSUMED，
各项保留 input_fields，不能把工程选择标成原文披露。

受 --check 检查的 .vh、参数／寄存器 JSON 和激励／期望 hex 只依赖 Config，
不写入 revision、dirty、describe 或提交 SHA。环境身份及各文件摘要单独记录于
export_manifest_<stem>.json，旁车清单不参与 --check。这样提交本身不会使参数
产物漂移；同一配置应生成相同字节。检查仅归一化 CRLF 为 LF，其他内容仍逐字比较。

定点输出只支持 split；unary 缺少 CalibrationSpec 时只导出参数与 .vh，报告
明确记下寄存器及激励跳过。可用 --config paper_literal --emit-stimulus 256
生成指定记录，--check 比较现有产物并在漂移时返回非零。
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
    """将有限浮点值表示为精确的二进有理数。

    Args:
        x: 有限浮点值。

    Returns:
        包含 num、den_exp、float 的映射，严格满足 x=num/2**den_exp。
        float 仅供阅读，不用于下游计算。
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
    """取得输入中最弱的来源等级。

    Args:
        grades: 非空来源等级序列。

    Returns:
        严重程度最大、最不适合引用的等级。

    Raises:
        ValueError: 输入为空。
    """
    if not grades:
        raise ValueError("derived quantities must declare at least one input")
    return max(grades, key=lambda g: _GRADE_SEVERITY[g])


def repo_identity(root: Path) -> dict[str, str]:
    """读取工作树 Git 身份，未知项显式写为 unknown。

    仅供 export_manifest_<stem>.json 使用，不进入受检参数产物，避免提交本身
    改变 --check 的结果。

    Args:
        root: 含 Git 身份的仓库根路径。

    Returns:
        commit、describe、dirty 字段；dirty 为字符串 0 或 1。
    """

    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                args, cwd=root, capture_output=True, text=True, check=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    dirty = _run(["git", "status", "--porcelain"])
    return {
        "commit": _run(["git", "rev-parse", "HEAD"]) or "unknown",
        "describe": _run(["git", "describe", "--tags", "--always"]) or "unknown",
        "dirty": "unknown" if dirty is None else ("1" if dirty else "0"),
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
    """生成记录运行环境的旁车清单，不参与 --check。

    Args:
        stem: 产物名称前缀，形式为 <config>[_<dither_mode>]。
        invocation: 产生这些文件的完整调用命令。
        identity: repo_identity 返回的仓库身份。
        artifacts: 仓库相对路径到文件文本的映射。

    Returns:
        包含身份及逐文件 SHA256 的 JSON 文本。
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
    """确定性序列化 JSON，拒绝 NaN 和 Infinity。"""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _display(path: Path, root: Path) -> str:
    """能相对 root 表示时返回相对路径，否则返回绝对路径。"""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _normalize_eol(text: str) -> str:
    """只将 CRLF 归一为 LF，其他字节不变，避免检出换行策略被误判为内容漂移。"""
    return text.replace("\r\n", "\n")


def _write(path: Path, text: str, *, check: bool, drift: list[str]) -> bool:
    """写入文本，或在 check 模式记录漂移。

    Returns:
        产物当前或已写入时为 True；存在内容漂移时为 False。
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
    """生成 RTL 使用的无量纲整数常量及来源记录。

    Args:
        cfg: 已解析配置。
        fmt: ADR 0014 的固定数值格式。
        phases: 每转换周期的 RTL 相位计数，属于标为 ASSUMED 的实现选择。

    Returns:
        逐项 name、value、width、group、grade、source 及 input_fields；直接字段
        继承实际值等级，算术推导不强于其输入。

    Raises:
        ValueError: 数值超出声明位宽，或 units_per_lsb1 与 dac_levels//2**b1 不一致。
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

    direct_fields = {
        "N_SLICES": "n_slices",
        "N_ACTIVE": "n_active",
        "N_UNIT_MAIN": "dac_n_main",
        "N_UNIT_SUB": "dac_n_sub",
        "DAC_COMPLETE_RANGE": "dac_complete_range",
        "B1": "b1",
        "N_UNITS_HEADROOM": "n_units_headroom",
        "ADC2_BITS": "adc2_n_bits",
        "DEM_SUB_ROLL": "dac_n_sub",
        "DEM_ENABLE": "dem_enable",
        "DEM_BRIDGE_ENABLE": "dem_bridge_enable",
        "DITHER_DISCRETE": "dither_discrete",
    }
    signal_inputs = ("n_active", "n_unit_per_slice", "n_units_headroom")
    grid_inputs = (*signal_inputs, "b1")
    derived_fields = {
        "N_UNIT_TOTAL": ("dac_n_main", "dac_n_sub"),
        "DAC_LEVELS": (
            ("dac_arch", "n_active", "n_unit_per_slice")
            if cfg.dac_arch == "unary"
            else ("dac_arch", "dac_n_main", "dac_complete_range", "dac_n_sub")
        ),
        "STAGE1_LEVELS": ("b1",),
        "N_UNITS_SIG": signal_inputs,
        "UNITS_PER_LSB1": grid_inputs,
        "UNITS_PER_D1": grid_inputs,
        "K0": ("n_units_headroom",),
        "DEM_ROT_WIDTH": ("dac_n_main",),
        "DEM_ROT_HEIGHT": ("dac_n_main",),
        "DITHER_UNITS_TOTAL": ("dither_mode", "dither_units_range"),
        "DITHER_UNITS_RANGE": ("dither_units_range",),
    }
    # These values are implementation choices rather than published ADC facts.
    # Qualified names identify non-Config inputs in the exported dependency list.
    rtl_choices = {
        "W_FRAC": ("format.weight_fraction_bits",),
        "W_BITS": ("format.coefficient_bits",),
        "V_FRAC": ("format.voltage_fraction_bits",),
        "V_BITS": ("format.voltage_bits",),
        "ACC_BITS": ("format.accumulator_bits",),
        "OUT_BITS": ("format.output_bits",),
        "DEM_STATES": ("mapper.N_DEM_STATES",),
        "DEM_LCG_A": ("mapper._LCG_A",),
        "DEM_LCG_A_MOD": ("mapper._LCG_A", "mapper.N_DEM_STATES"),
        "DITHER_MODE": ("dither_mode",),
        "DITHER_SPLIT_IS_SUB": ("dither_split_bank",),
        "PHASES": ("phases",),
    }
    annotated = annotate_config(cfg)
    out: list[dict[str, Any]] = []
    for name, value, width, group, source in raw:
        # raw 的全部 value 在构造处即为 int（见 raw 的类型标注：fmt.* 由
        # FixedPointFormat.__post_init__ 保证为正 int，cfg.* 均经 int() 包裹，
        # rot_* 为 math.ceil 返回，phases 已在上文入口校验）——此处只需区间校验；
        # 若将来引入浮点项，须在此恢复有限性校验。
        if value < 0 or value >= 1 << width:
            raise ValueError(f"{name}={value} does not fit in {width} bits")
        if name in direct_fields:
            inputs = (direct_fields[name],)
            grade = annotated[inputs[0]].effective_grade
        elif name in derived_fields:
            inputs = derived_fields[name]
            grade = weakest_grade(
                [SourceGrade.DERIVED, *(annotated[field].effective_grade for field in inputs)]
            )
        elif name in rtl_choices:
            inputs = rtl_choices[name]
            grade = weakest_grade(
                [
                    SourceGrade.ASSUMED,
                    *(annotated[field].effective_grade for field in inputs if field in annotated),
                ]
            )
        else:
            raise ValueError(f"{name}: localparam provenance inputs are not registered")
        out.append(
            {
                "name": name,
                "value": int(value),
                "width": int(width),
                "group": group,
                "grade": grade.value,
                "source": source,
                "input_fields": list(inputs),
            }
        )
    return out


def analog_references(cfg: Config) -> list[dict[str, Any]]:
    """列出模拟侧参考量，不写入 .vh。

    Args:
        cfg: 已解析配置。

    Returns:
        各量的 name、exact 有理数、unit、依赖 input_fields 及传播后的 grade。
    """
    step_inputs = (
        ("dac_arch", "v_fs", "n_active", "n_unit_per_slice", "n_units_headroom")
        if cfg.dac_arch == "unary"
        else ("dac_arch", "v_fs", "dac_n_main", "dac_n_sub")
    )
    if cfg.dither_mode != "sampling":
        alpha_inputs = ("dither_mode",)
    elif cfg.dac_arch == "unary":
        alpha_inputs = (
            "dither_mode",
            "dither_units_range",
            "dac_arch",
            "n_active",
            "n_unit_per_slice",
        )
    else:
        # Follow the implemented charge formula, including dac_unit_cap inputs;
        # do not silently drop fields because common scale factors cancel.
        alpha_inputs = (
            "dither_mode",
            "dither_units_range",
            "dac_arch",
            "dither_split_bank",
            "dac_n_main",
            "dac_n_sub",
            "dac_parasitic_ratio",
            "c_total0",
            "cap_scale",
        )
    if not cfg.ktc_enable:
        kappa_inputs = beta_inputs = ("ktc_enable",)
    else:
        kappa_inputs = (
            ("ktc_enable", "ktc_kappa", "ktc_gain_n", "g0")
            if cfg.ktc_kappa is None
            else ("ktc_enable", "ktc_kappa")
        )
        beta_inputs = tuple(
            dict.fromkeys(("ktc_enable", "ktc_beta_error", *kappa_inputs, "ktc_gain_n", "g0"))
        )
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
            step_inputs,
        ),
        ("ADC2_STEP", float(cfg.delta2), "V", ("adc2_v_min", "adc2_v_max", "adc2_n_bits")),
        ("G0", float(cfg.g0), "1", ("g0",)),
        ("KAPPA_EFF", float(cfg.kappa_eff()), "1", kappa_inputs),
        (
            "BETA_EFF",
            float(cfg.beta_eff()),
            "1",
            beta_inputs,
        ),
        (
            "DITHER_ALPHA",
            float(cfg.dither_alpha),
            "1",
            alpha_inputs,
        ),
        ("RA_V_CLIP", float(cfg.ra_v_clip), "V", ("ra_v_clip",)),
        (
            "C_ACTIVE_NOMINAL",
            float(cfg.c_active_nominal()),
            "F",
            ("c_total0", "cap_scale", "n_active", "n_unit_per_slice", "n_units_headroom"),
        ),
    ]

    annotated = annotate_config(cfg)
    direct_names = {"FS", "V_FS", "N_BITS_TARGET", "G0", "RA_V_CLIP"}
    out: list[dict[str, Any]] = []
    for name, value, unit, inputs in items:
        grades = []
        sources = []
        for field in inputs:
            graded = annotated[field]
            grades.append(graded.effective_grade)
            sources.append(f"{field}: {graded.source}")
        grade = weakest_grade(grades if name in direct_names else [SourceGrade.DERIVED, *grades])
        out.append(
            {
                "name": name,
                "exact": exact_rational(value),
                "unit": unit,
                "grade": grade.value,
                "grade_label": grade.label_zh,
                "inputs": list(inputs),
                "input_fields": list(inputs),
                "sources": sources,
                "note": (
                    "direct Config value: effective grade"
                    if name in direct_names
                    else "derived: weakest effective input grade (see exporter docstring)"
                ),
            }
        )
    return out


def config_field_entries(cfg: Config) -> dict[str, Any]:
    """导出全部 Config 字段及其来源。

    使用 Config.to_dict() 序列化嵌套 conversion、input_network 等参数，避免另写
    一套序列化。嵌套块沿用父字段等级，不虚构未登记的内部成员等级。

    Returns:
        字段到 value、grade、declared_grade、grade_label、quotable、source、unit、
        note、overridden 的映射；实际值与原始声明的等级分别保留。
    """
    annotated = annotate_config(cfg)
    values = cfg.to_dict()
    out: dict[str, Any] = {}
    for name in sorted(values):
        graded = annotated[name]
        out[name] = {
            "value": values[name],
            "grade": graded.effective_grade.value,
            "declared_grade": graded.grade.value,
            "grade_label": graded.effective_grade.label_zh,
            "quotable": bool(graded.effective_grade.quotable),
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
    """生成以 LF 结尾的 rtl_params.vh 文本。

    头部只记录由 Config 决定的配置名、格式、参数载荷摘要及合同指针；不写随
    提交变化的 Git revision／dirty。

    Args:
        params: rtl_localparams 返回的条目。
        meta: 配置名、数值格式及 guard 等头部元数据。
        payload_sha: 已渲染参数块的 SHA256。

    Returns:
        完整头文件文本。
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
    """计算规范化参数载荷的 SHA256。"""
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ----------------------------------------------------------------------
# 产物：黄金激励
# ----------------------------------------------------------------------
def _stimulus_files(
    cfg: Config, result: Any, data: Any, limit: int, name: str
) -> tuple[str, str, dict[str, Any]]:
    """将模型激励及期望码流转录为定宽十六进制文本。

    已知注入直接读取 DigitalObservation，避免再次推导后与重构所消费的量不同。

    Args:
        cfg: 生成 result 的已解析配置。
        result: 模型 SimResult。
        data: 从 result 构造的 DigitalObservation。
        limit: 导出开头多少个样本。
        name: 文件头使用的配置名称。

    Returns:
        激励文本、期望文本及含样本数、累加峰值、裁剪和溢出计数的摘要。

    Raises:
        ValueError: RDAC 命令非整数，或已知注入超出声明电压寄存器范围。
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
    """在内存生成全部产物，不写文件。

    Args:
        cfg_name: _CONFIG_FACTORIES 的配置键。
        phases: 标为 ASSUMED 的每周期 RTL 相位数。
        dither_mode: 可选扰动模式覆盖。
        stimulus: 参考激励样本数，0 表示不生成激励。
        n_image: 获取寄存器镜像的记录长度。
        invocation: 可复现本次产物的完整命令，记录于参数 JSON。

    Returns:
        vh、params_json、可为空的 registers_json 等文本，以及报告元数据。

    Raises:
        KeyError: 配置名称未知。
        ValueError: 扰动模式不在合法枚举中。
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
            # Bind the stimulus to the exact register image it was generated
            # with.  The parameter payload hash above does not cover weights.
            summary["register_payload_sha256"] = hashlib.sha256(
                _json_text(register_image).encode("utf-8")
            ).hexdigest()
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
    """执行主线参数导出入口。

    Args:
        argv: 命令行参数；None 表示 sys.argv[1:]。

    Returns:
        成功为 0；--check 发现漂移时为 1。
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
