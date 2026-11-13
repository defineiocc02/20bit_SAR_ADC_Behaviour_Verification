"""RTL 侧 profile 必须只有**一个真源**，且与主链的偏差必须显式。

## 背景（链闭合表 B1）

2026-11-13 审计发现：同一组 RTL 参数被硬编码在**三处**：

| 位置 | 形态 |
|:---|:---|
| `verification/contracts/rtl_params.vh` | `localparam int unsigned` |
| `verification/contracts/digital_interfaces.yaml` | `profile:` 段 |
| `verification/vectors/generate.py::PROFILE` | Python dict |

三者曾**一致地**停在旧几何 63+8 / 12-bit，而主链已迁到 17/5 / 14-bit。
多副本 + 无校验 = 静默漂移，且没有任何测试会发现。

## 本文件做两件事

1. **消除多副本**：`verification/contracts/profile.py::RTL_PROFILE` 是唯一真源，
   另两处必须与它逐键相同（这三个测试**必须通过**）；
2. **把与主链的偏差变成显式失败**：`test_rtl_profile_matches_the_main_chain`
   在 B1 未修前是被 `xfail(strict=True)` 标记的——
   它**当前会失败**，这是诚实的；一旦有人把 RTL 改成 17/5，
   strict xfail 会报 XPASS 并提醒移除标记。

## 为什么不直接把 RTL 改成 17/5

RTL 侧改动**在本机无法验证**（无 VCS / Verilator）。
把契约改成 17/5 会产出"看起来对、实际从未跑过 RTL"的向量——
那正是本仓库禁止的"用未验证物代替验证"。
必须先在**有仿真器的环境**确认 RTL 在 17/5 / 14-bit 下仍位真，才能改。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from verification.contracts.profile import (
    DIVERGENT_KEYS,
    MAIN_CHAIN_PROFILE,
    RTL_PROFILE,
)

ROOT = Path(__file__).resolve().parents[2]


def test_rtl_params_vh_matches_the_single_source():
    """`rtl_params.vh` 的 localparam 必须与 `RTL_PROFILE` 逐键相同。"""
    text = (ROOT / "verification/contracts/rtl_params.vh").read_text(encoding="utf-8")
    declared = {
        name: int(value)
        for name, value in re.findall(r"localparam int unsigned (\w+) = 32'd(\d+);", text)
    }
    missing = [key for key in RTL_PROFILE if key not in declared]
    assert not missing, f"rtl_params.vh 缺少这些键: {missing}"
    mismatched = {
        key: (RTL_PROFILE[key], declared[key])
        for key in RTL_PROFILE
        if declared[key] != RTL_PROFILE[key]
    }
    assert not mismatched, (
        "rtl_params.vh 与单一真源不一致 (真源值, 文件值): "
        + repr(mismatched)
        + "；请改真源后同步本文件，或反之"
    )


def test_digital_interfaces_yaml_matches_the_single_source():
    """`digital_interfaces.yaml` 的 `profile:` 段必须与 `RTL_PROFILE` 逐键相同。"""
    text = (ROOT / "verification/contracts/digital_interfaces.yaml").read_text(encoding="utf-8")
    # 结构是：`profile: adi_40m` 后接 `params:`，参数在 params 块内。
    block = re.search(r"^params:\s*\n((?:\s+\w+:\s*\d+\s*\n)+)", text, re.MULTILINE)
    assert block, "digital_interfaces.yaml 里找不到 params: 段"
    declared = {
        name: int(value) for name, value in re.findall(r"(\w+):\s*(\d+)", block.group(1))
    }
    mismatched = {
        key: (RTL_PROFILE[key], declared[key])
        for key in RTL_PROFILE
        if key in declared and declared[key] != RTL_PROFILE[key]
    }
    # 派生量 N_UNIT_TOTAL 必须等于主+子，否则宽度声明会自相矛盾
    if "N_UNIT_TOTAL" in declared:
        expected_total = RTL_PROFILE["N_UNIT_MAIN"] + RTL_PROFILE["N_UNIT_SUB"]
        assert declared["N_UNIT_TOTAL"] == expected_total, (
            f"N_UNIT_TOTAL={declared['N_UNIT_TOTAL']} 与 "
            f"N_UNIT_MAIN+N_UNIT_SUB={expected_total} 不一致"
        )
    assert not mismatched, (
        "digital_interfaces.yaml 的 profile 与单一真源不一致 (真源值, 文件值): "
        + repr(mismatched)
    )
    # profile 段还必须写全所有键，不能漏
    missing = [key for key in RTL_PROFILE if key not in declared]
    assert not missing, f"digital_interfaces.yaml 的 profile 缺少这些键: {missing}"


def test_vector_generator_uses_the_single_source():
    """`generate.py` 不得再自己维护一份 PROFILE 副本。"""
    text = (ROOT / "verification/vectors/generate.py").read_text(encoding="utf-8")
    assert "from verification.contracts.profile import" in text, (
        "generate.py 应当从单一真源导入 PROFILE，而不是硬编码一份副本"
    )
    # 不得再出现字面量字典定义
    assert not re.search(r"^PROFILE\s*=\s*\{", text, re.MULTILINE), (
        "generate.py 里仍有一份 PROFILE 字面量副本；请改为导入单一真源"
    )


def test_the_divergence_set_matches_the_documented_breakpoint():
    """偏差集合必须与链闭合表 B1 记录的三个键一致。

    B1 记录：RTL 是 63/8/12、主链是 17/5/14 ⇒ 恰好这三项不同。
    若将来有人只改了其中一项（例如只把 ADC2_BITS 调到 14），
    这个测试会失败并指出"改了一半"。
    """
    assert DIVERGENT_KEYS == frozenset({"ADC2_BITS", "N_UNIT_MAIN", "N_UNIT_SUB"}), (
        f"偏差键集合变成了 {sorted(DIVERGENT_KEYS)}；"
        "若 B1 正在被修复，请同步更新链闭合表与 RTL_PROFILE"
    )


@pytest.mark.xfail(
    strict=True,
    reason="B1：RTL 契约仍是 63+8/12-bit，主链是 17/5/14-bit；"
    "修它需要先在有仿真器的环境验证 RTL 在 17/5 下仍位真",
)
def test_rtl_profile_matches_the_main_chain():
    """**B1 的判据**：RTL 侧 profile 必须与主链逐键相同。

    当前**故意失败**（xfail strict）。修复路径：
    1. 在有 VCS/Verilator 的环境把 `rtl_params.vh` 改成 17/5/14 并重跑 V1；
    2. 确认 7 个模块仍位真（尤其 `unit_therm` 的位宽与 `adc2_dec` 的 14 位支持）；
    3. 同步 `RTL_PROFILE` 与 `digital_interfaces.yaml`、重生成向量；
    4. 移除本 xfail 标记。
    """
    assert RTL_PROFILE == MAIN_CHAIN_PROFILE


def test_main_chain_profile_matches_default_model():
    """`MAIN_CHAIN_PROFILE` 必须反映 `DefaultParameters` 的真实值。

    防止这个"比对用常量"自己漂移，变成另一个假真源。
    """
    from adi_model.defaults import DefaultParameters

    parameters = DefaultParameters()
    assert MAIN_CHAIN_PROFILE["N_UNIT_MAIN"] == parameters.n_main
    assert MAIN_CHAIN_PROFILE["N_UNIT_SUB"] == parameters.n_sub
    assert MAIN_CHAIN_PROFILE["ADC2_BITS"] == parameters.adc2_n_bits
