"""一笔真实样本的完整链路追踪（供 docs/mechanisms 的两篇专题引用）。

必须说清的事：

* 数据来自**一次真实 pipeline 运行**（dither 关闭），dither 的模拟注入由
  `Fig19DitherProgram` 按同一笔的六位字解析后**叠加到已记录的量上**；
  由于 A6（环内 dither）尚未接入主循环，这不是"一次跑完的 dither 样本"，
  而是"真实记录 + 显式程序"的组合。文档里必须这样标注。
* RDAC 的逐事件码由 `follower` 用已决前缀生成，与主链共用同一张前缀表。

运行：
    PYTHONPATH=src python3 tools/trace_one_sample.py --out docs/mechanisms/data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adi_model.dem3d import Dem3State, round_robin_plan, unary_rotation_masks
from adi_model.dither import FIG19_FRACTIONAL_WEIGHTS_Q, Fig19DitherProgram, Fig19StaticSpec
from adi_model.follower import ResolvedPrefixes, run_follower
from adi_model.slice_pool import PhysicalSlicePool
from tools.run_p4_ablation import FS_HZ, LSB20, V_FS, _build_model, _run

SAMPLE_INDEX = 97
DITHER_WORD = 21  # {D6,D5,D4,D3,D2,D1} = 0,1,0,1,0,1 -> U=0, L=2*step + (4+1)/7*step


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/mechanisms/data")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    model = _build_model(1e-3, True)
    n = 256
    plan = round_robin_plan(n)
    pool = PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=unary_rotation_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )
    t = np.arange(n) / FS_HZ
    values = 2.4 * np.sin(2 * np.pi * 250_000 * t)
    raw, voltage = _run(
        model, lambda tt: 2.4 * np.sin(2 * np.pi * 250_000 * tt), n, dem=False, uniform_plan=False
    )
    index = SAMPLE_INDEX
    coarse = int(np.asarray(raw.coarse)[index])
    x = float(values[index])
    state = Dem3State.sequence(n, columns=63)[index]

    # dither：同一笔字 -> lower/upper -> 等效 LSB
    spec = Fig19StaticSpec(
        v_fs=V_FS,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="tools/trace_one_sample.py explicit binding (US10505561 Fig19)",
    )
    program = Fig19DitherProgram.from_words(np.array([DITHER_WORD]), spec=spec)
    lower_lsb = float(program.lower_q[0]) / spec.step_q
    upper_lsb = int(program.upper_code[0])
    sadc_input = x - float(program.lower_v[0])

    # RDAC 逐事件
    prefixes = ResolvedPrefixes.from_final(np.array([coarse], dtype=np.int64))
    trace = run_follower(
        pool,
        prefixes,
        np.array([state], dtype=np.int64),
        slice_ids=plan.conv[index : index + 1],
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
    )
    events = [int(v) for v in np.asarray(trace.applied_codes)[0]]
    charges = [float(v) for v in np.asarray(trace.cumulative_abs_charge_c)[0]]

    # 掩码：该笔实际与 DEM 状态
    ids = [int(v) for v in plan.conv[index]]
    masks = unary_rotation_masks(
        pool.geometry,
        np.array([coarse], dtype=np.int64),
        np.array([state], dtype=np.int64),
        plan.conv[index : index + 1],
    )
    main_on = np.asarray(masks.main_on)[0]
    sub_on = np.asarray(masks.sub_on)[0]

    # 有 dither 时的码：用与主链相同的 513 点门限重算；模拟尾巴（RA/ADC2）不重算，
    # 因为 A6 未接主链，不存在"带 dither 的 RA/ADC2 记录"。
    thresholds = np.asarray(model.cfg.sadc_thresholds_v, dtype=float)
    with_dither_code = int(np.clip(np.searchsorted(thresholds, sadc_input) - 1, 0, 511))
    shifted_prefixes = ResolvedPrefixes.from_final(np.array([with_dither_code], dtype=np.int64))
    shifted_trace = run_follower(
        pool,
        shifted_prefixes,
        np.array([state], dtype=np.int64),
        slice_ids=plan.conv[index : index + 1],
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
    )

    record = {
        "sample_index": index,
        "sample_time_ns": float(t[index] * 1e9),
        "input_v": x,
        "loop_status": "A6 (in-loop dither) not wired: only the digital/RDAC part can be composed",
        "no_dither": {
            "sadc_input_v": x,
            "coarse_code": coarse,
            "coarse_nominal_v": float(
                np.asarray(model.codebook.evaluate(np.array([coarse])))[0]
            ),
            "rdac_event_codes": events,
            "rdac_cumulative_abs_charge_pc": [c * 1e12 for c in charges],
            "ra_input_residue_v": float(np.asarray(raw.residue)[index]),
            "adc2_code": int(np.asarray(raw.adc2_code)[index]),
            "reconstructed_v": float(np.asarray(voltage)[index]),
            "reconstruction_error_lsb20": float(
                (np.asarray(voltage)[index] - x) / LSB20
            ),
        },
        "with_dither": {
            "dither_word": DITHER_WORD,
            "dither_bits_D6_to_D1": [int(b) for b in f"{DITHER_WORD:06b}"],
            "lower_lsb": lower_lsb,
            "upper_lsb": upper_lsb,
            "lower_v": float(program.lower_v[0]),
            "total_v": float(program.total_v[0]),
            "sadc_input_v": sadc_input,
            "coarse_code": with_dither_code,
            "rdac_event_codes": [int(v) for v in np.asarray(shifted_trace.applied_codes)[0]],
            "rdac_cumulative_abs_charge_pc": [
                float(v) * 1e12 for v in np.asarray(shifted_trace.cumulative_abs_charge_c)[0]
            ],
            "ra_adc2": "not available: requires A6 in-loop dither",
        },
        "dem_state": {"horizontal": int(state) & 0x3F, "vertical": int(state) >> 6},
        "physical_slices": ids,
        "main_units_on_per_slice": main_on.sum(axis=1).tolist(),
        "sub_units_on_per_slice": sub_on.sum(axis=1).tolist(),
        "main_unit_index_slice0": np.flatnonzero(main_on[0]).tolist(),
        "sub_unit_index_slice0": np.flatnonzero(sub_on[0]).tolist(),
        "provenance": {
            "no_dither": "single real pipeline run (dither disabled)",
            "with_dither": "same sample; code recomputed from the 513-point SADC thresholds; analog tail absent",
            "prefix_table": "complete_flash3_sar6_prefix_table (same as main chain)",
        },
    }
    (out / "one_sample_trace.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in {"main_units_on_per_slice"}},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
