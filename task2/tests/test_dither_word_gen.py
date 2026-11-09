# =============================================================================
# 文件名     : test_dither_word_gen.py
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

from task2.model.dither_word_gen import DEFAULT_DITHER_SEED, DitherWordGenerator, DitherWordOutput


# =========================================================================
# 1. 函数组：test_word_order_and_back_to_back_samples / test_idle_and_disabled_samples_do_not_consume_random_bits / test_illegal_seed_keeps_transaction_state
# =========================================================================
# -------------------------------------------------------------------------
# 单个高位触发的首个反馈位应落在 D6，两次接受各发一个字。
# 停顿不发字，关闭时发同笔零字，恢复后继续原序列。
# 零种子或同沿冲突发错误，不发布样本，也不消耗状态。
# -------------------------------------------------------------------------
def test_word_order_and_back_to_back_samples() -> None:
    """单个高位触发的首个反馈位应落在 D6，两次接受各发一个字。"""
    generator = DitherWordGenerator()
    assert not generator.step(seed_load=True, seed=1 << 30).word_valid

    first = generator.step(sample_accept=True, sample_id=0xFFFFFFFF)
    second = generator.step(sample_accept=True, sample_id=0)
    assert first == DitherWordOutput(
        word=0x20, word_valid=True, word_sample_id=0xFFFFFFFF, rng_state=0x20
    )
    assert second == DitherWordOutput(word=0, word_valid=True, word_sample_id=0, rng_state=0x800)


def test_idle_and_disabled_samples_do_not_consume_random_bits() -> None:
    """停顿不发字，关闭时发同笔零字，恢复后继续原序列。"""
    active = DitherWordGenerator()
    reference = DitherWordGenerator()
    first = active.step(sample_accept=True, sample_id=1)
    assert first == reference.step(sample_accept=True, sample_id=1)

    idle = active.step(sample_id=42)
    assert not idle.word_valid
    assert (idle.word, idle.word_sample_id, idle.rng_state) == (first.word, 1, first.rng_state)
    disabled = active.step(dither_enable=False, sample_accept=True, sample_id=2)
    assert disabled == DitherWordOutput(
        word_valid=True, word_sample_id=2, rng_state=first.rng_state
    )
    assert active.step(sample_accept=True, sample_id=3) == reference.step(
        sample_accept=True, sample_id=3
    )


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


@pytest.mark.parametrize(("seed", "sample_id"), [(-1, 0), (1 << 31, 0), (0, -1), (0, 1 << 32)])
def test_python_rejects_unrepresentable_port_values(seed: int, sample_id: int) -> None:
    """软件接口不截断无法由硬件端口表示的输入。"""
    with pytest.raises(ValueError, match="31 and 32 bits"):
        DitherWordGenerator().step(seed=seed, sample_id=sample_id)
