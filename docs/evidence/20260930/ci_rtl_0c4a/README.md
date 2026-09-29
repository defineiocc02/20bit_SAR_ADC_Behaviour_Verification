# CI RTL evidence: head 0c4a, overall job FAILED

The RTL regression step passed all **21 registered benches and 3 strict lint profiles** using **Verilator 5.020 2024-01-01 rev (Debian 5.020-1)** on Ubuntu 24.04. The subsequent independent arbitrary-precision arithmetic-audit step failed before its simulation could run because its wrapper could not find `verilator_bin` (command return 127). The RTL job therefore concluded **failure**. This archive does not label the workflow or job green, and does not claim that independent audit passed.

[Actual job and steps](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/runs/36626662809/job/109605295575) are recorded in `job_metadata.json`. `arithmetic_entry_failure.log` is the small original failing compiler-entry log, not an RTL assertion failure.

## Exact source identity

- PR head: `0c4a7726597c49044d25117a701ba780137ddf04`.
- Actual checked-out and tested PR merge: `308e489f363a6c19f26e28c08c82d8f2a34c2a6a`.
- Both complete Git trees: `d8f838a28634c27e6bda222ea0b40cf102c91225`. Equality was verified using the GitHub merge commit object and local immutable Git head, not assumed from the artifact name.
- The 26-file production RTL content identity is `fd885e882ad5447f87626dc034fb59a6853a86f01050957a93b8a6407ebbbc13`; this is a content-set SHA256, not a Git commit.

`tested_inputs_sha256.json` binds all tracked RTL, TB, parameter/vector files and the relevant runner/audit/workflow tools in that tree. No tracked `sim/reference` files existed. `rtl_content_identity.json` retains the established 26-file hashing method. Current-worktree comparison is recorded separately in `manifest.json`; later changes to the audit tool, physical-flow Tcl or tests are not tested by this older CI run.

## Compact, original evidence

Artifact 11060083889 ZIP SHA256 is `be031a47e765656a5e9383678a9a3b6a05e67f5cde3c75dba6a54151739a9eb3`, matching GitHub's recorded digest. Individual run/lint/version logs are copied byte-for-byte. Build logs and the full job log remain external; the timestamped `job_excerpt.log` keeps only source/version/step/failure evidence, with source line numbers in the manifest. Zero-byte lint logs are corroborated by the successful regression step and the frozen runner's mandatory return-code checks; emptiness alone is not treated as a PASS.

The runner command was `python tools/run_open_rtl.py` without `--tops`; its 21-item registration and required markers were read from the exact tested tree. `p2_tb` delegates its full-code oracle to the separately passing `p2_oracle_tb`. The reducer miter completed all seven geometries; the reconstruction latency bench completed P5/P6/P7. These are RTL simulation results, not mapped-netlist, analog, PPA or STA evidence. No simulation was rerun to create this archive.

| Bench | Completion evidence |
| --- | --- |
| `sar_trial_tb` | `SAR_TRIAL_COMPLETE fine_codes=4096 coarse_codes=512 stalls_and_cancel=PASS` |
| `calibration_recovery_tb` | `CALIBRATION_RECOVERY_COMPLETE samples=2048 max_calibrated_error=4 max_uncalibrated_error=504` |
| `calibration_fit_tb` | `CALIBRATION_FIT_COMPLETE samples=128 fitted_external_weights=1278 cfg_commit=PASS` |
| `structural_adc_tb` | `STRUCTURAL_ADC_COMPLETE modes=3 outputs=438 checks=7680 physical_slices=18 recon_stages=7 recon_latency=11 latency_checks=438` |
| `structural_protocol_tb` | `STRUCTURAL_PROTOCOL_COMPLETE frames=120 launches=57 expected_drops=63 checks=1920 decoder_perturbations=2` |
| `recon_ppa_latency_tb` | `RECON_PPA_PROFILE_PASS stages=7 samples=32 busy_release=10 latency=11 initiation_interval=16` |
| `divider_borrow_tb` | `DIVIDER_BORROW_COMPLETE exhaustive_checks=67592` |
| `tree_mapping_tb` | `TREE_MAPPING_COMPLETE checks=12904` |
| `cal_weight_reduce_ppa_tb` | `CAL_WEIGHT_REDUCE_PPA_COMPLETE` |
| `calibration_physical_tb` | `CALIBRATION_PHYSICAL_COMPLETE samples=2048 physical_slices=18` |
| `review_top_protocol_tb` | `REVIEW_TOP_PROTOCOL_COMPLETE checks=1108 outputs=38` |
| `review_leaf_tb` | `REVIEW_LEAF_COMPLETE checks=91142` |
| `review_recon_protocol_tb` | `REVIEW_RECON_PROTOCOL_COMPLETE` |
| `review_dither_tb` | `REVIEW_DITHER_COMPLETE` |
| `review_config_tb` | `REVIEW_CONFIG_COMPLETE outputs=624` |
| `p2_tb` | `P2 RESULT: PASS` |
| `p2_oracle_tb` | `P2_ORACLE_COMPLETE codes=1048576 errors=0` |
| `p2_periph_tb` | `p2_periph PASS` |
| `p2_smoke_tb` | `p2_smoke PASS` |
| `p1_tb` | `P1 RESULT: PASS` |
| `p3_top_tb` | `P3 TOP RESULT: PASS` |

`manifest.json` records the verification and archive mapping. `sha256.json` hashes every local evidence file except itself.

GitHub API JSON metadata arrived without a final newline. The archived copies append one LF for repository formatting; original response paths/hashes and this exact transformation are recorded in the manifest. Simulation/lint logs are not normalized.
