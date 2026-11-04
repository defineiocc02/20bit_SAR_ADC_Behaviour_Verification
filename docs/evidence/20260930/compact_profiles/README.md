# Compact production RTL: P5/P6/P7 regression archive

These are actual local Verilator 5.49 RTL simulations of the frozen compact production top. They are not mocked results, a Vivado/netlist simulation, or timing closure. Each profile passed three modes, 438 calibrated output comparisons, 7,680 protocol observations, coverage of 18 physical slices, and 438 exact reconstruction latency checks. The observed accepted-start-to-output latencies were P5=15, P6=13 and P7=11 clock intervals.

The frozen 26-file RTL aggregate SHA-256 is `fd885e882ad5447f87626dc034fb59a6853a86f01050957a93b8a6407ebbbc13`. Relative to the preceding `169f957dc7fe...` frozen RTL set, only `rtl/core/weight_store.sv` changed: a legal coefficient write explicitly clears the bit already proved zero by the accepted range. `rtl_compact_identity.json` contains every individual RTL digest and the aggregate algorithm. This is a content identity, not a Git commit.

`p5/run.log`, `p6/run.log`, `p7/run.log`, `manifest.json`, `evidence_hashes.json`, and `rtl_compact_identity.json` are byte-identical copies of the external run artifacts. `archive_manifest.json` verifies those archive bytes. The copied original manifest includes the commands, tool identity, source digests, subprocess return codes, and successful before/after source verification.

The original `evidence_hashes.json` deliberately remains unchanged: its paths are relative to the external full run directory, not a claim that every listed file is in this compact archive. Full frozen source copies, C++ objects/binaries, build logs, version record and `run_profiles.py` remain outside Git at:

`/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_vivado_20260930/compact_profiles/`

That external run used its own read-only copies from `repair_compact/`; it never overwrote historical `final_profiles`. It did not repeat the public-port mapped-TB qualification or other RTL benches.

## Reproduction

Use the matching production RTL and `sim/tb/structural_adc_tb.sv`, and verify all digests in the manifest before running. The exact host-specific commands are retained in `manifest.json`. The portable equivalent for each P=5,6,7 is:

```sh
verilator --binary --timing --assert --unroll-count 8192 -j 2   -Wno-fatal -Werror-PINMISSING -Werror-SELRANGE   --top-module structural_adc_tb -Irtl/params   -GP_RECON_STAGES=5 --Mdir /absolute/new/build-p5   -f rtl/rtl_sources.f sim/tb/structural_adc_tb.sv
/absolute/new/build-p5/Vstructural_adc_tb
```

Change both the profile parameter and new build directory for P6/P7. The captured macOS run additionally used C++20, host C++ `-O0`, an explicit make `PYTHON3`, and the local PCH workaround; these are recorded verbatim. `-O0` is a host compiler setting and makes no FPGA timing claim. Require exit code 0 and the exact completion line for each profile; a generic PASS substring alone is insufficient.
