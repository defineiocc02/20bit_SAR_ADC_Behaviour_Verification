# Shared per-row old coefficient: actual 25 ns core timing pass

Same completed remote run recovered after the local process session became unavailable; no resynthesis or reimplementation. Vivado 2018.3, xc7vx690tffg1761-2, P5, BUFGCTRL_X0Y0, 25 ns, unchanged XDC/implementation script. Actual complete route: setup WNS +0.946 ns, true register-clock-pin to register-data-pin hold +0.052 ns, zero route/DRC errors and 16 zero check_timing categories. Worst setup is now context slice to rails register, 23.914 ns data with 18.623 ns routed interconnect.

All-path OOC hold remains -2.278 ns (65048 failing endpoints) under zero input minimum delay. Thus REGREG_TIMING_MET=1 and TIMING_MET=0 are both retained; board and full external interface timing are not certified. The core can run 40 MHz under these recorded conditions (16 clocks/sample = 2.5 MS/s). This is not the ASIC 640 MHz/40 MS/s target. Large routed DCP stays remote with verified identity; full synthesis DCP was byte-verified locally.

Original launch manifest, scripts, recovery metadata, raw reports and hashes are preserved. `internal` path queries use explicit sequential clock/data pins. Raw Windows wrapper exit code 0 does not override TIMING_MET=0.
