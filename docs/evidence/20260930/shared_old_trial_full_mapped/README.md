# Shared selector: full-top mapped functional pass

Vivado/XSim 2018.3 P5 full post-synthesis netlist, SHA-bound to the locally verified synthesis DCP and all 26 frozen RTL files. Three modes have 438 code/flag output rows, 3,900 configuration readbacks, 7,680 protocol checks and three in-flight cancellations. Both vendor-run and independent local trace audit pass. The public trace is byte-identical to the baseline trace.

This is functional simulation without SDF. Nonzero flags and internal launch latency are not exercised in this mapped bench; separate RTL benches cover these cases. Actual routed core setup/hold results are in shared_old_trial_route_25ns. Large netlist/DCP remain outside Git with identity retained in manifests.
