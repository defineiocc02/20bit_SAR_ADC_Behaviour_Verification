"""对实际 Fig19 组件采集执行公开重放与身份损坏反例。

每个模块只编译、运行一次公开 64 字组件入口，不训练；其后复制实际产物做反例。
这些检查覆盖理想双采样端口和数字身份合同，不证明完整芯片网络或工艺时序。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _environment():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1")
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(ROOT / "src"), env.get("PYTHONPATH")) if value
    )
    return env


def _snapshot(folder):
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _replay(capture, trace, out, *, inputs=None):
    inputs = capture.root / "inputs" if inputs is None else inputs
    arguments = {
        "trace": trace,
        "inputs": inputs,
        "plant": capture.root / "plant_metadata.json",
        "plant-parameters": capture.root / "plant_replay_only.json",
        "stimulus": trace / "stimulus.txt",
        "config": inputs / "config.json",
        "prefix-table": ROOT / "sim/fixtures/fig19_ideal_ports/rdac_prefix_table.json",
        "registers": trace / "registers.json",
        "out": out,
    }
    command = [sys.executable, str(ROOT / "tools/verify_source_bound_replay.py")]
    for name, path in arguments.items():
        command.extend(("--" + name, str(path)))
    return subprocess.run(
        command, cwd=out.parent, env=_environment(), text=True, capture_output=True, timeout=90
    )


@pytest.fixture(scope="module")
def actual_capture(tmp_path_factory):
    parent = tmp_path_factory.mktemp("source_bound_actual")
    output = parent / "capture"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_fig19_rtl_calibration.py"),
            "--component-only",
            "--out",
            str(output),
        ],
        cwd=parent,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=360,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["execution_mode"] == "COMPONENT_ONLY_NO_TRAINING"
    assert manifest["calibration"]["status"] == "NOT_RUN"
    assert not (output / "frozen_fit.json").exists()
    assert not (output / "fitted_registers.json").exists()
    compilation = json.loads((output / "compile.log.command.json").read_text())
    assert compilation["raw_returncode"] == 0
    case = output / "component_shared64"
    counts = json.loads((case / "case_manifest.json").read_text())["expected_counts"]
    assert counts["capture_samples"] == counts["outputs"] == counts["observations"] == 64
    assert counts["program_samples"] == counts["source_accepts"] == 67
    assert counts["samples"] == counts["residues"] == 66
    assert counts["rdac_events"] == 462 and counts["unretired_postamble"] == 3
    _, sampled = _csv(case / "samples.csv")
    assert [int(row["word"]) for row in sampled[:64]] == list(range(64))
    capture = SimpleNamespace(root=output, trace=case)
    baseline = _replay(capture, case, parent / "positive_replay")
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    capture.receipt = json.loads((parent / "positive_replay/receipt.json").read_text())
    before = _snapshot(output)
    yield capture
    assert _snapshot(output) == before, "negative tests modified the actual capture baseline"


def _csv(path):
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def _write_csv(path, columns, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _edit_packets(rows, schema, field, update, *, require_change=True):
    shift = 0
    offsets = {}
    for name in reversed(schema["field_order_msb_to_lsb"]):
        width = schema["field_widths"][name]
        offsets[name] = shift, width
        shift += width
    offset, width = offsets[field]
    mask = (1 << width) - 1
    for row in rows:
        if row["kind"] != "OBS":
            continue
        packet = int(row["packet_hex"], 16)
        current = packet >> offset & mask
        updated = update(current)
        assert 0 <= updated <= mask
        assert not require_change or updated != current
        packet = (packet & ~(mask << offset)) | (updated << offset)
        row["packet_hex"] = f"{packet:x}"


def _swap_slots(value, width):
    mask = (1 << width) - 1
    low, high = value & mask, value >> width & mask
    return (value & ~((1 << (2 * width)) - 1)) | (low << width) | high


def _permute_packet_slots(rows, schema):
    _edit_packets(rows, schema, "slice_ids", lambda value: _swap_slots(value, 5))
    # 同次命令在各活动槽位的 canonical mask 相同。仍交换相应位段，不能靠
    # 未变的最终和证明有序身份一致。
    for field, width in (("main_masks", 63), ("sub_masks", 8)):
        _edit_packets(
            rows,
            schema,
            field,
            lambda value, width=width: _swap_slots(value, width),
            require_change=False,
        )


def test_public_replay_preserves_the_actual_finite_window_and_explicit_tail(actual_capture):
    receipt = actual_capture.receipt
    assert receipt["status"] == "PASS"
    assert receipt["scope"] == "SOURCE_BOUND_STATIC_SAME_PLANT_ORDERED_PLAN_REPLAY"
    assert receipt["rtl_executed_by_this_tool"] is False
    assert receipt["nonuniform_physical_parameters"] is True
    assert receipt["samples"] == receipt["integer_words_matched"] == 64
    assert receipt["program_samples"] == 67 and receipt["physical_samples_replayed"] == 66
    assert receipt["rdac_prefix_events_matched"] == 462
    assert receipt["strict_ABI2_packet_checked"] is True
    assert receipt["actual_ordered_ids_and_controls_replayed"] is True
    tail = receipt["unretired_postamble"]
    assert [row["sample_id"] for row in tail] == [66, 67, 68]
    assert [row["sampled_and_residue_replayed"] for row in tail] == [True, True, False]
    assert all(row["source_accepted"] and not row["retired"] for row in tail)
    assert all(not row["adc2_observation_or_retirement"] for row in tail)
    assert all(
        receipt[name] == 0
        for name in (
            "normal_capture_status_errors",
            "normal_capture_analog_sticky",
            "normal_capture_conversion_fault",
            "event_epoch",
        )
    )


@pytest.mark.parametrize(
    "damage",
    [
        "packet_ids_shifted_together",
        "row_and_packet_ids_shifted_together",
        "packet_coarse",
        "packet_state",
        "packet_word",
        "packet_ordered_ids_and_masks",
        "conversion_order_disagrees_with_sampling",
        "packet_main_mask",
        "missing_observation",
        "duplicate_observation",
        "out_before_matching_obs",
        "all_outputs_before_observations",
        "missing_prefix_event",
        "sampled_word",
        "sampled_acquiring_mask",
        "rdac_sampled_node",
        "residue_unselected_main",
        "residue_unselected_sub",
        "prefix_unselected_main",
        "prefix_unselected_sub",
        "residue_above_main",
        "residue_above_sub",
        "prefix_above_main",
        "prefix_above_sub",
        "actual_control_word",
        "source_status",
        "conversion_fault",
        "epoch",
        "postamble_changed_to_target",
        "bundle_manifest_input_config_json",
        "bundle_manifest_input_format_json",
        "bundle_manifest_input_binding_json",
        "bundle_manifest_input_source_sv",
        "bundle_manifest_input_acquisition_sv",
        "bundle_source_artifact_only",
        "bundle_acquisition_artifact_only",
        "bundle_missing_input_digest",
        "bundle_extra_input_digest",
        "bundle_unregistered_source_file",
        "scalar_64bit_alias",
        "scalar_overwide_zero",
        "scalar_signed_zero",
        "scalar_missing_row",
        "scalar_extra_row",
        "scalar_nonhex",
    ],
)
def test_public_replay_rejects_corrupted_actual_capture(actual_capture, tmp_path, damage):
    trace = tmp_path / "damaged_capture"
    shutil.copytree(actual_capture.trace, trace)
    schema = json.loads((actual_capture.root / "inputs/packet_schema.json").read_text())
    inputs = None
    if damage.startswith("bundle_"):
        inputs = tmp_path / "damaged_inputs"
        shutil.copytree(actual_capture.root / "inputs", inputs)
        path = inputs / "rtl_input_manifest.json"
        manifest = json.loads(path.read_text())
        if damage.startswith("bundle_manifest_input_"):
            field = damage.removeprefix("bundle_manifest_input_")
            manifest["input_sha256"][field] = "0" * 64
        elif damage in {"bundle_source_artifact_only", "bundle_acquisition_artifact_only"}:
            filename = (
                "source_control_binding.sv"
                if damage == "bundle_source_artifact_only"
                else "source_acquisition_binding.sv"
            )
            source = inputs / filename
            source.write_bytes(
                source.read_bytes() + b"\n// Test-only identity mutation, not executed.\n"
            )
            manifest["artifact_sha256"][filename] = hashlib.sha256(source.read_bytes()).hexdigest()
            assert (
                manifest["artifact_sha256"][filename]
                != json.loads(path.read_text())["artifact_sha256"][filename]
            )
        elif damage == "bundle_missing_input_digest":
            del manifest["input_sha256"]["format_json"]
        elif damage == "bundle_extra_input_digest":
            manifest["input_sha256"]["undeclared_input"] = "0" * 64
        else:
            assert damage == "bundle_unregistered_source_file"
            (inputs / "unregistered.sv").write_text("module unregistered; endmodule\n")
        path.write_text(json.dumps(manifest) + "\n")
        # 外层所有登记产物的摘要仍与实际字节相同；拒绝必须来自来源身份链或库存。
        assert all(
            hashlib.sha256((inputs / name).read_bytes()).hexdigest() == expected
            for name, expected in manifest["artifact_sha256"].items()
        )
    elif damage.startswith("scalar_"):
        path = trace / "scalars.hex"
        lines = path.read_text().splitlines()
        assert int(lines[0], 16) == 0, "this explicit nominal fixture has zero offset"
        if damage == "scalar_missing_row":
            lines.pop()
        elif damage == "scalar_extra_row":
            lines.append("0000000000000000")
        else:
            lines[0] = {
                "scalar_64bit_alias": f"{1 << 64:x}",
                "scalar_overwide_zero": "0" * 17,
                "scalar_signed_zero": "-0",
                "scalar_nonhex": "not_hex",
            }[damage]
        path.write_text("\n".join(lines) + "\n")
        manifest_path = trace / "case_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["input_sha256"]["scalars.hex"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest) + "\n")
    elif damage == "conversion_order_disagrees_with_sampling":
        path = trace / "residues.csv"
        columns, residues = _csv(path)
        sample_id = residues[1]["sample_id"]
        packed = int(residues[1]["converting_ids"], 16)
        changed = _swap_slots(packed, 5)
        assert changed != packed
        assert sorted(packed >> (5 * slot) & 31 for slot in range(8)) == sorted(
            changed >> (5 * slot) & 31 for slot in range(8)
        )
        residues[1]["converting_ids"] = f"{changed:x}"
        _write_csv(path, columns, residues)
        path = trace / "events.csv"
        columns, events = _csv(path)
        observation = next(
            row for row in events if row["kind"] == "OBS" and row["sample_id"] == sample_id
        )
        _permute_packet_slots([observation], schema)
        _write_csv(path, columns, events)
    elif damage.startswith("packet_") or damage in {
        "row_and_packet_ids_shifted_together",
        "missing_observation",
        "duplicate_observation",
        "out_before_matching_obs",
        "all_outputs_before_observations",
        "source_status",
        "conversion_fault",
        "epoch",
    }:
        path = trace / "events.csv"
        columns, rows = _csv(path)
        if damage in {"packet_ids_shifted_together", "row_and_packet_ids_shifted_together"}:
            _edit_packets(rows, schema, "sample_id", lambda value: value + 1)
            if damage.startswith("row_"):
                for row in rows:
                    row["sample_id"] = str(int(row["sample_id"]) + 1)
        elif damage in {"packet_coarse", "packet_state", "packet_word", "packet_main_mask"}:
            field = {
                "packet_coarse": "coarse",
                "packet_state": "state",
                "packet_word": "control_word",
                "packet_main_mask": "main_masks",
            }[damage]
            _edit_packets(rows, schema, field, lambda value: value ^ 1)
        elif damage == "packet_ordered_ids_and_masks":
            _permute_packet_slots(rows, schema)
        elif damage == "missing_observation":
            rows.pop(next(index for index, row in enumerate(rows) if row["kind"] == "OBS"))
        elif damage == "duplicate_observation":
            rows.append(dict(next(row for row in rows if row["kind"] == "OBS")))
        elif damage in {"out_before_matching_obs", "all_outputs_before_observations"}:
            before = {kind: [row for row in rows if row["kind"] == kind] for kind in ("OBS", "OUT")}
            if damage == "out_before_matching_obs":
                retired = rows.pop(
                    next(index for index, row in enumerate(rows) if row["kind"] == "OUT")
                )
                observed_at = next(
                    index
                    for index, row in enumerate(rows)
                    if row["kind"] == "OBS" and row["sample_id"] == retired["sample_id"]
                )
                rows.insert(observed_at, retired)
            else:
                rows = [*before["OUT"], *before["OBS"]]
            assert all(
                [row for row in rows if row["kind"] == kind] == before[kind] for kind in before
            )
        else:
            field, value = {
                "source_status": ("status_word", "13"),
                "conversion_fault": ("conversion_fault", "1"),
                "epoch": ("epoch", "1"),
            }[damage]
            rows[0][field] = value
        _write_csv(path, columns, rows)
    elif damage == "missing_prefix_event":
        path = trace / "rdac_events.csv"
        columns, rows = _csv(path)
        rows.pop(3)
        _write_csv(path, columns, rows)
    elif damage.startswith(("residue_", "prefix_")):
        record, position, bank = damage.split("_")
        path = trace / ("residues.csv" if record == "residue" else "rdac_events.csv")
        columns, rows = _csv(path)
        units = 63 if bank == "main" else 8
        field = "main_physical" if bank == "main" else "sub_physical"
        if position == "unselected":
            selected = int(rows[0]["selected_mask"], 16)
            physical = next(physical for physical in range(18) if not (selected >> physical & 1))
            bit = physical * units
        else:
            assert position == "above"
            bit = 18 * units
        rows[0][field] = f"{int(rows[0][field], 16) ^ (1 << bit):x}"
        _write_csv(path, columns, rows)
    elif damage in {
        "sampled_word",
        "sampled_acquiring_mask",
        "rdac_sampled_node",
        "actual_control_word",
    }:
        path = trace / ("controls.csv" if damage == "actual_control_word" else "samples.csv")
        columns, rows = _csv(path)
        if damage == "rdac_sampled_node":
            rows[63]["rdac_sample_v"] = rows[63]["reference_v"]
        elif damage == "sampled_acquiring_mask":
            rows[0]["acquiring_mask"] = f"{int(rows[0]['acquiring_mask'], 16) ^ 1:x}"
        else:
            field = "actual_control_word" if damage == "actual_control_word" else "word"
            rows[0][field] = str(int(rows[0][field]) ^ 32)
        _write_csv(path, columns, rows)
    else:
        assert damage == "postamble_changed_to_target"
        path = trace / "stimulus.txt"
        lines = path.read_text().splitlines()
        lines[-1] = "32 0.31 0"
        path.write_text("\n".join(lines) + "\n")
        manifest_path = trace / "case_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["input_sha256"]["stimulus.txt"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest) + "\n")
    output = tmp_path / "rejected_replay"
    result = _replay(actual_capture, trace, output, inputs=inputs)
    assert result.returncode != 0, result.stdout + result.stderr
    failure = json.loads((output / "failure.json").read_text())
    assert failure["status"] == "FAIL" and failure["reason"]
    assert failure["exception"] in {"ValueError", "AssertionError"}, failure
    expected_guard = {
        "packet_ids_shifted_together": "packet sample_id",
        "row_and_packet_ids_shifted_together": "Arrays are not equal",
        "packet_coarse": "packet coarse",
        "packet_state": "packet state",
        "packet_word": "packet control_word",
        "packet_ordered_ids_and_masks": "Arrays are not equal",
        "conversion_order_disagrees_with_sampling": "converting ID order differs from its sampled acquisition group",
        "packet_main_mask": "Arrays are not equal",
        "missing_observation": "shapes (63,), (64,)",
        "duplicate_observation": "shapes (65,), (64,)",
        "out_before_matching_obs": "OUT precedes its matching OBS",
        "all_outputs_before_observations": "OUT precedes its matching OBS",
        "missing_prefix_event": "len(rdac_events) == n * len(phases)",
        "sampled_word": "Arrays are not equal",
        "sampled_acquiring_mask": "acquiring mask",
        "rdac_sampled_node": "max(sadc_error, rdac_error) < 1e-14",
        "residue_unselected_main": "complete physical scatter mask",
        "residue_unselected_sub": "complete physical scatter mask",
        "prefix_unselected_main": "complete physical scatter mask",
        "prefix_unselected_sub": "complete physical scatter mask",
        "residue_above_main": "declared physical bus width",
        "residue_above_sub": "declared physical bus width",
        "prefix_above_main": "declared physical bus width",
        "prefix_above_sub": "declared physical bus width",
        "actual_control_word": "Arrays are not equal",
        "source_status": "positive capture contains",
        "conversion_fault": "positive capture contains",
        "epoch": "one reset-bounded epoch0",
        "postamble_changed_to_target": "three explicit legal zero postamble",
        "bundle_manifest_input_config_json": "source bundle input hashes",
        "bundle_manifest_input_format_json": "source bundle input hashes",
        "bundle_manifest_input_binding_json": "source bundle input hashes",
        "bundle_manifest_input_source_sv": "source bundle input hashes",
        "bundle_manifest_input_acquisition_sv": "source bundle input hashes",
        "bundle_source_artifact_only": "source bundle input hashes",
        "bundle_acquisition_artifact_only": "source bundle input hashes",
        "bundle_missing_input_digest": "source bundle input hashes",
        "bundle_extra_input_digest": "source bundle input hashes",
        "bundle_unregistered_source_file": "eight exported bundle files",
        "scalar_64bit_alias": "unsigned hexadecimal load word exceeds declared character width",
        "scalar_overwide_zero": "unsigned hexadecimal load word exceeds declared character width",
        "scalar_signed_zero": "unsigned hexadecimal load word has invalid syntax",
        "scalar_missing_row": "hexadecimal load payload line count differs",
        "scalar_extra_row": "hexadecimal load payload line count differs",
        "scalar_nonhex": "unsigned hexadecimal load word has invalid syntax",
    }[damage]
    assert expected_guard in failure["reason"], failure
    assert not (output / "receipt.json").exists()


def test_public_replay_refuses_to_overwrite_previous_output(actual_capture, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    prior = b"keep prior result\n"
    (output / "receipt.json").write_bytes(prior)
    result = _replay(actual_capture, actual_capture.trace, output)
    assert result.returncode != 0
    assert (output / "receipt.json").read_bytes() == prior
    assert not (output / "failure.json").exists()
