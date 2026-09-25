"""Render current-schema acceptance results without stale numerical narrative."""

from __future__ import annotations

import base64
import json
from html import escape
from pathlib import Path

from .acceptance import acceptance_records, gate


def _table(rows, columns):
    heading = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            rendered = f"{value:.6g}" if isinstance(value, float) else str(value)
            cells.append(f"<td>{escape(rendered)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        "<div class='scroll'><table><thead><tr>"
        + heading
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def render_report(results: dict, figures_dir=None) -> str:
    """Build a self-contained report from actual records and current gate status.

    HTML escapes all source/result text. Unknown/undefined numerical values are
    shown explicitly through the schema metadata and raw evidence sections.
    No static headline numbers are copied from an earlier release.
    """
    verdict = gate(results)
    records = acceptance_records(results)
    status = "PASS" if verdict.ok() else "FAIL / INCOMPLETE"
    body = [
        f"<h1>20-bit SAR ADC 行为验证</h1><p class='status'>{status}</p>",
        f"<p>验收记录 {len(records)} 条，通过 {sum(records.values())} 条。"
        "本报告基于当前 results.json；行为仿真通过不代表晶体管、功耗或流片验证。</p>",
        "<h2>实验与来源口径</h2><p>新闭合实验采用 9 位判决、18 slice / 8 active、63+8 分段候选。"
        "历史 stages 中的 Config() 保留 7 位假设基线；两类结果不混合作为一个芯片的性能。"
        "94.2 dB/9.3 nV 与 94.6 dB/8.8 nV 分别来自论文和幻灯片。"
        "RA 噪声使用目标 DR 标定；匹配目标属于预算一致性检查。</p>",
    ]
    if not verdict.ok():
        body.append("<pre>" + escape("\n".join(verdict.lines())) + "</pre>")
    holdout = results.get("noisy_weight_holdout", {})
    if holdout:
        body += [
            "<h2>带噪训练与独立定点验证</h2><p>每颗芯片训练后冻结权重；验证使用独立波形和噪声。"
            "RMS 单位为 V；系数 SE 为有效 C/Cf 单位。最终码采用 Q30/Q32、96 位累加器和 20 位 offset binary。"
            "参考源系统误差未从这些样本中推断。</p>",
            _table(
                holdout.get("rows", []),
                [
                    ("chip_seed", "芯片 seed"),
                    ("training_samples", "训练样本"),
                    ("rank", "秩"),
                    ("condition", "条件数"),
                    ("coefficient_se_rms", "系数 SE RMS"),
                    ("before_error_rms_v", "校准前 RMS [V]"),
                    ("after_error_rms_v", "最终码 RMS [V]"),
                    ("SNDR_dB", "SNDR [dB]"),
                    ("ENOB", "ENOB"),
                    ("overflow_count", "溢出数"),
                ],
            ),
            _table(
                holdout.get("benchmarks", []),
                [
                    ("source", "来源"),
                    ("dr_db", "DR [dB]"),
                    ("nsd_v_rthz", "NSD [V/√Hz]"),
                    ("noise_rms_from_dr_v", "由 DR 推算 RMS [V]"),
                    ("noise_rms_from_flat_nsd_v", "平坦谱积分 RMS [V]"),
                ],
            ),
        ]
    slow = results.get("long_record_noise", {})
    if slow:
        body += [
            "<h2>低频状态验证</h2><p>这是共享物理时间的慢噪声与理想抗混叠观测通道；"
            "不构成全速整链噪声或 auto-zero 电路验证。短 FFT 仍不能辨识 40 Hz 转角。</p>",
            _table(
                [slow],
                [
                    ("adc_fs_hz", "ADC 时钟 [Hz]"),
                    ("probe_fs_hz", "观测率 [Hz]"),
                    ("duration_s", "时间 [s]"),
                    ("low_cutoff_hz", "假设低截止 [Hz]"),
                    ("psd_resolution_hz", "PSD 分辨率 [Hz]"),
                    ("slope_db_decade", "斜率 [dB/dec]"),
                    ("band_power_v2", "带内功率 [V²]"),
                    ("band_prediction_v2", "解析预测 [V²]"),
                ],
            ),
            _table(
                slow.get("mode_convergence", []),
                [("modes", "模式数"), ("covariance_relative_rms", "协方差相对 RMS 误差")],
            ),
        ]
    body += [
        "<h2>参考、放大与输入机制</h2><p>共享 Rs 的连续输入网络、有符号参考电荷、有限 RA/ADC2 响应、"
        "可用量化码驱动的预跟踪均在实际数据路径内。参考负载线性化于名义电压；"
        "峰值 droop 明确限制小信号适用范围。相位时长和具体 SAR 试探次序为候选假设。"
        "KTC 观察器属于研究扩展，其未量化电压校正不能进入当前定点接口。</p>"
    ]
    for key in ("pipeline", "il_offset", "dither_quant", "rdac_bitwise"):
        # results[key] 可能是 None（v8 的 null-undefined 口径）——or {} 兜底，
        # 否则 .get 直接 AttributeError（独立审查 2026-09-25）
        entry = results.get(key) or {}
        body.append(f"<h3>{escape(key)}</h3><p>{escape(str(entry.get('判据','未提供证据')))}</p>")
        body.append(
            "<details><summary>数值证据</summary><pre>"
            + escape(json.dumps(entry, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )
    boundary = results.get("s13", {}).get("rho_boundary")
    if boundary:
        body.append(
            "<h3>Ron 扫描的条件性边界</h3><p>未跨越目标时不发布 rho 上限；2.2 LSB 是工程扫描门限。"
            "2.2 ppmFS 换算为约 2.307 LSB20。静态条件均值不能替代完整码密度 INL/DNL。</p><pre>"
            + escape(json.dumps(boundary, ensure_ascii=False, indent=2))
            + "</pre>"
        )
    body += [
        "<h2>验收清单</h2>",
        _table(
            [{"id": k, "result": "PASS" if v else "FAIL"} for k, v in sorted(records.items())],
            [("id", "判据"), ("result", "结果")],
        ),
    ]
    if figures_dir is not None:
        for path in sorted(Path(figures_dir).glob("*.png")):
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            body.append(
                f"<details><summary>实验图：{escape(path.name)}（各 stage 自身配置）</summary><img alt='{escape(path.name)}' src='data:image/png;base64,{encoded}'></details>"
            )
    body.append(
        "<h2>完整数据与未定义数字</h2><p>null 与 undefined_numeric_values 记录未定义值的位置和类型；"
        "没有将其替换成有限性能数字。原始失败判据保持失败。</p><details><summary>完整 results.json</summary><pre>"
        + escape(json.dumps(results, ensure_ascii=False, indent=1))
        + "</pre></details>"
    )
    style = "body{font:16px/1.6 system-ui,sans-serif;color:#1e293b;background:#f8fafc;margin:0}main{max-width:1280px;margin:auto;padding:36px}h1,h2,h3{line-height:1.3}h2{margin-top:42px}table{border-collapse:collapse;width:100%;background:white}td,th{border:1px solid #cbd5e1;padding:9px;text-align:left;white-space:nowrap}th{background:#e2e8f0}.scroll{overflow:auto;margin:18px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#e2e8f0;padding:16px}img{max-width:100%}.status{font-weight:700;color:#075985}details{margin:16px 0}summary{cursor:pointer}"
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>SAR ADC 验收报告</title><style>"
        + style
        + "</style><main>"
        + "".join(body)
        + "</main></html>"
    )
