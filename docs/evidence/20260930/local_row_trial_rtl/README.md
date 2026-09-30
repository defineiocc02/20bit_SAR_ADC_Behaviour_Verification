# Local row-update RTL candidate: 22 functional regressions

The candidate changes only `rtl/core/weight_store.sv` relative to the cached P5 frozen source. `source_manifest.json` lists the exact 26-file RTL identity; the archive script checked it against the working tree before copying. `result.json` records the runner hash. All 22 benches report their required completion markers under Verilator 5.020, and three production lint profiles pass. Build/run logs and the tool version are preserved in `logs/`. `sha256.json` binds the copied files.

This is zero-delay digital functional evidence, not a netlist equivalence proof, routed timing success, analog reproduction or ASIC PPA result. The candidate's final acceptance depends on actual same-constraint Vivado synthesis and physical implementation.

The same frozen candidate also passed actual 18-slice top-level P5 and P6 structural runs (`profiles/`): each checked three dither modes, 438 outputs, 7,680 protocol cycles and 438 latency observations. Observed latencies were 15 and 13 clocks, respectively; the default P7 run above observed 11 clocks. `profiles/sha256.json` binds those extra logs.
