"""Build a current-schema, self-contained HTML report from actual result records."""

from __future__ import annotations

import json
import os
from pathlib import Path

from adi_model.reporting import render_report

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("ADI_MODEL_RESULTS_DIR") or HERE / "results")


def reject_nonstandard(value):
    raise ValueError(f"nonstandard JSON number {value}; regenerate with the current sweep")


with (OUT / "results.json").open(encoding="utf-8") as stream:
    results = json.load(stream, parse_constant=reject_nonstandard)
report = render_report(results, OUT / "fig")
(OUT / "report.html").write_text(report, encoding="utf-8")
print(f"Report written to {OUT / 'report.html'}")
