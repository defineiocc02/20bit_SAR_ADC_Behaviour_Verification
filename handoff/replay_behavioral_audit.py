# =============================================================================
# 文件名     : replay_behavioral_audit.py
# 模块名     : handoff.replay_behavioral_audit
# 版本       : v1.0
# 功能       : 只读复算八片行为成果、冻结NLMS与CAAZ联合数据。
# 设计说明   :
#   [边界] 不运行器件、开关瞬态、RTL或后端，不把缺少这些视作行为模型失败。
#   [身份] 归档数据与当前源码身份分别记录，不修改旧收据或实验数值。
#   [独立] 指标与码宽使用既有独立复算器；当前入口重放与训练重放另列。
#   [噪声] CAAZ用保存的样本域误差与整数码重算，保留相同输出带宽。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================

"""行为级读证与复算，写入新收据，原始成果保持不变。"""

import argparse
import json
from pathlib import Path

import numpy as np
from adctoolbox.spectrum import compute_spectrum

from adi_model.capacitors import Capacitors, ExperimentConfig
from adi_model.hashing import sha256_file as sha
from task1.experiments.adctoolbox_calibration import CalibrationConfig
from task1.experiments.residue_control_study import MapperTable
from task2.eight.experiment import _capture, _fit_weights
from task2.eight.model import ChargePool, conversion_state, decode
from task2.eight.tests.audit_archives import _check_metrics, _integrate

ROOT = Path(__file__).resolve().parent.parent


