# Divider borrow rewrite: compact evidence

This directory is a separate source snapshot from the earlier arithmetic audit.
It records the exact floor-preserving borrow/width rewrite, not FPGA/ASIC timing closure.

- `arithmetic.manifest.json` / `arithmetic.run.log`: independent Python integer vectors;
  40,010 MAC, 40,000 ADC2, 10,048 signed-63/unsigned-64 division cases, eight output flag sequences.
- `exhaustive.manifest.json` / `exhaustive.run.log`: 67,592 exhaustive small-width divisions
  over six configurations, including floor inequality, divide-zero, busy rejection, exact latency,
  busy/idle result hold and one-cycle done.
- `mutations.json` and two failing logs: removal of the high-divisor guard or negative floor
  correction is detected by the independent full-width oracle; only temporary copies mutate.
- `source_readback.json`: final manifest source hashes re-read and matched to this checkout.
- `sha256.json`: hashes of the compact files in this directory; excludes itself.

From the repository root, with Verilator available:

```sh
python tools/audit_rtl_arithmetic.py --output-dir /tmp/adc-divider-oracle
python tools/run_open_rtl.py --tops divider_borrow_tb
python docs/evidence/20260930/divider/run_negative_controls.py --repo . --output-dir /tmp/adc-divider-negative
```

The runners honor `VERILATOR`; for local tool/runtime configuration use the project RTL instructions.
The two negative controls must return a detected divider mismatch; tool/build failures cannot count
as a detected mutation. No generated vectors or build products are committed here.

The original incomplete top-level Vivado netlist is only a diagnostic of a serial divider chain.
Only a functionally valid frozen netlist can supply a PPA baseline. ASIC 640 MHz and FPGA achievable
frequency have separate acceptance boundaries.
