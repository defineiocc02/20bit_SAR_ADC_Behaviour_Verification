# Complete, repaired P7 synthesis A/B evidence

These are the complete report sets for two finished Vivado 2018.3 OOC synthesis runs, both on `xc7vx690tffg1761-2` with `P_RECON_STAGES=7`. Both are **SYNTH_COMPLETE_TIMING_NOT_MET**. The candidate increases LUT use; this experiment does not demonstrate a PPA advantage.

| Report value | Repaired baseline | Candidate | Candidate minus baseline |
| --- | ---: | ---: | ---: |
| LUT | 171,246 | 199,039 | +27,793 (+16.23%) |
| FF | 66,817 | 66,778 | -39 (-0.058%) |
| DSP / BRAM / BUFGCTRL | 20 / 0 / 0 | 20 / 0 / 0 | 0 / 0 / 0 |
| WNS (ns) | -17.426 | -17.112 | +0.314; both fail |
| WHS (ns) | -0.264 | -0.264 | 0; both fail |
| WPWS / TPWS (ns) | +0.025 / 0 | +0.025 / 0 | 0 / 0 |
| Setup failing endpoints | 71,067 | 69,461 | -1,606 |
| Hold failing endpoints | 61,733 | 61,735 | +2 |
| Worst setup path logic levels | 135 | 148 | +13 |

`comparison.json` gives precise values, source differences and scope; each subset's `readings.json` extracts the original reports. Raw timing summaries are authoritative (TNS rounding can differ from a subsequent detailed section).

The two source archives each have 28 members. Exactly `rtl/core/cal_weight_reduce.sv` and `rtl/core/div_floor.sv` differ; all other members, including the synthesis Tcl, are byte-identical. XDC is also byte-identical. Thus this is an A/B test of the **combined** reducer/divider change, not isolated attribution. Neither snapshot contains the later constant-MSB weight-store optimization. The frozen archives must not be replaced by current repository RTL or the later 26-file RTL identity. Rebuilt hierarchy can move logic between source modules; do not sum parent/child rows or interpret hierarchy deltas as isolated module costs.

Both runs requested period 1.5625 ns (Vivado reports 1.563 ns) with user uncertainty 0.050 ns and zero external input/output delays. All 16 explicit `check_timing` counts are zero and the unconstrained-path table is empty under those recorded constraints. This does not establish board timing coverage. Both raw logs report `[Timing 38-242]` because `HD.CLK_SRC` is unset; clock delay/skew estimation is prevented. No BUFGCTRL or actual route is present. Negative setup and hold slack means timing is not closed; the clock's reported 639.795 MHz is a constraint, not achieved Fmax. These reports do not prove full-top mapped functional equivalence, routed timing, analog performance or ASIC signoff.

Both DRC reports contain 49 warnings and zero errors: CFGBVS-1 (1), DPIP-1 (14), DPOP-1 (20), DPOP-2 (14). Source warnings, including declaration-after-use, register-based 3D memory, zero replication and profile trimming, are preserved in `vivado.log`; no `Synth 8-3848` undriven warning is present. The vectorless power estimate is 1.580 W for each (no simulation activity file); it is not a measured or activity-annotated power result.

`baseline/` preserves the recovered complete baseline reports and original recovery/transport records; the original source archive is supplied from the corresponding original run directory. `optimized/` preserves the sealed candidate manifest, the earlier manifest, and the subsequent full DCP verification. The original records are historical and remain unchanged. A normalized return code of 3 records completed synthesis with timing not met, not successful timing closure.

Every subset `archive_index.json` records each original absolute path, raw byte count/hash, archived byte count/hash and any gzip transformation. Reports over 512 KiB are deterministic gzip (level 9, mtime 0, empty filename); all other source packages, reports, logs, scripts, status and manifest files retain original bytes. To restore the large report, run `gzip -dc baseline/out/timing_paths.rpt.gz > /tmp/baseline_timing_paths.rpt` (or the corresponding `optimized/` path). Both raw and compressed SHA256 values are retained and checked. Subset and root `sha256.json` cover all delivered files except their own hash list.

The DCP files are intentionally external; complete file bytes and hashes were checked against recorded remote identities before archival:

- Baseline: `/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_vivado_20260930/repair_baseline/synth/artifacts/vivado/20260929T190955.260429Z_p7_recovered/out/post_synth.dcp`; 69,108,593 bytes; SHA256 `9c3bc09dd193d0bffcd6d00f9af83cc887fde5423402416af751f4a2d1ef506b`.
- Candidate: `/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_vivado_20260930/repair_optimized/synth/artifacts/vivado/20260929T195145.299826Z_p7/out/post_synth.dcp`; 71,421,073 bytes; SHA256 `4712577f4291fed3c43ce9f5987098984cd04a87b9c05ed894b99e282ad3b642`.