def run(receipt_path: Path) -> dict:
    """只读重放归档与当前静态入口，写入新收据。

    Args:
        receipt_path: 尚不存在的JSON输出文件，不能指向原归档收据。

    Returns:
        指标、训练、当前入口和CAAZ检查结果及实际依赖摘要。

    Raises:
        ValueError: 输出文件已存在。
        AssertionError: 实际数据身份、数值复算、同笔控制、系数或配对不一致。

    独立FFT／逐码宽和区间积分来自现有独立复算器，不调用生产指标返回值。
    当前捕获和训练重放用于确认归档可复现，并非第二个独立训练算法。
    训练空间只由训练位图决定；留出参考未进入训练，仅检查留出特征覆盖。
    数据SHA不一致立即停止；旧源码摘要不一致另列，不伪造旧收据的通过身份。
    CAAZ的斩波解调和AZ共享分别处理，不能把二者当成同一布尔条件。
    每次只复算现有制造池与激励，不以通过结果声称MC、全幅或器件验收完成。
    原图／数据不重绘、不覆盖，当前模型和RTL字节不改变。
    """
    if receipt_path.exists():
        raise ValueError("output already exists; choose a new receipt path")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "scope": "behavioral model and archived results; no transistor/switch-transient/RTL/physical prerequisite",
        "static": {},
        "caaz": {},
        "source_identity_notes": {},
    }

    # 同一制造池贯穿开关对照，失配值只生成观测，不直接充当训练权重。
    for sigma in (2, 3):
        folder = ROOT / f"task2/evidence/eight_negative/sigma_{sigma}pct"
        summary = json.loads((folder / "summary.json").read_text())
        assert sha(folder / "data.npz") == summary["artifact_sha256"]["data.npz"]
        # 源码字节变化与数据数值错误分开记录，当前入口还要实际回放核对。
        mismatch = [p for p, h in summary["source_sha256"].items() if sha(ROOT / p) != h]
        result["source_identity_notes"][str(sigma)] = mismatch
        experiment = ExperimentConfig(**summary["experiment"])
        cfg = CalibrationConfig(**summary["calibration"])
        step = summary["native_dither_step_v"]
        with np.load(folder / "data.npz") as data:
            # 独立FFT和逐码宽复算，不用生产脚本返回的指标充当期望。
            metrics = _check_metrics(data, summary)
            pool = ChargePool.from_caps(
                Capacitors(
                    data["actual_main_cap_f"], data["actual_sub_cap_f"], data["actual_bridge_cap_f"]
                ),
                experiment,
            )
            threshold = data["nominal_threshold_v"]
            replay = {}
            training = {}
            for group in ("no_dem", "fig21_dem"):
                table = MapperTable.build(bridge=group == "fig21_dem")
                x = data[group + "_tone_reference_v"]
                plan = data[group + "_tone_plan"]
                phase = (data[group + "_tone_horizontal"], data[group + "_tone_vertical"])
                for arm in ("off", "on"):
                    prefix = group + "_" + arm
                    state, coarse, residue, adc = _capture(
                        x,
                        data[prefix + "_tone_word"],
                        plan,
                        phase,
                        threshold,
                        step,
                        table,
                        pool,
                        cfg,
                    )
                    # 所有同笔字段都比，避免仅终码一致掩盖控制错配。
                    for measured, key in [
                        (coarse, "coarse"),
                        (state.switches.codes, "command"),
                        (state.switches.main, "main"),
                        (state.switches.sub, "sub"),
                        (state.dither.sample_sub_high, "sample_sub"),
                        (adc, "adc2_code"),
                    ]:
                        np.testing.assert_array_equal(measured, data[prefix + "_tone_" + key])
                    diff = float(np.max(abs(residue - data[prefix + "_tone_adc2_residue_v"])))
                    assert diff < 2e-14
                    replay[prefix] = {
                        "samples": len(x),
                        "control_and_adc2_exact": True,
                        "residue_max_diff_v": diff,
                    }
                # 仅从训练参考与观测重训练；holdout只用于已实现的子空间覆盖检查。
                plan = data[group + "_train_plan"]
                word = data[group + "_shared_train_word"]
                state = conversion_state(
                    data[group + "_shared_train_command"],
                    plan,
                    (data[group + "_train_horizontal"], data[group + "_train_vertical"]),
                    decode(word),
                    threshold,
                    step,
                    table,
                )
                hold = []
                for arm in ("off", "on"):
                    p = group + "_" + arm
                    a = conversion_state(
                        data[p + "_tone_command"],
                        data[group + "_tone_plan"],
                        phase,
                        decode(data[p + "_tone_word"]),
                        threshold,
                        step,
                        table,
                    )
                    from task2.eight.model import fit_features

                    hold.append(fit_features(a.switches, a.features))
                # 训练观测来自保存的训练命令，不用留出参考或真实电容作答案。
                w, fit, _ = _fit_weights(
                    data[group + "_train_reference_v"],
                    state,
                    data[group + "_shared_train_adc2_code"],
                    data["frozen_gain"],
                    np.concatenate(hold),
                    cfg,
                    experiment.full_scale_peak_v,
                )
                delta = float(np.max(abs(w - data[group + "_shared_frozen_weight_v"])))
                assert delta < 1e-12
                training[group] = {
                    "train_samples": len(word),
                    "holdout_samples": len(x),
                    "rank": fit["design"]["rank"],
                    "columns": fit["design"]["columns"],
                    "max_weight_replay_diff_v": delta,
                    "holdout_reference_not_used_for_training": True,
                }
            # 精确的状态平均码宽是合法行为指标；不把它改叫单次无缺码或有限命中。
            # 评价对象是声明窗口的逐码平均宽度，不把精确积分误称有限命中。
            reintegrated = [_integrate(data, summary, enabled=e) for e in (False, True)]
            result["static"][str(sigma)] = {
                "metrics": metrics,
                "current_capture_replay": replay,
                "training_replay": training,
                "independent_reintegration": reintegrated,
                "manufacturing_seed": experiment.manufacturing_seed,
                "scope": "256 equal states, declared common window; not full-input-range guarantee",
            }
        print("PASS static", sigma, flush=True)

    # 逐组重算半LSB配对、误差RMS、FFT和噪声相关性，始终保持5 MS/s。
    folder = ROOT / "task3/evidence/negative/joint_noise"
    summary = json.loads((folder / "summary.json").read_text())
    assert (
        sha(folder / "caaz_joint_noise_data.npz")
        == summary["artifact_sha256"]["caaz_joint_noise_data.npz"]
    )
    with np.load(folder / "caaz_joint_noise_data.npz") as data:
        reference = data["pair_reference_v"]
        lsb = summary["output_lsb20_v"]
        n = len(reference)
        for group, expected in summary["groups"].items():
            raw = data[group + "_raw_q28_lms_signed_code"].astype(np.int64)
            e, o = raw.reshape(-1, 2).T
            # 独立AZ的斩波对照也用偶减奇；共享性由噪声序列而非此布尔量决定。
            difference_pair = group != "az_independent"
            # 同时核对21位半LSB差与20位输出，防止只比RMS漏掉舍入差异。
            exact = e - o if difference_pair else e + o + 1
            pair = exact // 2
            np.testing.assert_array_equal(exact, data[group + "_pair_exact_half_lsb"])
            np.testing.assert_array_equal(pair, data[group + "_pair_signed_code"])
            output = (pair + 0.5) * lsb
            error = output - reference
            np.testing.assert_allclose(error, data[group + "_pair_error_v"], atol=1e-15, rtol=0)
            # 全组保持相同5 MS/s及整带宽；偏移另由误差向量保留，不改频率元数据。
            metrics = compute_spectrum(
                output,
                fs=summary["pair_rate_hz"],
                max_scale_range=(-5, 5),
                win_type="rectangular",
                side_bin=0,
            )["metrics"]
            np.testing.assert_allclose(
                [metrics["sndr_dbc"], metrics["sfdr_dbc"]],
                [expected["sndr_db"], expected["sfdr_dbc"]],
                atol=1e-8,
                rtol=0,
            )
            rms = float(error.std() * 1e6)
            np.testing.assert_allclose(rms, expected["ac_error_rms_uv"], atol=1e-9, rtol=0)
            az = data[group + "_az_injected_v"]
            a, b = az.reshape(-1, 2).T
            if group == "shared_ideal":
                np.testing.assert_array_equal(a, b)
            if group == "shared_hold_error":
                np.testing.assert_allclose(
                    b, summary["hold_ratio"] * a + summary["hold_shift_v"], atol=1e-20, rtol=0
                )
            result["caaz"][group] = {
                "pair_outputs": n,
                "integer_pair_exact": True,
                "ac_rms_uv": rms,
                "sndr_db": float(metrics["sndr_dbc"]),
                "az_even_odd_correlation": float(np.corrcoef(a, b)[0, 1]),
                "input_noise_model": "white AZ with explicit shared pair and hold impairment; no explicit flicker process in this experiment",
            }
    print("PASS CAAZ", flush=True)
    # 快照只保存实际核对的结论，不用旧PASS替换输入摘要变化记录。
    result["source_and_data_sha256"] = {
        str(p.relative_to(ROOT)): sha(p)
        for p in [
            Path(__file__),
            ROOT / "task2/eight/tests/audit_archives.py",
            ROOT / "task2/eight/experiment.py",
            ROOT / "task1/experiments/bridge_lms_comparison.py",
            ROOT / "task3/tools/run_joint_noise.py",
            ROOT / "task2/evidence/eight_negative/sigma_2pct/data.npz",
            ROOT / "task2/evidence/eight_negative/sigma_3pct/data.npz",
            folder / "caaz_joint_noise_data.npz",
        ]
    }
    with receipt_path.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("SAVED", receipt_path.name, flush=True)

    return result


def main() -> None:
    """从仓库根目录以PYTHONPATH=.:src执行，--out给出新文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)


if __name__ == "__main__":
    main()
