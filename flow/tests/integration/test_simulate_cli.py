# =============================================================================
# 文件名     : test_simulate_cli.py
# 功能       : 核对公开命令与实际生产模型的数据、输出路径及拒绝条件。
# =============================================================================
"""实际生产事务 CLI 的完整输出、覆盖追溯与失败边界。"""

import hashlib
import json

import numpy as np
import pytest

from adi_model import __main__ as module_cli
from adi_model import cli

KEYS = {
    "code",
    "sample_id",
    "clipped_low",
    "clipped_high",
    "analog_overflow",
    "sadc_over",
    "ra_sat",
    "adc2_over",
    "rdac_over",
    "input_clean_v",
    "input_v",
}


# 命令行的参数错误由 argparse 退出，运行时数据错误由入口返回码 2。
# 测试分别保留错误输出并检查两种路径的可观察结果。
def _invoke(arguments):
    try:
        return cli.main_simulate(arguments)
    except SystemExit as exc:
        return exc.code


# 同一份 NPZ 检查一维样本列和二维物理开关。
# 分组阶数和向量长度必须同时正确，避免 slot 轴与 sample 轴被混用。
# 复位后的前四个扰动字是硬件身份检查；若命令行走回旧研究模型，这里会立即失败。
def test_cli_runs_the_real_model_and_keeps_all_output_arrays(tmp_path, capsys):
    output = tmp_path / "run"
    assert _invoke(["--samples", "32", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "SIMULATION_COMPLETE"
    assert summary["model_run"] is True
    assert summary["result_role"] == "DIGITAL_IP_TRANSACTION_REFERENCE"
    assert summary["source_conformance"] == "NOT_ASSESSED"
    assert summary["source_reproduction"] is False
    assert summary["records_filtered"] == 0
    assert summary["n_codes"] == summary["n_samples"] == 32
    assert summary["frequency_adjusted"] is False
    assert {p.name for p in output.iterdir()} == {"codes.npz", "metadata.json", "assumptions.json"}
    with np.load(output / "codes.npz") as data:
        physical = {"acquisition_ids", "pre_slots", "post_slots", "event_k"}
        scalars = {"tag", "qid", "word", "h", "v", "half", "coin", "sadc_mask", "lower_eighths",
                   "upper", "coarse", "k", "adc2_code", "event_count", "g_q30", "wm_q30", "wp_q30",
                   "out_valid", "out_err"}
        # 各物理槽和阶段需要完整保存。
        # 单个正确输出无法证明两端真的接受了同一笔扰动。
        assert set(data.files) == KEYS | physical | scalars
        assert all(data[name].shape == (32,) for name in KEYS | scalars)
        assert all(data[name].shape == (32, 8) for name in physical)
        assert data["word"][:4].tolist() == [45, 7, 17, 40]
        assert data["tag"].tolist() == list(range(1, 33))
        assert summary["digital_contract"] == "adc_digital_ip_v21"

        np.testing.assert_array_equal(data["sample_id"], np.arange(32))
        assert np.all(data["code"] < 2**20)
    assert json.loads((output / "assumptions.json").read_text()) == summary["inferences"]
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["model_run"] is True and metadata["records_filtered"] == 0


# 故意让正弦越过外部输入范围，仍应保存每一行。
# 从节点标志独立构造并集，再与汇总计数比较，可发现丢掉异常样本的路径。
def test_cli_preserves_out_of_range_samples_and_the_node_flag_union(tmp_path, capsys):
    output = tmp_path / "overrange"
    assert _invoke(["--amplitude", "3.2", "--samples", "32", "--output", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    with np.load(output / "codes.npz") as data:
        assert len(data["code"]) == 32
        assert np.any(data["sadc_over"])
        assert np.any(abs(data["input_clean_v"]) > 3)
        union = data["sadc_over"] | data["ra_sat"] | data["adc2_over"] | data["rdac_over"]
        np.testing.assert_array_equal(data["analog_overflow"], union)
        assert summary["raw_invalid"] == int(np.count_nonzero(union))


# 不为获得整周期 FFT 而重调输入频率。
# 比较解析后的真实数值及其来源，避免恰好数值相同却漏记用户覆盖。
def test_exact_equal_form_flags_keep_user_origin_and_do_not_retune_frequency(capsys):
    assert _invoke(["--amplitude=1.2", "--frequency=1234567", "--samples=16"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["stimulus"]["frequency"]["value"] == 1234567
    assert summary["frequency_adjusted"] is False
    for name in ("amplitude", "frequency", "samples"):
        assert summary["stimulus"][name]["origin"] == "USER_OVERRIDE"


# 两个 JSON 分别覆盖宏参数与运行条件；核对摘要采用它们在磁盘上的原字节。
# 实际 fs 和噪声还要检查模型元数据，防止收到参数但运行继续使用原值。
def test_explicit_parameter_files_have_hashes_and_actual_override_values(tmp_path, capsys):
    parameters = tmp_path / "parameters.json"
    overrides = tmp_path / "overrides.json"
    parameters.write_text(json.dumps({"unit_mismatch_std": 0.0, "bridge_mismatch_std": 0.0}))
    overrides.write_text(
        json.dumps({"fs": 20e6, "enable_sampling_noise": False, "ra_enable_noise": False})
    )
    assert (
        _invoke(
            [
                "--samples",
                "16",
                "--parameters",
                str(parameters),
                "--config-overrides",
                str(overrides),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    # 文件摘要必须配对实际解释的覆盖。
    # 只记录路径不能重建被修改前的运行。
    by_role = {row["role"]: row for row in summary["input_files"]}
    for role, path in (("parameters", parameters), ("config_overrides", overrides)):
        assert by_role[role]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert by_role[role]["override_fields"] == sorted(json.loads(path.read_text()))
    assert summary["inferences"]["fs_hz"]["value"] == 20e6
    assert summary["inferences"]["fs_hz"]["grade"] == "USER_OVERRIDE"
    assert summary["enabled"]["sampling_noise"] is False
    assert summary["enabled"]["ra_output_noise"] is False


# 模糊缩写可能把一个控制量送到错误的模拟端口。
# 非有限激励与越位的记录数也必须在产生成功摘要之前失败。
@pytest.mark.parametrize(
    "arguments",
    [
        ["--amp", "1"],
        ["--fre", "1e6"],
        ["--samples", "0"],
        ["--samples", "4294967296"],
        ["--seed", "-1"],
        ["--frequency", "nan"],
        ["--amplitude", "inf"],
    ],
)
def test_invalid_cli_requests_emit_failure_json_without_a_model_success_claim(arguments, capsys):
    assert _invoke(arguments) == 2
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert error["status"] == "SIMULATION_REJECTED" and error["model_run"] is False


# 给一份坏配置时，命令不能忽略它然后改跑默认装配。
# 选用不存在的字段、非法布尔和重复 JSON 键，分别覆盖读取与解释阶段。
@pytest.mark.parametrize("kind", ["unknown", "active_none", "dither_not_boolean", "duplicate"])
def test_bad_override_files_do_not_fall_back_to_defaults(tmp_path, capsys, kind):
    path = tmp_path / "bad.json"
    payload = {
        "unknown": '{"legacy_field": 1}',
        "active_none": '{"c_sadc": null}',
        "dither_not_boolean": '{"dither_enable": "yes"}',
        "duplicate": '{"v_fs": 3, "v_fs": 2}',
    }[kind]
    path.write_text(payload)
    assert _invoke(["--samples", "16", "--config-overrides", str(path)]) == 2
    result = capsys.readouterr()
    assert not result.out
    assert json.loads(result.err)["model_run"] is False


# 已有目录中的标记文件用于检测误覆盖。
# 悬空符号链接则用于证明检查的是输出名称自身，而非目标目录是否存在。
def test_existing_and_dangling_output_paths_are_rejected_without_modification(tmp_path, capsys):
    existing = tmp_path / "old"
    existing.mkdir()
    marker = existing / "user.txt"
    marker.write_bytes(b"prior evidence")
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "absent")
    # 两种目标的名称均被占用。
    # 额外核对原始标记，可发现跟随符号链接后误覆盖的路径。
    for target in (existing, dangling):
        assert _invoke(["--samples", "8", "--output", str(target)]) == 2
        result = capsys.readouterr()
        assert json.loads(result.err)["model_run"] is False
    assert marker.read_bytes() == b"prior evidence" and dangling.is_symlink()


# 模块子命令与控制台脚本是两种调用拼法，均应走真实模拟。
# 这里核对运行回执；其数值与保存的物理列由完整 CLI 用例和 RTL 对拍验证。
def test_python_module_uses_the_same_actual_simulate_entry(capsys):
    assert module_cli.main(["simulate", "--samples", "8"]) == 0
    assert json.loads(capsys.readouterr().out)["model_run"] is True
