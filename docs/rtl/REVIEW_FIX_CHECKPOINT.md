# Review fixes checkpoint — 2026-09-19

Base: df0a5754560964b6b4d494357d034d1b64873d27.
Branch: codex/rtl-review-fixes-20260919.
User requested publishing review findings and a branch with corrected RTL.

Milestones:
1. Repair dither recurrence and specify/test its probability distribution.
2. Enforce complete, atomic configuration activation and reject protocol collisions.
3. Require complete mutation runs; preserve observability semantics separately.
4. Add actual open-source RTL simulation CI, regression evidence and review report.
5. Publish branch/PR, inspect exact-head CI, report unresolved timing/analog scope.

Open findings to record, not falsely close: 40 MS/s clock/II architecture;
physical AZ noise and dielectric absorption; VCS/DC/PVT signoff.

Status (2026-09-20): milestones 1–4 complete. Six actual Verilator testbenches
passed: review_dither, review_config, p1, p2_periph, p2_smoke, p3_top.
Old dither and old configuration each failed new RTL regressions as expected.
Actual Verilator mutation controls: div-shift killed, capacity-guard deletion
unobserved; both complete 4095 rows. Python: 541 passed, 3 remote tests deselected.
ruff/format and mypy passed. New configuration protocol is ADR 0016; detailed
findings and local evidence are REVIEW_20260919.md and evidence/review_20260919.json.
Remaining: publish branch/PR and inspect exact-head CI. Main must not be merged.
Local simulator: PyPI verilator 5.48.0 wheel (binary reports development 5.49),
installed outside repo; macOS wheel PCH flags supplied through VERILATOR override.
CI uses Ubuntu 24.04 apt Verilator; CI is a separate portability check.
Local review evidence: ../counterexamples.json and ../GitHub更新复核_20260919.md.
