# =============================================================================
# 文件名     : test_readme_effect_evidence.py
# 模块名     : test_readme_effect_evidence
# 版本       : v1.0
# 功能       : 从冻结样本重算历史 README 的静态误差和频谱数字
# 设计说明   :
#   * JSON 的参数与 NPZ 的原始样本须来自同一 Git 提交，否则比较没有意义。
#   * 只对旧结果做数值回归，当前模型的测试不依赖这批大文件留在交付树。
#   * CI 使用完整 Git 历史。缺少该快照只在脱离历史的源码副本标为不可用。
# 变更记录   : 旧样本移出工作树后，改从冻结提交读取。
# =============================================================================

"""从固定 ADC 时钟和样本重算公开图，不把历史波形当作当前模型输出。"""

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
# 清理工作树前的最后一版含有 JSON 和 NPZ；从同一提交读取避免混用口径。
_FROZEN_README_COMMIT = "952eddc70685583c8591dfe11724be5f94d413a2"


@pytest.fixture(scope="module")
def historical_readme_asset():
    """返回读取旧图数据的函数；源码包或浅克隆缺历史时报告原因。"""
    git = ["git", "-C", str(ROOT)]
    try:
        available = subprocess.run(
            [*git, "cat-file", "-e", f"{_FROZEN_README_COMMIT}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("historical README samples require git")
    if available.returncode:
        pytest.skip(f"checkout does not contain README sample snapshot {_FROZEN_README_COMMIT}")

    # 提交存在却缺文件是证据不完整，必须保留为失败。
    def read(name: str) -> bytes:
        return subprocess.check_output(
            [*git, "show", f"{_FROZEN_README_COMMIT}:docs/readme/data/{name}"]
        )

    return read


def test_calibration_plot_metrics_match_retained_samples(historical_readme_asset):
    # 校准前后共用物理输入和采样率；噪声失真功率应剔除直流及注入音。
    metadata = json.loads(historical_readme_asset("calibration_40m.json"))
    with np.load(io.BytesIO(historical_readme_asset("calibration_40m.npz")), allow_pickle=False) as samples:
        for name, case in metadata["cases"].items():
            assert case["validation"]["fs_hz"] == 40e6
            assert case["overflow_count"] == 0
            x = samples[name + "_x_clean_v"]
            assert len(x) == case["validation"]["n"]
            for kind in ("before", "after"):
                y = samples[f"{name}_{kind}_v"]
                stats = case[kind + "_integer"]
                assert float(np.std(y - x)) == pytest.approx(stats["error_std_v"], abs=1e-14)
                power = np.abs(np.fft.rfft(y - y.mean()) / len(y)) ** 2
                power[1:-1] *= 2
                k = stats["fundamental_bin"]
                mask = np.ones(len(power), dtype=bool)
                mask[0] = mask[k] = False
                noise_and_distortion = power[mask].sum()
                sndr = 10 * np.log10(power[k] / noise_and_distortion)
                assert float(sndr) == pytest.approx(stats["SNDR_dB"], abs=1e-6)


def test_interleave_plot_spurs_and_rms_match_retained_samples(historical_readme_asset):
    # 这里用残差谱按满幅正弦归一化，两个交织杂散的位置由样本数与 k 决定。
    metadata = json.loads(historical_readme_asset("interleave_40m.json"))
    with np.load(io.BytesIO(historical_readme_asset("interleave_40m.npz")), allow_pickle=False) as samples:
        for row in metadata["rows"]:
            assert row["fs_hz"] == 40e6
            assert row["analog_overflow_count"] == 0
            error = samples[row["condition"]]
            assert len(error) == row["n"]
            assert float(np.sqrt(np.mean(error**2))) == pytest.approx(row["error_rms_v"], abs=1e-14)
            power = np.abs(np.fft.rfft(error - error.mean()) / len(error)) ** 2
            power[1:-1] *= 2
            for key, index in (
                ("spur_fs2_dbfs_bin", len(error) // 2),
                ("spur_fs2_minus_fin_dbfs_bin", len(error) // 2 - row["k"]),
            ):
                spur = 10 * np.log10(max(power[index] / (3.0**2 / 2), 1e-300))
                assert float(spur) == pytest.approx(row[key], abs=1e-8)
