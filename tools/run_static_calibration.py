#!/usr/bin/env python3
"""重生显式静态失配模型的训练、冻结和独立留出结果。

本工具只运行 Python 行为链。训练器只接数字观测及独立已知参考，不读取实际电容、增益或保持电压。
同一留出 raw 分别使用名义/冻结系数，并与独立整数方程逐字比较。实际电容另存为 plant-only 文件，
仅供 RTL 行为 plant 重放；它不是学习器输入。SVD、几何、理想参考与算术格式均为工程选择。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
import scipy

import adi_model
from adi_model.defaults import DefaultParameters, default_model
from adi_model.fixed_point import CodeStream, FixedPointFormat, FixedPointReconstructor
from adi_model.weight_calibration import DigitalObservation, FrozenCalibration, fit_unit_weights


def require(condition: bool | np.bool_, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def loaded_sources(source_root: Path) -> dict[str, Path]:
    """返回实际已加载的本包 Python 源文件，不把未使用模块当受测源。"""
    result = {}
    for name, module in sys.modules.items():
        filename = getattr(module, "__file__", None)
        if not name.startswith("adi_model") or not isinstance(filename, str):
            continue
        path = Path(filename).resolve()
        if path.parent == source_root:
            result[path.name] = path
    return result


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_parameters(path: Path | None) -> DefaultParameters:
    """读取高层显式参数；缺省值由唯一 DefaultParameters 推断表解释。"""
    if path is None:
        return DefaultParameters()
    require(
        path.is_file() and path.stat().st_size < 300_000_000,
        "parameter JSON must be a readable file smaller than300MB",
    )

    def unique(pairs):
        result = {}
        for name, value in pairs:
            require(name not in result, f"duplicate parameter JSON key: {name}")
            result[name] = value
        return result

    def nonfinite(name):
        raise ValueError(f"nonfinite parameter JSON constant: {name}")

    data = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=nonfinite
    )
    require(isinstance(data, dict), "parameters must be an explicit JSON object")
    if "format" in data:
        require(isinstance(data["format"], dict), "format must explicitly declare its fields")
        data["format"] = FixedPointFormat(**data["format"])
    return DefaultParameters(**data)


def error_statistics(stream: CodeStream, reference_v: np.ndarray) -> dict:
    """对全部样本评分 [V]；不剔除异常、直流、增益或相位误差。"""
    error = stream.voltage - reference_v
    require(
        error.shape == stream.code.shape and np.all(np.isfinite(error)),
        "reference error must remain a complete finite record",
    )
    return {
        "mean_v": float(np.mean(error)),
        "std_v": float(np.std(error)),
        "rms_v": float(np.sqrt(np.mean(error**2))),
        "max_abs_v": float(np.max(np.abs(error))),
        "analog_invalid": int(np.count_nonzero(stream.analog_overflow)),
        "clip_low": int(np.count_nonzero(stream.clipped_low)),
        "clip_high": int(np.count_nonzero(stream.clipped_high)),
    }


def nearest_even_ratio(numerator: int, denominator: int) -> int:
    """独立的符号/幅度整数舍入，不调用生产 round_even_divide。"""
    require(denominator > 0, "oracle denominator must be positive")
    magnitude, remainder = divmod(abs(numerator), denominator)
    if remainder > denominator - remainder or (
        remainder == denominator - remainder and magnitude & 1
    ):
        magnitude += 1
    return -magnitude if numerator < 0 else magnitude


def independent_integer_codes(
    image: dict, data: DigitalObservation, common_q: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """用实际 mask、外部寄存器与显式公共 Q 项逐单元计算；不调用生产 MAC/重构。

    此 oracle 限全 SIGNAL、对称±Vfs折叠。common_q为无量纲归一化有符号电压寄存器，
    其逐单元项与实际数字观察取偶量化后必须精确相同。
    """
    data.validate()
    fmt, spec = image["format"], image["spec"]
    require(np.all(data.signal_mask == 1), "oracle requires the declared allSIGNAL fold")
    masks = np.concatenate((data.main_masks, data.sub_masks), axis=2).astype(np.int64)
    require(common_q.shape == data.adc2_code.shape, "one common Q term is required per raw")
    vscale, wscale, count = (
        1 << fmt["voltage_fraction_bits"],
        1 << fmt["weight_fraction_bits"],
        1 << fmt["output_bits"],
    )
    expected = common_q[:, None, None].astype(object) + (1 - 2 * masks) * vscale
    actual = np.rint(data.known_voltage_term_v / data.spec.v_fs * vscale)
    require(
        np.all(actual == expected), "oracle voltage terms do not exactly match the declared fold"
    )
    limit = 1 << (fmt["accumulator_bits"] - 1)
    words, low, high = [], [], []
    for index, ids in enumerate(data.slice_ids):
        gain, known, products = 0, 0, []
        for slot, physical in enumerate(ids):
            for unit in range(data.spec.shape[1]):
                weight = image["weights_q"][int(physical)][unit]
                gain += weight
                product = weight * (
                    int(common_q[index]) + (1 - 2 * int(masks[index, slot, unit])) * vscale
                )
                known += product
                products.append(product)
        fine = image["adc2_min_q"] + nearest_even_ratio(
            (2 * int(data.adc2_code[index]) + 1) * (image["adc2_max_q"] - image["adc2_min_q"]),
            1 << (spec["adc2_n_bits"] + 1),
        )
        fine_product = (fine - image["offset_q"]) * wscale
        numerator = fine_product - known
        shifted, denominator = (numerator + gain * vscale) * count, 2 * gain * vscale
        require(
            gain > 0
            and all(
                -limit <= value < limit
                for value in (*products, known, fine_product, numerator, shifted, denominator)
            ),
            "independent integer oracle exceeded the declared arithmetic domain",
        )
        value = shifted // denominator
        low.append(value < 0)
        high.append(value >= count)
        words.append(min(max(value, 0), count - 1))
    return np.asarray(words, np.int64), np.asarray(low, bool), np.asarray(high, bool)


def save_digital_record(path: Path, data: DigitalObservation, reference_v: np.ndarray) -> None:
    """保存数字记录和独立参考；不包含物理 C/G/heldV。"""
    np.savez_compressed(
        path,
        reference_v=reference_v,
        slice_ids=data.slice_ids,
        adc2_code=data.adc2_code,
        main_masks=data.main_masks,
        sub_masks=data.sub_masks,
        signal_mask=data.signal_mask,
        known_voltage_term_v=data.known_voltage_term_v,
        analog_invalid=data.overflow,
        sample_ids=data.sample_ids,
    )


def write_case(
    directory: Path,
    words: np.ndarray,
    reference: np.ndarray,
    decoder: FixedPointReconstructor,
    first_sample_id: int,
    plant_sha: str,
) -> None:
    """使用与 RTL driver 相同的三列刺激及装载载荷约定；不是 SV trace。"""
    directory.mkdir()
    (directory / "stimulus.txt").write_text(
        "".join(
            f"{int(word)} {float(value):.17g} 0\n"
            for word, value in zip(words, reference, strict=True)
        )
    )
    write_json(directory / "registers.json", decoder.to_dict())
    (directory / "weights.hex").write_text(
        "".join(f"{int(w):012x}\n" for w in decoder.weights_q.flat)
    )
    bits = decoder.format.voltage_bits
    (directory / "scalars.hex").write_text(
        "".join(
            f"{v % (1 << bits):0{math.ceil(bits / 4)}x}\n"
            for v in (decoder.offset_q, decoder.adc2_min_q, decoder.adc2_max_q)
        )
    )
    write_json(
        directory / "case_manifest.json",
        {
            "schema_version": 1,
            "expected_samples": len(reference),
            "expected_good": len(reference),
            "first_sample_id": first_sample_id,
            "sample_id_increment": 1,
            "sample_id_bits": 32,
            "stimulus_columns": ["word", "reference_v", "expected_reject"],
            "plant_sha256": plant_sha,
            "input_sha256": {p.name: digest(p) for p in directory.iterdir() if p.is_file()},
            "trace_scope": "PYTHON_DIGITAL_RECORD_ONLY_NOT_SV_CAPTURE",
        },
    )


def run(args: argparse.Namespace, model) -> dict:
    out, params = args.out, model.parameters
    if args.parameters_json is not None:
        parameter_bytes = args.parameters_json.read_bytes()
        require(
            hashlib.sha256(parameter_bytes).hexdigest() == args.parameters_sha256,
            "parameter JSON changed after validation",
        )
        (out / "parameters_supplied.json").write_bytes(parameter_bytes)
    source_root = Path(adi_model.__file__).resolve().parent
    source_before = {p.name: digest(p) for p in source_root.glob("*.py")}
    write_json(out / "source_snapshot_before.json", source_before)
    plant = {
        name: np.asarray(getattr(model.chip, name)).tolist()
        for name in ("C_main", "C_sub", "C_bridge", "c_p_sub", "c_p_main", "C_feedback_true")
    }
    write_json(out / "plant_replay_only.json", plant)
    cfg = model.cfg
    write_json(
        out / "plant_metadata.json",
        {
            **plant,
            "scope": "ACTUAL_FIXED_PLANT_ONLY_NOT_LEARNER_INPUT",
            "offset_v": 0.0,
            "vfs": cfg.v_fs,
            "adc2_min_v": cfg.adc2_v_min,
            "adc2_max_v": cfg.adc2_v_max,
            "adc2_n_bits": cfg.adc2_n_bits,
            "adc2_codebook": cfg.adc2_codebook,
            "adc2_endpoint_policy": cfg.adc2_endpoint_policy,
            "adc2_max_closed": int(cfg.adc2_endpoint_policy == "closed"),
            "ra_clip_v": cfg.ra_v_clip,
            "sampled_top_v": 0.0,
            "sampled_subnode_v": 0.0,
            "actual_reference_high_v": cfg.v_fs,
            "actual_reference_low_v": -cfg.v_fs,
            "nominal_reference_high_v": cfg.v_fs,
            "nominal_reference_low_v": -cfg.v_fs,
        },
    )
    write_json(out / "parameters.json", asdict(params))
    write_json(out / "config.json", cfg.to_dict())
    write_json(out / "inferences.json", model.inference_table())
    reference = np.random.Generator(np.random.PCG64(args.training_reference_seed)).uniform(
        -args.training_peak_v, args.training_peak_v, args.train
    )

    def training_source(time_s):
        index = np.rint(np.asarray(time_s) * cfg.fs).astype(np.int64)
        require(
            np.all((index >= 0) & (index < args.train)), "training query outside reference record"
        )
        return reference[index]

    training = model.simulate(training_source, args.train, run_id=args.training_run_id)
    observed = DigitalObservation.from_result(training.raw)
    save_digital_record(out / "training_digital.npz", observed, reference)
    write_case(
        out / "training_word0",
        np.zeros(args.train, np.int64),
        reference,
        model.nominal_decoder,
        int(observed.sample_ids[0]),
        digest(out / "plant_replay_only.json"),
    )
    fitted = fit_unit_weights(observed, reference)
    write_json(out / "frozen_schema2.json", fitted.to_dict())
    restored = FrozenCalibration.from_dict(json.loads((out / "frozen_schema2.json").read_text()))
    np.testing.assert_array_equal(restored.weights, fitted.weights)
    frozen_before = json.dumps(restored.to_dict(), sort_keys=True, allow_nan=False)

    def holdout_source(time_s):
        return args.holdout_dc_v + args.holdout_peak_v * np.sin(
            2 * np.pi * (args.holdout_bin * cfg.fs / args.holdout) * time_s
        )

    known_holdout = holdout_source(np.arange(args.holdout) / cfg.fs)
    held = model.simulate(holdout_source, args.holdout, run_id=args.holdout_run_id)
    require(
        held.raw.pool.chip is training.raw.pool.chip,
        "training/holdout changed physical chip object",
    )
    held_data = DigitalObservation.from_result(held.raw)
    calibrated = held.raw.to_codes(format=params.format, calibration=restored)
    decoder = FixedPointReconstructor.from_frozen_calibration(
        held_data.spec, restored, format=params.format
    )
    write_json(out / "registers.json", decoder.to_dict())
    reloaded = FixedPointReconstructor.from_dict(json.loads((out / "registers.json").read_text()))
    np.testing.assert_array_equal(calibrated.code, reloaded.reconstruct(held_data).code)
    oracle, low, high = independent_integer_codes(
        reloaded.to_dict(), held_data, np.zeros(args.holdout, np.int64)
    )
    np.testing.assert_array_equal(calibrated.code, oracle)
    np.testing.assert_array_equal(calibrated.clipped_low, low)
    np.testing.assert_array_equal(calibrated.clipped_high, high)
    require(
        json.dumps(restored.to_dict(), sort_keys=True, allow_nan=False) == frozen_before,
        "holdout modified frozen calibration parameters",
    )
    save_digital_record(out / "holdout_digital.npz", held_data, known_holdout)
    np.savez_compressed(
        out / "integer_words.npz",
        nominal=held.codes.code,
        calibrated=calibrated.code,
        independent=oracle,
        clipped_low=calibrated.clipped_low,
        clipped_high=calibrated.clipped_high,
        analog_invalid=calibrated.analog_overflow,
    )
    plant_sha = digest(out / "plant_replay_only.json")
    for name, image in (("holdout_nominal", model.nominal_decoder), ("holdout_fitted", reloaded)):
        write_case(
            out / name,
            np.zeros(args.holdout, np.int64),
            known_holdout,
            image,
            int(held_data.sample_ids[0]),
            plant_sha,
        )
    before, after = (
        error_statistics(held.codes, known_holdout),
        error_statistics(calibrated, known_holdout),
    )
    write_json(out / "holdout_metrics.json", {"nominal": before, "frozen": after})
    require(
        all(after[name] == 0 for name in ("analog_invalid", "clip_low", "clip_high")),
        "holdout retains invalid or clipped records; static acceptance failed",
    )
    require(after["rms_v"] < before["rms_v"], "same-raw holdout RMS did not improve")
    source_after = {
        name: digest(path) for name, path in sorted(loaded_sources(source_root).items())
    }
    require(
        source_after == {name: source_before[name] for name in source_after},
        "loaded production source changed during the experiment",
    )
    result = {
        "schema_version": 1,
        "status": "PASS",
        "scope": "STATIC_PYTHON_BEHAVIORAL_TRAIN_FREEZE_HOLDOUT",
        "rtl_executed": False,
        "source_chip_algorithm_reproduction": False,
        "input_sha256": {}
        if args.parameters_json is None
        else {"parameters_json": args.parameters_sha256},
        "source_sha256": source_after,
        "source_root": "src/adi_model",
        "producer_sha256": digest(Path(__file__)),
        "producer": "tools/run_static_calibration.py",
        "seed": args.seed,
        "training_reference_seed": args.training_reference_seed,
        "training_samples": args.train,
        "holdout_samples": args.holdout,
        "training_run_id": args.training_run_id,
        "holdout_run_id": args.holdout_run_id,
        "training_peak_v": args.training_peak_v,
        "training_digest": restored.training_digest,
        "rank": restored.rank,
        "condition": restored.condition,
        "training_residual_rms_v": restored.residual_rms_v,
        "same_holdout_raw_for_both_decoders": True,
        "same_physical_chip_object": True,
        "independent_integer_words_matched": len(oracle),
        "nominal_error": before,
        "calibrated_error": after,
        "holdout_waveform": {
            "kind": "coherent_sine",
            "peak_v": args.holdout_peak_v,
            "dc_v": args.holdout_dc_v,
            "bin": args.holdout_bin,
            "frequency_hz": args.holdout_bin * cfg.fs / args.holdout,
        },
        "inferred_model_metadata": held.metadata,
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        },
        "not_verified": [
            "RTL/real calibration readout",
            "reference accuracy",
            "noise/dynamics/drift",
            "ADC2 nonlinearities",
            "source full DEM/dither circuit",
            "PDK/STA/PEX",
        ],
    }
    try:
        result["git_head"] = subprocess.check_output(
            ["git", "-C", str(source_root.parents[1]), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        result["git_dirty"] = bool(
            subprocess.check_output(
                ["git", "-C", str(source_root.parents[1]), "status", "--porcelain"], text=True
            )
        )
    except (OSError, subprocess.CalledProcessError):
        result.update(git_head=None, git_dirty=None)
    result["artifact_sha256"] = {
        str(p.relative_to(out)): digest(p) for p in sorted(out.rglob("*")) if p.is_file()
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="尚不存在的新输出目录")
    parser.add_argument(
        "--parameters-json", type=Path, help="DefaultParameters覆盖；完整实际值写入结果"
    )
    parser.add_argument("--seed", type=int, default=42, help="固定制造seed，非工艺参数（默认42）")
    parser.add_argument("--train", type=int, default=4096)
    parser.add_argument("--holdout", type=int, default=512)
    parser.add_argument("--training-reference-seed", type=int, default=20261105)
    parser.add_argument("--training-run-id", type=int, default=0)
    parser.add_argument("--holdout-run-id", type=int, default=1)
    parser.add_argument("--training-peak-v", type=float, default=3.0)
    parser.add_argument("--holdout-peak-v", type=float, default=2.65)
    parser.add_argument("--holdout-dc-v", type=float, default=0.05)
    parser.add_argument("--holdout-bin", type=int, default=37)
    args = parser.parse_args()
    require(not args.out.exists(), "output directory already exists; refusing overwrite")
    require(
        min(args.seed, args.training_reference_seed, args.training_run_id, args.holdout_run_id)
        >= 0,
        "seeds and run IDs must be nonnegative integers",
    )
    require(args.training_run_id != args.holdout_run_id, "training and holdout run IDs must differ")
    require(
        args.holdout > 1 and 0 < args.holdout_bin < args.holdout / 2,
        "holdout requires a positive in-band coherent sine bin",
    )
    if args.parameters_json is not None:
        require(
            args.parameters_json.is_file() and args.parameters_json.stat().st_size < 300_000_000,
            "parameter JSON must be a readable file smaller than300MB",
        )
    args.parameters_sha256 = None if args.parameters_json is None else digest(args.parameters_json)
    model = default_model(
        seed=args.seed,
        parameters=load_parameters(args.parameters_json),
        config_overrides={"enable_sampling_noise": False, "ra_enable_noise": False},
    )
    require(
        args.train > int(np.prod(model.nominal_decoder.spec.shape)) + 1,
        "training count must exceed all weight and offset coefficients",
    )
    require(
        all(
            math.isfinite(value)
            for value in (args.training_peak_v, args.holdout_peak_v, args.holdout_dc_v)
        )
        and 0 < args.training_peak_v <= model.cfg.v_fs
        and args.holdout_peak_v > 0
        and abs(args.holdout_dc_v) + args.holdout_peak_v < model.cfg.v_fs,
        "declared training/holdout voltages exceed the static reference domain",
    )
    args.out.mkdir(parents=True, exist_ok=False)
    try:
        result = run(args, model)
        write_json(args.out / "summary.json", result)
    except Exception as error:
        source_root = Path(adi_model.__file__).resolve().parent
        current_sources = {
            name: digest(path) for name, path in sorted(loaded_sources(source_root).items())
        }
        snapshot = args.out / "source_snapshot_before.json"
        before = json.loads(snapshot.read_text()) if snapshot.is_file() else {}
        write_json(
            args.out / "failure.json",
            {
                "status": "FAIL",
                "exception": type(error).__name__,
                "reason": str(error),
                "producer_sha256": digest(Path(__file__)),
                "source_sha256": current_sources,
                "source_unchanged": all(
                    before.get(name) == value for name, value in current_sources.items()
                ),
                "training_reference_seed": args.training_reference_seed,
                "seed": args.seed,
                "training_peak_v": args.training_peak_v,
                "training_samples": args.train,
                "partial_artifacts_retained": True,
            },
        )
        raise
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "rank",
                    "nominal_error",
                    "calibrated_error",
                    "independent_integer_words_matched",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
