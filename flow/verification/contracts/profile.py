# =============================================================================
# 文件名     : profile.py
# 模块名     : verification.contracts.profile
# 版本       : v1.0
# 功能       : RTL 侧参数 profile 的**单一真源**，供向量生成与契约校验共用。
#
# 设计说明   :
#   * 为什么需要这个文件：
#     2026-11-13 链闭合审计发现，同一组 RTL 参数被**硬编码在三处**：
#         verification/contracts/rtl_params.vh        (localparam)
#         verification/contracts/digital_interfaces.yaml (profile 段)
#         verification/vectors/generate.py::PROFILE    (Python dict)
#     三者曾**一致地**停在旧的 63+8 / 12-bit 几何上，而主链已迁到 17/5 / 14-bit。
#     多副本 + 无校验 = 静默漂移。本文件把值收到一处，另两处由测试比对。
#
#   * 为什么不是直接改成主链的 17/5：
#     RTL 侧改动**无法在本机验证**（无 VCS / Verilator）。
#     把契约改成 17/5 会产出"看起来对、实际从未跑过 RTL"的向量——
#     那正是本仓库明令禁止的"用未验证物代替验证"。
#     因此本轮只做**可验证的一半**：消除多副本、并把偏差变成显式失败（见
#     `tests/unit/test_rtl_profile_consistency.py` 的 xfail 标记）。
#
#   * 与主链的关系：
#     主链（`src/adi_model`）的几何由 `DefaultParameters` 定义，是另一个真源。
#     两侧**当前不一致**（RTL 63/8/12 vs 主链 17/5/14），这是链闭合表里的 B1。
#     本文件不假装它们一致；一致性由测试显式断言并在 B1 未修前 xfail。
#
# 变更记录   : [新增] 2026-11-13 从 generate.py::PROFILE 抽出，作为单一真源。
# =============================================================================

"""RTL 侧参数 profile 的单一真源。

**当前值仍是 63+8 / 12-bit**（与已归档的 V1 向量一致）。主链已是 17/5 / 14-bit，
两者不一致 ⇒ 链闭合表 B1。改这里必须同时：
  1. 重生成 `verification/vectors/generated/**`（`python -m verification.vectors.generate`）；
  2. 在有仿真器的环境重跑 V1，确认 RTL 在 17/5 / 14-bit 下仍位真；
  3. 同步 `rtl_params.vh` 与 `digital_interfaces.yaml`（测试会检查）。
"""

from __future__ import annotations

from typing import Final

#: RTL 侧唯一真源。键名与 `rtl_params.vh` 的 localparam 一一对应。
RTL_PROFILE: Final[dict[str, int]] = {
    "B1": 9,
    "ADC2_BITS": 12,
    "N_ACTIVE": 8,
    "N_UNIT_MAIN": 63,
    "N_UNIT_SUB": 8,
    "N_SLICES": 18,
    "W_BITS": 48,
    "V_BITS": 64,
    "OUT_BITS": 20,
}

#: 主链（`src/adi_model`）当前采用的几何，仅用于比对，不是 RTL 的真源。
MAIN_CHAIN_PROFILE: Final[dict[str, int]] = {
    "B1": 9,
    "ADC2_BITS": 14,
    "N_ACTIVE": 8,
    "N_UNIT_MAIN": 17,
    "N_UNIT_SUB": 5,
    "N_SLICES": 18,
    "W_BITS": 48,
    "V_BITS": 64,
    "OUT_BITS": 20,
}

#: 两侧不一致的键（B1 的精确内容）；B1 修好后此集合应为空。
DIVERGENT_KEYS: Final[frozenset[str]] = frozenset(
    key for key in RTL_PROFILE if RTL_PROFILE[key] != MAIN_CHAIN_PROFILE[key]
)
