# Published row-cache RTL CI evidence (417f)

RTL job [109627112125](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36633152469/job/109627112125) completed successfully in run 36633152469: **22 bench completion markers, 3 strict lint profiles, independent arithmetic audit and 272 flow unit/mock checks**. Simulator: `Verilator 5.020 2024-01-01 rev (Debian 5.020-1)`. The new weight-row-cache regression reports 5 geometries and 16150 steps. Every bench is individually listed in `manifest.json`; this is measured job output, not a prior-run assumption.

PR head `417f261f24fe917552ac71f6aadb6b3dbf779c5f` and actual checkout merge `63e9887fa0faf23565c35b6bd1a4edfa9eb8edd7` have the same full Git tree `f0be3b81c49fdf17f3113331b7429a559d08aa7b`. GitHub tree blob IDs were checked against immutable head content for all 88 selected RTL/testbench/vector/runner inputs and 31 flow/test/fixture inputs. The 26 production RTL files have content identity `1e3fab94b7b29b77113fa2e18eae99b6ee1c180cd7df6004625c85358392eef4` (not a Git commit SHA). The arithmetic manifest's source hashes match this tested tree. Presence in the input list does not mean every vector was exercised.

Individual artifact member bytes are unchanged. `sha256.json` hashes the deliverables; `manifest.json` records each ZIP member, original ZIP/full job log hashes, source binding, and selected coverage. `physical_flow_checks.log` and `job_excerpt.log` are raw-byte excerpts with original line numbers, not full logs. Lint success is grounded in the completed runner step and its nonzero-exit rejection, not merely empty log files.

At retrieval the workflow is `in_progress` / `None`. Only this completed RTL job is certified here; the final full-workflow observation is separate. These regressions and mocks are not actual synthesis/place/route, full-top mapped equivalence, analog performance or board/silicon signoff. Older f028 and row-cache local evidence are preserved; later RTL/source edits need their own verification.

No EDA or simulation was rerun, and no production source or Git commit was changed to produce this archive.
