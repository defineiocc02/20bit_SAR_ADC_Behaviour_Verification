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
Published: Issue #2 and PR #3; initial head 69d8770. Git Data API preserved the
exact local commit/tree after Git transport failed. Initial Linux CI passed five
RTL benches but Verilator 5.020 rejected forcing input variables in P3. Follow-up
uses named top-level nets and fixes smoke input/invalid TB selects, promotes those
warning classes to errors, and makes added test IO explicitly UTF-8. Local RTL
benches passed again; targeted Python 36 passed. Remaining: publish this follow-up
and inspect its exact-head CI. Main must not be merged.
Local simulator: PyPI verilator 5.48.0 wheel (binary reports development 5.49),
installed outside repo; macOS wheel PCH flags supplied through VERILATOR override.
CI uses Ubuntu 24.04 apt Verilator; CI is a separate portability check.
Local review evidence: ../counterexamples.json and ../GitHub更新复核_20260919.md.


RTL/synthesis follow-up (2026-09-20):
- Previous head 940a657: all 9 GitHub CI jobs passed, run 35458856309.
- Replace full 1278-word combinational sum with exact accepted-write accumulation.
- Correct compile/ultra dispatch, zero IO delay, pF load units, sampled TNS,
  process/status conflict and negative-fractional WNS acceptance.
- 7 actual RTL benches passed locally. P2 arithmetic: 1,071,625 checks, including
  every one of 1,048,576 oracle codes, zero errors (~371 s). P2 peripheral: 708
  checks including independent full-array sum invariant. P3: 4095 complete rows.
- Full Python run: 560 passed, 3 remote slow deselected (initial 19 synth tests).
  Final synthesis-driver suite: 29 passed after adding load/WNS cases. Lint,
  formatting, mypy and Bash syntax passed. Latest CI must verify the final tree.
- Detailed structural analysis: RTL_SYNTHESIS_REVIEW_20260920.md; evidence:
  evidence/synthesis_followup_20260920.json. No measured new DC/PPA results.
- Next: publish this milestone to existing PR #3, inspect exact-head CI; do not merge.
