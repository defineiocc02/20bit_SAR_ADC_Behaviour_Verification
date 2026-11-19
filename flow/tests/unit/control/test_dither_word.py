# =============================================================================
# 文件名     : test_dither_word.py
# 模块名     : test_dither_word_gen
# 版本       : v1.0
# 功能       : 六位 dither 字发生器的采样与复位契约。
#
# 设计说明   :
#   * 承担 八片模型与归档审计 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""六位 dither 字发生器的采样与复位契约。"""

import pytest

from adi_model.control.dither_word import DEFAULT_DITHER_SEED, DitherWordGenerator, DitherWordOutput


# =========================================================================
# 1. 生产字序列、外部覆盖与空拍
# =========================================================================
# -------------------------------------------------------------------------
# 已知答案直接列出完整状态和低六位字，不能拿同一函数同时生成输入与期望。
# 停顿不消耗状态。已受理的强制字只覆盖该笔输出，后续样本编号不偏移。
# 零种子或同沿冲突发错误，不发布样本，也不消耗状态。
# -------------------------------------------------------------------------
def test_word_order_and_back_to_back_samples() -> None:
    """从合同规定的复位种子出发，两笔相邻事务使用连续的两个新状态。"""
    generator = DitherWordGenerator()
    assert generator.state.rng_state == 0xC001D00D

    first = generator.step(sample_accept=True, sample_id=0xFFFFFFFF)
    second = generator.step(sample_accept=True, sample_id=0)
    assert first == DitherWordOutput(
        word=45, word_valid=True, word_sample_id=0xFFFFFFFF, rng_state=0xBA01ACAD
    )
    assert second == DitherWordOutput(
        word=7, word_valid=True, word_sample_id=0, rng_state=0x7D1D2787
    )


def test_idle_holds_and_forced_samples_advance_once() -> None:
    """空拍保持；受理时强制字或关闭扰动均按一个样本推进。"""
    active = DitherWordGenerator()
    reference = DitherWordGenerator()
    first = active.step(sample_accept=True, sample_id=1)
    assert first == reference.step(sample_accept=True, sample_id=1)

    idle = active.step(sample_id=42)
    assert not idle.word_valid
    assert (idle.word, idle.word_sample_id, idle.rng_state) == (first.word, 1, first.rng_state)
    disabled = active.step(dither_enable=False, sample_accept=True, sample_id=2)
    second = reference.step(sample_accept=True, sample_id=2)
    assert disabled == DitherWordOutput(
        word_valid=True, word_sample_id=2, rng_state=second.rng_state
    )
    forced = active.step(sample_accept=True, sample_id=11, force_word=53)
    third = reference.step(sample_accept=True, sample_id=11)
    assert (forced.word, forced.rng_state) == (53, third.rng_state)
    assert active.step(sample_accept=True, sample_id=3) == reference.step(
        sample_accept=True, sample_id=3
    )


@pytest.mark.parametrize("invalid_word", [-1, 64])
def test_invalid_forced_word_does_not_change_state(invalid_word: int) -> None:
    """软件显式拒绝六位口无法表达的值，拒绝前后状态须完全相同。"""
    generator = DitherWordGenerator()
    before = generator.state
    with pytest.raises(ValueError, match="6 bits"):
        generator.step(sample_accept=True, force_word=invalid_word)
    assert generator.state == before


@pytest.mark.parametrize(
    ("seed", "sample_accept", "dither_enable"),
    [(0, False, True), (1, True, True), (1, True, False)],
)
def test_illegal_seed_keeps_transaction_state(
    seed: int, sample_accept: bool, dither_enable: bool
) -> None:
    """零种子或同沿冲突发错误，不发布样本，也不消耗状态。"""
    generator = DitherWordGenerator()
    prior = generator.step(sample_accept=True, sample_id=12)
    rejected = generator.step(
        seed_load=True,
        seed=seed,
        sample_accept=sample_accept,
        sample_id=13,
        dither_enable=dither_enable,
    )
    assert rejected == DitherWordOutput(prior.word, False, 12, prior.rng_state, True)
    assert not generator.step().seed_error


# =========================================================================
# 2. 函数组：test_reset_and_clear_take_precedence / test_python_rejects_unrepresentable_port_values
# =========================================================================
# -------------------------------------------------------------------------
# 同步复位或清除覆盖写种子冲突，并恢复已知序列。
# 软件接口不截断无法由硬件端口表示的输入。
# -------------------------------------------------------------------------
@pytest.mark.parametrize("reset", [{"rst_n": False}, {"clear": True}])
def test_reset_and_clear_take_precedence(reset: dict[str, bool]) -> None:
    """同步复位或清除覆盖写种子冲突，并恢复已知序列。"""
    generator = DitherWordGenerator()
    generator.step(seed_load=True, seed=1)
    generator.step(sample_accept=True, sample_id=90)
    observed = generator.step(seed_load=True, seed=0, sample_accept=True, sample_id=91, **reset)
    assert observed == DitherWordOutput(rng_state=DEFAULT_DITHER_SEED)
    assert generator.step(sample_accept=True) == DitherWordGenerator().step(sample_accept=True)


@pytest.mark.parametrize(("seed", "sample_id"), [(-1, 0), (1 << 32, 0), (0, -1), (0, 1 << 32)])
def test_python_rejects_unrepresentable_port_values(seed: int, sample_id: int) -> None:
    """软件接口不截断无法由硬件端口表示的输入。"""
    with pytest.raises(ValueError, match="32 bits"):
        DitherWordGenerator().step(seed=seed, sample_id=sample_id)
