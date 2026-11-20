"""核公开静态校准工具的输入和评分边界，不把这些小检查称为器件辨识。"""

from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np
import pytest
from _rtl_test_support import invoke, load_tool

from adi_model.contracts.fixed_point import CodeStream
from subsys.experiments.fig11 import Fig11Parameters


# 该工具使用带 Fig.11 几何的物理模型。
# 从实际文件加载可验证其仓库路径和参数校验逻辑。
@pytest.fixture
def tool(monkeypatch):
    return load_tool("run_static_calibration", monkeypatch)


# 标称值应来自这一实验的参数类，而非当前数字顶层的归一化加载表。
# 若研究装配被错误接到公共入口，物理维度和训练数据都会漂移。
def test_parameters_use_the_single_engineering_default_table(tool, tmp_path):
    assert tool.load_parameters(None) == Fig11Parameters()
    path = tmp_path / "parameters.json"
    declared = {"unit_mismatch_std": 0.002, "format": asdict(Fig11Parameters().format)}
    path.write_text(json.dumps(declared))
    parameters = tool.load_parameters(path)
    assert parameters.unit_mismatch_std == 0.002
    assert parameters.format == Fig11Parameters().format
    assert json.loads(path.read_text()) == declared


# 重复键和非有限常量会改变输入身份或无法定点化。
# 同时核对输入文件的字节，保证拒绝不会改写用户提供的数据。
@pytest.mark.parametrize(
    "text,exception,match",
    [
        ('{"unit_mismatch_std": 0, "unit_mismatch_std": 0.1}', ValueError, "duplicate"),
        ('{"unit_mismatch_std": NaN}', ValueError, "nonfinite"),
        ('{"unit_mismatch_std": Infinity}', ValueError, "nonfinite"),
        ("[]", ValueError, "object"),
        ('{"format": null}', ValueError, "format"),
        ('{"format": {"output_bits": 16}}', TypeError, "missing"),
        ('{"ra_gain_model": "cpp"}', TypeError, "unexpected"),
    ],
)
def test_parameter_json_rejects_ambiguity_without_rewriting_inputs(
    tool, tmp_path, text, exception, match
):
    path = tmp_path / "parameters.json"
    path.write_text(text)
    with pytest.raises(exception, match=match):
        tool.load_parameters(path)
    assert path.read_text() == text


# 不合格的训练长度、参考源或波形幅度应在预约输出目录之前报告。
# 这样调用者能够重试同一目标名称，且不存在伪装成已完成的空报告。
@pytest.mark.parametrize(
    "arguments,match",
    [
        (["--seed", "-1"], "nonnegative"),
        (["--training-reference-seed", "-1"], "nonnegative"),
        (["--training-run-id", "1", "--holdout-run-id", "1"], "must differ"),
        (["--holdout", "1"], "in-band"),
        (["--holdout-bin", "0"], "in-band"),
        (["--holdout", "64", "--holdout-bin", "32"], "in-band"),
        # 2026-11-13：系数总数随几何从 63+8（512）变为 17/5（18×22=396），
        # 阈值是 prod(shape)+1 = 397。原值 1279 在新几何下已超过阈值，
        # 于是不再被参数校验拒绝、而是进入训练后报秩不足——这不是工具缺陷，
        # 是测试常量随几何切换失效。这里改用"恰好不超过阈值"的值。
        (["--train", "396"], "training count"),
        (["--training-peak-v", "3.1"], "voltages"),
        (["--training-peak-v", "nan"], "voltages"),
        (["--holdout-dc-v", "0.4", "--holdout-peak-v", "2.7"], "voltages"),
    ],
)
def test_cli_rejects_invalid_experiments_before_creating_output(
    tool, monkeypatch, tmp_path, arguments, match
):
    output = tmp_path / "new_run"
    with pytest.raises(ValueError, match=match):
        invoke(tool, ["--out", output, *arguments], monkeypatch)
    assert not output.exists()


# 重名时原来的收据仍代表原实验。
# 检查其原字节可发现先删除再尝试运行的实现。
def test_cli_preserves_existing_output(tool, monkeypatch, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    original = b"previous calibration evidence\n"
    (output / "receipt.json").write_bytes(original)
    with pytest.raises(ValueError, match="refusing overwrite"):
        invoke(tool, ["--out", output], monkeypatch)
    assert (output / "receipt.json").read_bytes() == original


# 有偏置的信号可以具有很小的标准差。
# 直接计算误差均值和均方根，并把越窗、数字裁剪分别计算。
def test_statistics_keep_dc_invalid_samples_and_clipping(tool):
    stream = CodeStream(
        code=np.array([0, 1, 2, 3]),
        clipped_low=np.array([True, False, False, False]),
        clipped_high=np.array([False, False, False, True]),
        analog_overflow=np.array([False, True, True, False]),
        v_fs=3.0,
        output_bits=20,
        peak_accumulator_bits=54,
    )
    error = np.array([0.001, -0.002, 0.003, 0.004])
    actual = tool.error_statistics(stream, stream.voltage - error)
    assert actual["mean_v"] == pytest.approx(np.mean(error), abs=1e-15)
    assert actual["std_v"] == pytest.approx(np.std(error), abs=1e-15)
    assert actual["rms_v"] == pytest.approx(np.sqrt(np.mean(error**2)), abs=1e-15)
    assert actual["max_abs_v"] == pytest.approx(0.004, abs=1e-15)
    assert (actual["analog_invalid"], actual["clip_low"], actual["clip_high"]) == (2, 1, 1)
    with pytest.raises(ValueError, match="complete finite"):
        tool.error_statistics(stream, np.full(4, np.nan))


# Fig.11 的通用定点格式选择半点取偶；生产数字 IP 的末级选择半点离零。
# 使用正负半点验证这里调用的确实是该研究工具自己的算术。
@pytest.mark.parametrize("numerator", [-7, -5, -3, -1, 1, 3, 5, 7])
def test_independent_oracle_rounds_signed_half_ties_to_even(tool, numerator):
    assert tool.nearest_even_ratio(numerator, 2) == round(numerator / 2)
    with pytest.raises(ValueError, match="positive"):
        tool.nearest_even_ratio(numerator, 0)
