# Final workflow completion for f028

[CI run 36629118047](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36629118047) is **completed / success**, with **9 of 9 jobs successful**. GitHub's final update is `2026-09-29T21:11:01Z`. This is a new, later observation; the earlier `../ci_rtl_f028/` archive and its `in_progress` observation are preserved unchanged.

The tested PR head is `f028ec5bb4f64a79414bac59c1b91bc1eaeee190`; checkout logs show merge `ae11d8b0cf73a47e073dbb5394db64fa91f9229a`. The earlier source archive binds their identical tree `d84022bd83195a9ad6443009f368d3764a0a7d3f`. This is the published predecessor: **later row-cache and other uncommitted RTL candidates are not covered**.

| Actual CPython | pytest passed | Deselected | Runtime (s) | Full acceptance step |
| --- | ---: | ---: | ---: | --- |
| 3.10.21 | 975 | 3 | 750.97 | skipped |
| 3.11.16 | 975 | 3 | 376.99 | skipped |
| 3.12.14 | 975 | 3 | 725.69 | success |
| 3.13.15 | 975 | 3 | 747.74 | skipped |

All four used pytest 9.1.1 with `pytest -m "not slow" -q --cov --cov-report=xml`. These are 975 selected tests per interpreter, not 3900 unique tests. The three deselected cases were not passed. The Python 3.12 acceptance run reports all 52 criteria passed. The determinism job executes two acceptance runs (52 criteria each), then prints `OK: results.json byte-identical across runs`; this certifies that recorded file comparison, not all output images or every random seed.

`workflow_metadata.json` and `jobs_metadata.json` preserve the final API response bytes, including metadata and steps for all nine jobs (four Python versions, lint, typing, wheel, RTL and determinism). `manifest.json` records the conclusions, counts, source binding and precise excerpts. `excerpts/*.excerpt.log` are **selected original byte lines**, not full logs. Their original line numbers/byte offsets and both full-log and excerpt SHA256 hashes are recorded; timestamps, ANSI escapes and line endings were not normalized. Full logs remain at the external `/tmp/sar_ci_workflow_f028_final/` paths recorded in the manifest. `retrieval.json` records endpoints, timestamps and response hashes; `sha256.json` covers all delivered files except itself.

The detailed RTL job evidence remains in `../ci_rtl_f028/` (21 benches, 3 lint profiles, independent arithmetic audit and flow mocks). The acceptance sweep is behavioral software evidence; workflow success is not transistor-level validation, actual FPGA route, timing closure, board validation or silicon measurement. No EDA, simulation rerun, source modification or Git commit was performed for this archive.
