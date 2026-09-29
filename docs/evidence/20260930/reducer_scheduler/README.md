# Reducer miter: Verilator 5.020 scheduling compatibility

The testbench now passes **1,430,848 checked samples across all seven geometries**, including all **43,758** production allocations, on both Verilator 5.020 and the recorded 5.49 development build. Each sample checks the old and new reducers against an independent physical-cell scalar sum and against one another. The original per-geometry count assertions, stimuli, seeds, reference-module body, and `--unroll-count 8192` remain intact.

This change is confined to the testbench. It does **not** modify production arithmetic or supply STA/analog-performance evidence.

## Observed failure and repair

- `before_5020.run.log`: untouched original seven-instance bench reproduces CI check13 failure, exit 134.
- `before_5020_observed.run.log`: a separate generated-C++ copy with printf-only observation shows TB weight00=1 and both active masks=0xff, but both totals/gains remain zero at checks9–12. At check13 the old rail result is -32 and the new result is 0; the intended values are T=24, G=24, R=-24. This is more than an output-concatenation comparison issue.
- `cxx_observation.patch` and `.json`: exact observation-only changes and generated-source hashes. This diagnostic run is not an acceptance simulation. Wide C++ storage padding is not interpreted as valid SystemVerilog bits.
- The repaired bench starts after 0.5ns, captures the complete input transaction on a positive test-clock edge, and checks on the negative edge. Inputs remain stable after that check. One transaction still occupies 1ns. Moving checking into one event process also avoids duplicating the arithmetic checker throughout timed stimulus tasks.
- `after_5020.run.log` and `after_549.run.log`: both exit 0 with exactly seven case markers, the original exact counts, and `CAL_WEIGHT_REDUCE_PPA_COMPLETE`.
- `common_stale_mutation_5020.run.log`: the negative control forces the shared sampled weight matrix to zero while preserving raw stimulus and oracle. Both models then see zero, but the independent oracle catches check9 (actual T/G/R=0/0/0 versus 24/24/24), exit 134. The archived mutation fixture contains this deliberate error and must not replace the production testbench.

No upstream bug number is asserted. Small probes were sensitive to compiler structure; aborted exploratory builds are excluded from passing evidence. The old reference body is byte-identical; its hash is in `manifest.json`.

## Reproduce

Run the normal current bench from the repository root with a suitable Verilator command:

```sh
VERILATOR="verilator -CFLAGS '-std=c++20'" python tools/run_open_rtl.py --tops cal_weight_reduce_ppa_tb
```

The exact archived runs used only the reducer and the bench, in TB-first source order. For a focused replay, choose `sar_tb` as the current bench, the original fixture, or the deliberate mutation fixture:

```sh
sar_tb=sim/tb/cal_weight_reduce_ppa_tb.sv
# sar_tb=docs/evidence/20260930/reducer_scheduler/fixtures/original_miter.sv
# sar_tb=docs/evidence/20260930/reducer_scheduler/fixtures/common_stale_mutation.sv
sar_build=$(mktemp -d "${TMPDIR:-/tmp}/sar_reducer.XXXXXX")
verilator --binary --timing --assert --unroll-count 8192 -j 2 -Wno-fatal \
  -CFLAGS '-std=c++20' --top-module cal_weight_reduce_ppa_tb -Irtl/params \
  --Mdir "$sar_build" "$sar_tb" rtl/core/cal_weight_reduce.sv
"$sar_build/Vcal_weight_reduce_ppa_tb"
```

On this macOS host, official 5.020 additionally needed `-I/tmp/sar_verilator_5_020_build/compat` inside `-CFLAGS` because the installed libc++ removed `experimental/coroutine`. `tool_5020_provenance.json` records that external header adapter and the unmodified official translator commit/hash. Its earlier diagnostic-status fields are historical tool-build records; the current acceptance results are in `manifest.json`. The 5.49 wheel used `-MAKEFLAGS 'PYTHON3=python3 CFG_CXXFLAGS_PCH_I=-include'`; its `--version` string is recorded verbatim, and the generated simulator reports Verilator 5.49.

The current bench must exit 0 with all counts; the original and deliberate mutation fixtures are expected to fail on the recorded 5.020 configuration. Tool/compiler changes can alter how the original scheduling defect manifests.

`sha256.json` covers every archived file except itself. `manifest.json` binds results to the reducer, parameter header, and repaired repository TB hashes. The earlier [`final_rtl`](../final_rtl/) snapshot is preserved as historical evidence; this directory records the later miter verification and does not silently replace that snapshot.
