#!/usr/bin/env python3
# =============================================================================
# 文件名     : fifo_crc_probe.py
# 模块名     : AD4080 FIFO 离线 CRC 核对入口
# 版本       : v1.0
# 功能       : 从真实或模拟抓包解析三种模式，输出限定假设内的 CRC 裁定。
# 设计说明   : 报告保留输入哈希；证据来源、候选唯一性和硅片绑定是三项独立结论。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""输入本地 JSON 与可选模型清单，向 stdout 写可复算的 JSON 报告。"""

import argparse
import json
from pathlib import Path
import sys

from capture import load_capture
from crc24 import fit_profiles, load_profiles


# =============================================================================
# 1. 冲突与可区分性的报告
# =============================================================================
def data_conflicts(records):
    # 在同一会话下，确定性 CRC 应为同一数据给出同一线尾。冲突需要核对采集/配置。
    first = {}
    collisions = []
    for record in records:
        if record.crc_wire is None:
            continue
        prior = first.setdefault(record.words, record)
        if record.crc_wire != prior.crc_wire:
            collisions.append([prior.record_id, record.record_id])
    return collisions


def summarize_fit(results, counts, conflicts):
    # 只有联合方程有解的模型能够存活。多项式列表和格式模型都计入歧义范围。
    survivors = [(item, model) for item in results for model in item["models"] if "remaining" in model]
    incomplete = [item["name"] for item in results if not item["derivation"]["complete"]]
    if conflicts:
        disposition = "capture_conflict"
    elif not counts["train"]:
        disposition = "no_lvds_training_frames"
    elif incomplete:
        disposition = "candidate_space_incomplete"
    elif not survivors:
        disposition = "no_surviving_model"
    elif len(survivors) > 1 or survivors[0][1]["remaining"]["free_degrees"]:
        disposition = "ambiguous"
    else:
        item, model = survivors[0]
        # 同一消息重传用于重复性检查。留出使训练增秩时，不能称为独立预测。
        if not item["distinct_holdout_messages"]:
            disposition = "no_distinct_holdout"
        elif model["status"] == "holdout_constrained":
            disposition = "holdout_contributed_fit_constraints"
        else:
            disposition = "unique_within_declared_hypotheses"
    return {"status": disposition, "surviving_models": len(survivors), "incomplete_profiles": incomplete,
            "limits": ["使用固定 0xAA 时，覆盖头的影响可以折入初始化状态。",
                       "CRC 字反射和线位反序可能构成等价表述。",
                       "可区分性仅针对输入假设和已抓数据。"]}


def analyze(capture_path, hypotheses_path=None):
    source, records = load_capture(capture_path)
    conflicts = data_conflicts(records)
    report = {
        "format": "ad4080-fifo-crc24-analysis-v1",
        "source": source,
        "records": [record.summary() for record in records],
        "repeated_payload_conflicting_tails": conflicts,
        "silicon_binding": "synthetic_only" if source["declared"]["kind"] == "synthetic" else "source_review_required",
    }
    if hypotheses_path is not None:
        profiles, digest = load_profiles(hypotheses_path)
        results, counts = fit_profiles(records, profiles)
        report.update({"hypotheses_sha256": digest, "lvds_fit_records": counts,
                       "fit": summarize_fit(results, counts, conflicts), "profiles": results})
    # 解析-only 适合同一清单先核头、数据和后缀。读报告再建立候选有助于排除采样错位。
    return report


# =============================================================================
# 2. 命令行错误和报告输出
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--hypotheses", type=Path)
    args = parser.parse_args()
    try:
        data = analyze(args.capture, args.hypotheses)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2
    # 一组候选不匹配是有效审计结果。只有输入本身损坏才以非零状态退出。
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
