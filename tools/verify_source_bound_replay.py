#!/usr/bin/env python3
"""用独立输入和实际有序计划重放同一静态失配行为器件。

输入为已执行 RTL case 的刺激、8文件源绑定、实际plant参数及5份CSV；本工具不运行RTL或训练。
先逐笔核对ID/word/reference、物理选择、两采样端口、残差和ADC2，再用严格v2观测与独立整数oracle对拍。
plant数值只进入模拟重放，不是数字学习器输入。不接受旧单源ABI，也不由最终码猜缺失事件。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import numpy as np

if __package__:
    from .run_open_rtl import validate_inputs
    from .run_static_calibration import independent_integer_codes, loaded_sources, require
else:
    from run_open_rtl import validate_inputs
    from run_static_calibration import independent_integer_codes, loaded_sources, require
import adi_model
from adi_model.simulation.config import Config
from adi_model.topology.dac_arch import NominalCodebook, SplitChip
from adi_model.control.dem import SamplingConnections, SplitSwitchCommand
from adi_model.control.dither import Fig19DitherProgram, Fig19StaticSpec
from adi_model.contracts.fixed_point import FixedPointReconstructor
from adi_model.topology.mapper import Mapper
from adi_model.simulation.pipeline import run_pipeline
from adi_model.rtl_observation import extract_bitfields, observations_from_rtl
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.control.timing import SlicePlan

CSV_HEADERS = {
    "controls.csv": [
        "sample_id",
        "word",
        "reference_v",
        "actual_control_word",
        "acquiring_ids",
        "control_valid",
        "sadc_shift_q",
        "rdac_shift_q",
    ],
    "samples.csv": [
        "sample_id",
        "word",
        "reference_v",
        "sadc_shift_q",
        "rdac_shift_q",
        "sadc_sample_v",
        "rdac_sample_v",
        "acquiring_mask",
        "acquiring_ids",
    ],
    "residues.csv": [
        "sample_id",
        "word",
        "coarse_final",
        "rdac_final",
        "reference_v",
        "ra_v",
        "adc2_code",
        "analog_bad",
        "selected_mask",
        "main_physical",
        "sub_physical",
        "converting_ids",
        "next_acquiring_ids",
    ],
    "rdac_events.csv": [
        "sample_id",
        "event_index",
        "phase",
        "coarse_prefix",
        "rdac_code",
        "word",
        "accepted",
        "bound_valid",
        "bound_error",
        "common_q",
        "selected_mask",
        "main_physical",
        "sub_physical",
    ],
    "events.csv": [
        "epoch",
        "kind",
        "sample_id",
        "code",
        "flags",
        "analog_bad",
        "packet_hex",
        "caaz_hex",
        "status_word",
        "analog_sticky",
        "conversion_fault",
    ],
}


def target_observation(data, count):
    """按预先声明的目标窗口取首N笔；尾部物理记录仍另行核对和保存。"""
    return replace(
        data,
        **{
            name: getattr(data, name)[:count]
            for name in (
                "slice_ids",
                "adc2_code",
                "main_masks",
                "sub_masks",
                "signal_mask",
                "known_voltage_term_v",
                "overflow",
                "sample_ids",
            )
        },
    )


def sha(path):
    require(
        path.is_file() and path.stat().st_size < 300_000_000,
        "missing input or file exceeds the supported300MB limit",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_rows(path):
    require(
        path.is_file() and path.stat().st_size < 300_000_000,
        "missing CSV or file exceeds the supported300MB limit",
    )
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == CSV_HEADERS[path.name], "CSV schema differs: " + path.name)
        result = list(reader)
    if not result or any(None in row or None in row.values() for row in result):
        raise ValueError(f"empty or malformed trace: {path}")
    return result


def unsigned_hex_lines(path, bits, count):
    """载荷每行一个无符号hex词；先核词法/宽度/行数，再作signed解释。"""
    require(
        path.is_file() and path.stat().st_size < 300_000_000,
        "missing or oversized hexadecimal load payload",
    )
    lines = path.read_text(encoding="ascii").splitlines()
    require(len(lines) == count, "hexadecimal load payload line count differs")
    words = []
    for line in lines:
        require(
            re.fullmatch(r"[0-9a-fA-F]+", line) is not None,
            "unsigned hexadecimal load word has invalid syntax",
        )
        require(
            len(line) <= (bits + 3) // 4,
            "unsigned hexadecimal load word exceeds declared character width",
        )
        word = int(line, 16)
        require(word < (1 << bits), "unsigned hexadecimal load word exceeds declared bit width")
        words.append(word)
    return words


def ids(text):
    value = int(text, 16)
    if value >> 40:
        raise ValueError("ordered8x5bit physical IDs exceed their declared field")
    return [value >> 5 * slot & 31 for slot in range(8)]


def masks(geometry, codes, states, slice_ids):
    if np.any(states != 0):
        raise ValueError("this explicit canonical Fig19 binding has no unit DEM states")
    main = np.arange(geometry.n_main)[None, None, :] < (codes // 8)[:, None, None]
    sub = np.arange(geometry.n_sub)[None, None, :] < (codes % 8)[:, None, None]
    shape = (len(codes), geometry.n_active)
    return SplitSwitchCommand(
        np.broadcast_to(main, (*shape, geometry.n_main)).copy(),
        np.broadcast_to(sub, (*shape, geometry.n_sub)).copy(),
    )


def all_signal(geometry, sample_ids, slice_ids, control):
    if control is not None:
        raise ValueError("this replay requires explicitly allSIGNAL acquisition")
    return SamplingConnections(
        np.zeros((*slice_ids.shape, geometry.n_main), int),
        np.zeros((*slice_ids.shape, geometry.n_sub), int),
    )


def check_physical_bus(text, ordered_ids, slot_masks, units, slices, context):
    """核对完整物理散射：有效宽度、选中行mask及未选中行清零。"""
    value = int(text, 16)
    require(0 <= value < (1 << (slices * units)), context + " exceeds declared physical bus width")
    expected = sum(
        int(slot_masks[slot, unit]) << (int(physical) * units + unit)
        for slot, physical in enumerate(ordered_ids)
        for unit in range(units)
    )
    require(value == expected, context + " differs from the complete physical scatter mask")


def verify(args):
    require(not args.out.exists(), "output already exists; refusing overwrite")
    args.out.mkdir(parents=True, exist_ok=False)
    module_root = Path(adi_model.__file__).resolve().parent
    source_before = {p.name: sha(p) for p in module_root.glob("*.py")}
    plant = read_json(args.plant)
    plant_parameters = read_json(args.plant_parameters)
    case = read_json(args.trace / "case_manifest.json")
    validate_case(args, case, plant, plant_parameters)
    cfg = Config.from_dict(read_json(args.config))
    if plant["offset_v"] != 0:
        raise ValueError("this replay has no explicit independent RA-output offset port")
    require(
        (plant["vfs"], plant["adc2_min_v"], plant["adc2_max_v"], plant["ra_clip_v"])
        == (cfg.v_fs, cfg.adc2_v_min, cfg.adc2_v_max, cfg.ra_v_clip),
        "replay contract failed: (plant['vfs'], plant['adc2_min_v'], plant['adc2_max_v'], plant['ra_clip_v']) == (cfg.v_fs, cfg.adc2_v_min, cfg.adc2_v_max, cfg.ra_v_clip)",
    )
    require(
        bool(plant["adc2_max_closed"]) == (cfg.adc2_endpoint_policy == "closed"),
        "replay contract failed: bool(plant['adc2_max_closed']) == (cfg.adc2_endpoint_policy == 'closed')",
    )
    chip = SplitChip(
        **{
            name: plant[name]
            for name in ("C_main", "C_sub", "C_bridge", "c_p_sub", "c_p_main", "C_feedback_true")
        }
    )
    nonuniform = any(
        np.any(value != value.flat[0])
        for value in (chip.C_main, chip.C_sub, chip.C_bridge, chip.c_p_sub, chip.c_p_main)
    )
    samples = csv_rows(args.trace / "samples.csv")
    controls = csv_rows(args.trace / "controls.csv")
    residues = csv_rows(args.trace / "residues.csv")
    events = csv_rows(args.trace / "events.csv")
    rdac_events = csv_rows(args.trace / "rdac_events.csv")
    require(
        all(row["epoch"] == "0" and row["kind"] in ("OBS", "OUT") for row in events),
        "one reset-bounded epoch0 capture with only OBS/OUT events is required",
    )
    require(
        all(
            int(row["status_word"], 16) == 0
            and int(row["analog_sticky"]) == 0
            and int(row["conversion_fault"]) == 0
            for row in events
        ),
        "positive capture contains a protocol/status or sticky analog error",
    )
    sample_ids: np.ndarray = np.asarray([int(row["sample_id"]) for row in samples], np.int64)
    stimulus_words, stimulus_reference, expected_ids = read_stimulus(args.stimulus, case)
    capture_count = case["capture_samples"]
    sampled_count = case["expected_counts"]["samples"]
    np.testing.assert_array_equal(sample_ids, expected_ids[:sampled_count])
    np.testing.assert_array_equal([int(row["sample_id"]) for row in controls], expected_ids)
    np.testing.assert_array_equal([int(row["word"]) for row in controls], stimulus_words)
    np.testing.assert_array_equal(
        [int(row["actual_control_word"]) for row in controls], stimulus_words
    )
    np.testing.assert_array_equal(
        [float(row["reference_v"]) for row in controls], stimulus_reference
    )
    require(
        all(int(row["control_valid"]) == 1 for row in controls),
        "source handshake/control binding was invalid",
    )
    residue_ids = [int(row["sample_id"]) for row in residues]
    require(
        sample_ids.tolist() == residue_ids,
        "replay contract failed: sample_ids.tolist() == residue_ids",
    )
    n = len(samples)
    words: np.ndarray = np.asarray([int(row["word"]) for row in samples], np.int64)
    np.testing.assert_array_equal(words, stimulus_words[:sampled_count])
    require(
        words.tolist() == [int(row["word"]) for row in residues],
        "replay contract failed: words.tolist() == [int(row['word']) for row in residues]",
    )
    reference = np.asarray([float(row["reference_v"]) for row in samples])
    np.testing.assert_array_equal(reference, stimulus_reference[:sampled_count])
    np.testing.assert_array_equal(reference, [float(row["reference_v"]) for row in residues])
    conv = np.asarray([ids(row["converting_ids"]) for row in residues], np.int64)
    acq = np.asarray([ids(row["next_acquiring_ids"]) for row in residues], np.int64)
    sampled_ids = np.asarray([ids(row["acquiring_ids"]) for row in samples], np.int64)
    for index, row in enumerate(samples):
        require(
            int(row["acquiring_mask"], 16)
            == sum(1 << int(physical) for physical in sampled_ids[index]),
            "sampled acquiring mask differs from ordered physical IDs",
        )
    control_ids = np.asarray([ids(row["acquiring_ids"]) for row in controls], np.int64)
    np.testing.assert_array_equal(sampled_ids, control_ids[:sampled_count])
    # 此RTL的advance直接复制acquiring_ids到converting_ids，保留slot顺序。
    require(
        np.array_equal(conv, sampled_ids),
        "converting ID order differs from its sampled acquisition group",
    )
    np.testing.assert_array_equal(acq, control_ids[1 : sampled_count + 1])
    plan = SlicePlan(
        conv,
        acq,
        np.arange(n, dtype=np.int64) - 1,
        np.ones(n, bool),
        "EXACT_OBSERVED_SV_ORDERED_PLAN",
        {"source": sha(args.trace / "residues.csv")},
    )
    prefix_document = read_json(args.prefix_table)
    prefix = np.asarray(prefix_document["event_codes_by_coarse"], np.int64)
    phases = prefix_document["event_phase_before_edge"]
    book = NominalCodebook(0, 511, -cfg.v_fs + 2 * cfg.v_fs * np.arange(512) / 512)
    mapper = Mapper(
        np.arange(512, dtype=np.int64), nominal_codebook=book, event_codes_by_coarse=prefix
    )
    pool = PhysicalSlicePool(
        cfg,
        chip=chip,
        nominal_codebook=book,
        mask_binding=masks,
        acquisition_binding=all_signal,
        schedule_binding=plan,
    )
    specification = Fig19StaticSpec(
        cfg.v_fs,
        1 << 24,
        32,
        (9586981, 4793490, 2396745),
        "subtract",
        "Explicit source-family static fixture, assumed target ports",
    )
    program = Fig19DitherProgram.from_words(words, spec=specification)
    full_program = Fig19DitherProgram.from_words(stimulus_words, spec=specification)
    np.testing.assert_array_equal(
        full_program.sadc_input_shift_q, [int(row["sadc_shift_q"]) for row in controls]
    )
    np.testing.assert_array_equal(
        full_program.rdac_input_shift_q, [int(row["rdac_shift_q"]) for row in controls]
    )

    def stimulus(time_s):
        index = np.rint(np.asarray(time_s) * cfg.fs).astype(np.int64)
        if np.any(index < 0) or np.any(index >= n):
            raise ValueError("reference replay queried outside its recorded stimulus")
        return reference[index]

    raw = run_pipeline(
        cfg,
        stimulus,
        n,
        pool=pool,
        mapper=mapper,
        sample_ids=sample_ids,
        dem_states=np.zeros(n, np.int64),
        sampled_top_v=0.0,
        sampled_subnode_v=0.0,
        reference_high_v=plant["vfs"],
        reference_low_v=-plant["vfs"],
        nominal_reference_high_v=cfg.v_fs,
        nominal_reference_low_v=-cfg.v_fs,
        static_charge_domain=True,
        dither_program=program,
    )
    np.testing.assert_array_equal(raw.sample_id, sample_ids)
    np.testing.assert_array_equal(raw.conv_slice_ids, conv)
    np.testing.assert_array_equal(raw.acq_slice_ids, sampled_ids)
    np.testing.assert_array_equal(raw.next_acq_slice_ids, acq)
    np.testing.assert_array_equal(
        program.sadc_input_shift_q, [int(row["sadc_shift_q"]) for row in samples]
    )
    np.testing.assert_array_equal(
        program.rdac_input_shift_q, [int(row["rdac_shift_q"]) for row in samples]
    )
    sadc_error = np.max(
        np.abs(raw.sample.x_sadc - [float(row["sadc_sample_v"]) for row in samples])
    )
    rdac_error = np.max(
        np.abs(raw.sample.rdac_input_v - [float(row["rdac_sample_v"]) for row in samples])
    )
    require(
        max(sadc_error, rdac_error) < 1e-14,
        "replay contract failed: max(sadc_error, rdac_error) < 1e-14",
    )
    np.testing.assert_array_equal(raw.coarse, [int(row["coarse_final"]) for row in residues])
    np.testing.assert_array_equal(raw.k, [int(row["rdac_final"]) for row in residues])
    np.testing.assert_array_equal(raw.adc2_code, [int(row["adc2_code"]) for row in residues])
    np.testing.assert_array_equal(
        raw.analog_overflow, [bool(int(row["analog_bad"])) for row in residues]
    )
    ra_error = float(np.max(np.abs(raw.vra - [float(row["ra_v"]) for row in residues])))
    require(ra_error < 1e-12, "replay contract failed: ra_error < 1e-12")
    for i, row in enumerate(residues):
        require(
            int(row["selected_mask"], 16) == sum(1 << int(physical) for physical in conv[i]),
            "replay contract failed: int(row['selected_mask'], 16) == sum((1 << int(physical) for physical in conv[i]))",
        )
        for field, unit_count, expected in (
            ("main_physical", 63, raw.main_masks),
            ("sub_physical", 8, raw.sub_masks),
        ):
            check_physical_bus(
                row[field],
                conv[i],
                expected[i],
                unit_count,
                pool.geometry.n_slices,
                "residue " + field,
            )
    require(
        len(rdac_events) == n * len(phases),
        "replay contract failed: len(rdac_events) == n * len(phases)",
    )
    events_by_id: dict[int, list[dict[str, str]]] = {}
    for row in rdac_events:
        events_by_id.setdefault(int(row["sample_id"]), []).append(row)
    require(
        set(events_by_id) == set(map(int, sample_ids)), "resolved-event sample identities differ"
    )
    for i, sample_id in enumerate(sample_ids):
        rows = events_by_id[int(sample_id)]
        require(
            [int(row["event_index"]) for row in rows] == list(range(len(phases))),
            "replay contract failed: [int(row['event_index']) for row in rows] == list(range(len(phases)))",
        )
        require(
            [int(row["phase"]) for row in rows] == phases,
            "replay contract failed: [int(row['phase']) for row in rows] == phases",
        )
        np.testing.assert_array_equal(
            raw.dither_trace.prefix_before_upper[i], [int(row["coarse_prefix"]) for row in rows]
        )
        np.testing.assert_array_equal(
            raw.dither_trace.rdac_event_codes[i], [int(row["rdac_code"]) for row in rows]
        )
        require(
            all(
                int(row["word"]) == words[i]
                and int(row["common_q"]) == program.known_common_q[i]
                and (int(row["accepted"]) == int(row["bound_valid"]) == 1)
                and (int(row["bound_error"]) == 0)
                for row in rows
            ),
            "replay contract failed: all((int(row['word']) == words[i] and int(row['common_q']) == program.known_common_q[i] and (int(row['accepted']) == int(row['bound_valid']) == 1) and (int(row['bound_error']) == 0) for row in rows))",
        )
        for row in rows:
            command = masks(
                pool.geometry,
                np.asarray([int(row["rdac_code"])]),
                np.zeros(1, np.int64),
                conv[i : i + 1],
            ).validated(pool.geometry, 1)
            require(
                int(row["selected_mask"], 16) == sum(1 << int(v) for v in conv[i]),
                "resolved event selected a different physical group",
            )
            for field, count, expected in (
                ("main_physical", 63, command.main_on[0]),
                ("sub_physical", 8, command.sub_on[0]),
            ):
                check_physical_bus(
                    row[field],
                    conv[i],
                    expected,
                    count,
                    pool.geometry.n_slices,
                    "resolved event " + field,
                )
    decoder = FixedPointReconstructor.from_dict(read_json(args.registers))
    validate_load_payloads(args, decoder, chip, case)
    data = raw.digital_observation
    require(data is not None, "replay contract failed: data is not None")
    data = target_observation(data, capture_count)
    manifest = json.loads((args.inputs / "rtl_input_manifest.json").read_text())
    for name, expected in manifest["artifact_sha256"].items():
        require(
            sha(args.inputs / name) == expected,
            "replay contract failed: sha(args.inputs / name) == expected",
        )
    schema = read_json(args.inputs / "packet_schema.json")
    obs_rows = [row for row in events if row["kind"] == "OBS"]
    np.testing.assert_array_equal(
        [int(row["sample_id"]) for row in obs_rows], expected_ids[:capture_count]
    )
    decoded = [
        extract_bitfields(
            int(row["packet_hex"], 16),
            field_order_msb_to_lsb=schema["field_order_msb_to_lsb"],
            field_widths=schema["field_widths"],
        )
        for row in obs_rows
    ]
    for field, expected in (
        ("sample_id", expected_ids[:capture_count]),
        ("coarse", raw.coarse[:capture_count]),
        ("state", raw.sid[:capture_count]),
        ("control_word", words[:capture_count]),
    ):
        require(
            [row[field] for row in decoded] == expected.tolist(),
            "packet " + field + " differs from actual controls or sample identity",
        )

    def connection(spec, controls):
        declared = Fig19DitherProgram.from_words(
            controls["control_word"].astype(np.int64), spec=specification
        )
        np.testing.assert_array_equal(controls["known_common_q"], declared.known_common_q)
        mask = np.concatenate((controls["main_masks"], controls["sub_masks"]), axis=2)
        return (
            np.ones_like(mask),
            spec.v_fs * (controls["known_common_q"][:, None, None] / 2**32 + 1 - 2 * mask),
        )

    packet_data = observations_from_rtl(
        [row["packet_hex"] for row in events if row["kind"] == "OBS"],
        data.spec,
        packet_schema=schema,
        binding_document=(args.inputs / "binding.json").read_bytes(),
        connection_binding=connection,
        static_charge_domain=True,
        fixed_configuration_epoch=True,
    )
    for name in (
        "sample_ids",
        "slice_ids",
        "adc2_code",
        "main_masks",
        "sub_masks",
        "signal_mask",
        "overflow",
    ):
        np.testing.assert_array_equal(getattr(packet_data, name), getattr(data, name))
    np.testing.assert_array_equal(
        np.rint(packet_data.known_voltage_term_v / cfg.v_fs * 2**32),
        np.rint(data.known_voltage_term_v / cfg.v_fs * 2**32),
    )
    data = packet_data
    codes = decoder.reconstruct(data)
    oracle_words, oracle_low, oracle_high = independent_integer_codes(
        decoder.to_dict(), data, program.known_common_q[:capture_count]
    )
    np.testing.assert_array_equal(codes.code, oracle_words)
    np.testing.assert_array_equal(codes.clipped_low, oracle_low)
    np.testing.assert_array_equal(codes.clipped_high, oracle_high)
    retired = [row for row in events if row["kind"] == "OUT"]
    require(
        [int(row["sample_id"]) for row in retired] == expected_ids[:capture_count].tolist(),
        "replay contract failed: [int(row['sample_id']) for row in retired] == sample_ids.tolist()",
    )
    # 实际bench先记录本ID的OBS，OUT同沿也必须经过observed_by_id资格。
    # 这里只检查每ID的文件行因果，不据此推绝对tick或不同ID之间的交错。
    seen_observations: set[int] = set()
    for row in events:
        sample_id = int(row["sample_id"])
        if row["kind"] == "OBS":
            seen_observations.add(sample_id)
        else:
            require(sample_id in seen_observations, "OUT precedes its matching OBS")
    np.testing.assert_array_equal(codes.code, [int(row["code"]) for row in retired])
    np.testing.assert_array_equal(
        8 * codes.clipped_low + 16 * codes.clipped_high, [int(row["flags"]) for row in retired]
    )
    np.testing.assert_array_equal(
        codes.analog_overflow, [bool(int(row["analog_bad"])) for row in retired]
    )
    np.savez_compressed(
        args.out / "physical_replay.npz",
        sample_ids=raw.sample_id,
        conv_slice_ids=raw.conv_slice_ids,
        acquired_slice_ids=raw.acq_slice_ids,
        next_acquired_slice_ids=raw.next_acq_slice_ids,
        coarse=raw.coarse,
        rdac_code=raw.k,
        main_masks=raw.main_masks,
        sub_masks=raw.sub_masks,
        sadc_input_v=raw.sample.x_sadc,
        rdac_sampled_input_v=raw.sample.rdac_input_v,
        ra_v=raw.vra,
        adc2_macro_target=raw.adc2_code,
        raw_invalid=raw.analog_overflow,
    )
    np.savez_compressed(
        args.out / "target_integer_words.npz",
        sample_ids=data.sample_ids,
        actual=np.asarray([int(row["code"]) for row in retired], np.int64),
        production=codes.code,
        independent=oracle_words,
        clipped_low=codes.clipped_low,
        clipped_high=codes.clipped_high,
        analog_invalid=codes.analog_overflow,
    )
    source_after = {name: sha(path) for name, path in sorted(loaded_sources(module_root).items())}
    require(
        source_after == {name: source_before[name] for name in source_after},
        "replay contract failed: source_after == {name: source_before[name] for name in source_after}",
    )
    receipt = {
        "status": "PASS",
        "scope": "SOURCE_BOUND_STATIC_SAME_PLANT_ORDERED_PLAN_REPLAY",
        "rtl_executed_by_this_tool": False,
        "nonuniform_physical_parameters": nonuniform,
        "samples": capture_count,
        "program_samples": len(stimulus_words),
        "physical_samples_replayed": n,
        "actual_ordered_ids_and_controls_replayed": True,
        "integer_words_matched": capture_count,
        "sadc_input_max_error_v": float(sadc_error),
        "rdac_input_max_error_v": float(rdac_error),
        "ra_max_error_v": ra_error,
        "rdac_prefix_events_matched": len(rdac_events),
        "strict_ABI2_packet_checked": args.inputs is not None,
        "rtl_input_sha256": manifest["input_sha256"],
        "rtl_bundle_artifact_sha256": manifest["artifact_sha256"],
        "case_load_input_sha256": case["input_sha256"],
        "normal_capture_status_errors": 0,
        "normal_capture_analog_sticky": 0,
        "normal_capture_conversion_fault": 0,
        "event_epoch": 0,
        "per_id_observation_precedes_retirement": True,
        "capture_scope": "N target retirements with three accepted, unretired explicit postamble records; not a paused or drained producer",
        "unretired_postamble": [
            {
                "sample_id": int(expected_ids[i]),
                "word": int(stimulus_words[i]),
                "reference_v": float(stimulus_reference[i]),
                "source_accepted": True,
                "sampled_and_residue_replayed": i < sampled_count,
                "backend_adc2_evidence": "plant_target_only"
                if i < sampled_count
                else "not_sampled",
                "adc2_observation_or_retirement": False,
                "ra_v": float(raw.vra[i]) if i < sampled_count else None,
                "adc2_macro_target": int(raw.adc2_code[i]) if i < sampled_count else None,
                "plant_analog_invalid": bool(raw.analog_overflow[i]) if i < sampled_count else None,
                "retired": False,
            }
            for i in range(capture_count, len(expected_ids))
        ],
        "source_sha256": source_after,
        "producer_sha256": sha(Path(__file__)),
        "helper_sha256": {
            "run_static_calibration.py": sha(Path(__file__).with_name("run_static_calibration.py")),
            "run_open_rtl.py": sha(Path(__file__).with_name("run_open_rtl.py")),
        },
        "input_sha256": {
            "plant": sha(args.plant),
            "plant_parameters": sha(args.plant_parameters),
            "stimulus": sha(args.stimulus),
            "case_manifest": sha(args.trace / "case_manifest.json"),
            "config": sha(args.config),
            "registers": sha(args.registers),
            "prefix_table": sha(args.prefix_table),
            **{
                name: sha(args.trace / name)
                for name in (
                    "controls.csv",
                    "samples.csv",
                    "residues.csv",
                    "rdac_events.csv",
                    "events.csv",
                )
            },
        },
        "not_verified": [
            "physical transistor timing",
            "actual reference accuracy",
            "source full chip network",
        ],
    }
    receipt["artifact_sha256"] = {p.name: sha(p) for p in args.out.iterdir() if p.is_file()}
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    (args.out / "SHA256SUMS").write_text(
        "".join(
            f"{sha(path)}  {path.name}\n"
            for path in sorted(args.out.iterdir())
            if path.is_file() and path.name != "SHA256SUMS"
        )
    )
    return receipt


def read_json(path):
    require(path.is_file() and path.stat().st_size < 300_000_000, "missing/oversized JSON input")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field: " + key)
            result[key] = value
        return result

    def bad_constant(name):
        raise ValueError("nonfinite JSON constant: " + name)

    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant
    )
    require(isinstance(value, dict), "explicit JSON object required")
    return value


def read_stimulus(path, case):
    require(path.is_file() and path.stat().st_size < 300_000_000, "missing/oversized stimulus")
    rows = [line.split() for line in path.read_text().splitlines()]
    require(
        len(rows) == case["program_samples"] and all(len(row) == 3 for row in rows),
        "stimulus row count/three-column format differs",
    )
    words: np.ndarray = np.asarray([int(row[0]) for row in rows], np.int64)
    reference = np.asarray([float(row[1]) for row in rows])
    require(
        np.all(np.isfinite(reference)) and all(int(row[2]) == 0 for row in rows),
        "this complete positive replay requires finite reference and expected_reject=0; no rows are dropped",
    )
    require(
        all(int(row[0]) == 0 and float(row[1]) == 0.0 and int(row[2]) == 0 for row in rows[-3:]),
        "three explicit legal zero postamble inputs are required",
    )
    ids = (case["first_sample_id"] + np.arange(len(rows), dtype=np.int64)) & 0xFFFF_FFFF
    return words, reference, ids


def validate_case(args, case, plant, physical):
    require(
        case.get("schema_version") == 2
        and case.get("capture_mode") == "FINITE_WINDOW_CONTINUOUS_SOURCE"
        and case.get("sample_id_bits") == 32
        and case.get("sample_id_increment") == 1,
        "explicit case identity convention required",
    )
    count = case.get("capture_samples")
    require(
        type(count) is int
        and 1 <= count <= 8192
        and case.get("expected_good") == count
        and case.get("program_samples") == count + 3
        and case.get("postamble_records") == 3
        and case.get("sampled_postamble_records") == 2
        and case.get("unretired_postamble_records") == 3
        and case.get("positive_capture") is True
        and case.get("normal_protocol_status_required_zero") is True,
        "positive replay requires the complete independently counted case",
    )
    expected_counts = {
        "good": count,
        "source_accepts": count + 3,
        "samples": count + 2,
        "residues": count + 2,
        "rdac_events": 7 * (count + 2),
        "observations": count,
        "outputs": count,
        "capture_samples": count,
        "program_samples": count + 3,
        "postamble_sampled": 2,
        "unretired_postamble": 3,
        "finite_capture": 1,
    }
    require(
        case.get("expected_counts") == expected_counts,
        "finite capture source/sample/residue/retirement counts differ",
    )
    require(
        type(case.get("first_sample_id")) is int and case["first_sample_id"] == 2,
        "explicit first uint32 sample ID required",
    )
    require(
        case.get("stimulus_columns") == ["word", "reference_v", "expected_reject"],
        "stimulus columns differ",
    )
    require(
        case.get("plant_sha256") == sha(args.plant_parameters),
        "actual physical-parameter bytes differ from the DUT case",
    )
    required = {"C_main", "C_sub", "C_bridge", "c_p_sub", "c_p_main", "C_feedback_true"}
    require(
        set(physical) == required and all(physical[key] == plant[key] for key in required),
        "plant metadata does not describe the actual loaded physical parameters",
    )
    payload_hashes = case.get("input_sha256")
    require(
        isinstance(payload_hashes, dict)
        and set(payload_hashes)
        == {
            "stimulus.txt",
            "registers.json",
            "weights.hex",
            "scalars.hex",
            "plant_effective_weights_only.txt",
        },
        "case load input hash inventory must contain exactly the five consumed payloads",
    )
    for name, path in (
        ("stimulus.txt", args.stimulus),
        ("registers.json", args.registers),
        ("weights.hex", args.trace / "weights.hex"),
        ("scalars.hex", args.trace / "scalars.hex"),
    ):
        require(
            case["input_sha256"].get(name) == sha(path), "actual load/input SHA mismatch: " + name
        )
    # 严格读JSON后，统一验证实际8文件库存与5份原始输入字节的完整来源图。
    manifest = read_json(args.inputs / "rtl_input_manifest.json")
    packet = read_json(args.inputs / "packet_schema.json")
    read_json(args.inputs / "binding.json")
    raw_format = read_json(args.inputs / "format.json")
    for path in args.inputs.iterdir():
        require(
            not path.is_symlink(), "source bundle members must be copied files, not path aliases"
        )
        require(
            not path.is_file() or path.stat().st_size < 300_000_000,
            "source bundle member exceeds the supported300MB limit",
        )
    bundle = validate_inputs(args.inputs)
    require(
        packet.get("input_sha256") == bundle["input_sha256"],
        "packet schema raw input hashes do not bind the actual five copied inputs",
    )
    require(
        packet.get("fixed_point_format") == raw_format and manifest.get("format") == raw_format,
        "source bundle format declarations differ from the actual format input",
    )
    actual_config = json.loads(
        json.dumps(Config.from_dict(read_json(args.config)).to_dict(), allow_nan=False)
    )
    require(
        manifest.get("config") == actual_config,
        "source bundle Config declaration differs from the actual input",
    )
    require(
        sha(args.config) == manifest["input_sha256"]["config_json"],
        "physical replay Config is not the actual RTL input Config",
    )
    require(
        plant["sampled_top_v"] == plant["sampled_subnode_v"] == 0.0,
        "this replay requires explicitly declared zero static reset",
    )
    require(
        plant["actual_reference_high_v"] == plant["nominal_reference_high_v"] == plant["vfs"]
        and plant["actual_reference_low_v"] == plant["nominal_reference_low_v"] == -plant["vfs"],
        "declared actual and nominal rails differ from the supported symmetric fixture",
    )


def validate_load_payloads(args, decoder, chip, case):
    weights = unsigned_hex_lines(
        args.trace / "weights.hex", decoder.format.coefficient_bits, decoder.weights_q.size
    )
    require(
        weights == decoder.weights_q.ravel().tolist(),
        "DUT loaded weights differ from the supplied register image",
    )
    bits = decoder.format.voltage_bits
    scalar_words = unsigned_hex_lines(args.trace / "scalars.hex", bits, 3)
    scalars = [word - (1 << bits) if word >= 1 << (bits - 1) else word for word in scalar_words]
    require(
        scalars == [decoder.offset_q, decoder.adc2_min_q, decoder.adc2_max_q],
        "DUT loaded scalars differ from the register image",
    )
    beta = chip.C_bridge / (chip.C_bridge + chip.C_sub.sum(axis=1) + chip.c_p_sub)
    plant_weights = (
        np.concatenate((chip.C_main, beta[:, None] * chip.C_sub), axis=1) / chip.require_feedback()
    )
    declared_bytes = "".join(f"{float(weight):.17g}\n" for weight in plant_weights.flat).encode()
    require(
        hashlib.sha256(declared_bytes).hexdigest()
        == case["input_sha256"].get("plant_effective_weights_only.txt"),
        "SV plant weights do not correspond to the same actual capacitance file",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in (
        "trace",
        "inputs",
        "plant",
        "plant-parameters",
        "stimulus",
        "config",
        "prefix-table",
        "registers",
        "out",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    require(not args.out.exists(), "output already exists; refusing overwrite")
    try:
        result = verify(args)
    except Exception as error:
        if args.out.is_dir():
            (args.out / "failure.json").write_text(
                json.dumps(
                    {
                        "status": "FAIL",
                        "reason": str(error),
                        "exception": type(error).__name__,
                        "producer_sha256": sha(Path(__file__)),
                    },
                    indent=2,
                )
                + "\n"
            )
        raise
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "scope", "samples", "integer_words_matched", "ra_max_error_v")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
