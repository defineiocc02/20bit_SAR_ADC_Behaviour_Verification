# ADR 0017: Fixed-phase RTL capture, atomic controls and explicit arithmetic structure

Status: accepted for the review-fix branch, 2026-09-20.
Scope: preserve the current top-level port list, 16-phase schedule and integer
reconstruction result. Extends ADR 0016. These are RTL engineering decisions,
not additional circuit details disclosed by ISSCC or the patents.

## Transaction contract

All digital input signals are synchronous to `clk` and meet setup/hold at their
capture edge. `ready` is a deadline qualifier; it is not an asynchronous handshake.
No synchronizer, variable stall or added pipeline stage is implied.

| Phase before rising edge | Action on that edge |
|---|---|
| 0 | Advance allocation, reset this transaction's input-valid flags |
| 8 | Capture `sadc_code` if `sadc_rdy=1`; otherwise record error 7 |
| 10 | Capture current DEM state, then advance its bank; draw enabled dither |
| 11 | Register physical RDAC switches only if coarse capture succeeded |
| 14 | Capture `adc2_code`, `inj_q`, `rdac_ovf`, `adc2_over`, `ra_sat` and internal RDAC clipping if `adc2_rdy=1`; otherwise error 8 |
| 15 | Start reconstruction only with both captures valid and core idle; unexpected busy records error 9 |

A missing deadline drops that conversion; it does not stop the schedule or
advance the output with stale input. The next conversion can proceed normally.
DEM/allocation continue on scheduled transactions, including dropped ones.
Reconstruction latency remains 11 edges counting the start edge as edge 1.
The sticky analog flag includes events from accepted transactions. Software
must qualify the conversion with `dout_valid`; the aggregate status is not a
per-sample packet and the error field is not a lossless event queue.

`cfg_clear_valid` synchronously cancels pending transactions, resets scheduler,
allocation, DEM, dither, switch registers and reconstruction, and clears load
completeness and diagnostics. No old `dout_valid` may appear after re-enable.
Coefficient storage and previously committed controls remain readable; every
coefficient must be reloaded before the next validation. Interrupted control
writes do not partially modify the retained controls. Counter wrap alone never
re-enters allocator warmup.

## Configuration and errors

0x1018 retains its address and 64-bit bus width. Its defined low bits are now:

| Bit | Meaning |
|---|---|
| 0 | DEM enable |
| 1 | DEM bridge enable |
| 2 | Sampling dither mask enable |
| 3 | Quantizer/RDAC code dither enable |
| 63:4 | Reserved; nonzero writes rejected |

The write captures four bits, preserves the existing three-cycle wait interval,
and commits them together on the third following edge. Validation pauses this
sequencer and is rejected while it is busy. Clear cancels it. Sampling and
quantizer enables cannot both be one at validation (error 10). Both zero selects
off. Modes are frozen while `cfg_ready=1`. Software that previously used value
3 for an implicitly dithered quantizer path must now use 11; value 3 means
DEM+bridge with dither explicitly off.

The internal `calib_regs` interface gains `controls_write`, `controls_data[3:0]`
and `quantizer_dither_en`; the external `sar20_digital_core` ports do not change.
Reset mode defaults follow generated `DITHER_MODE`. Analog dither remains an
external analog-system responsibility. The internal code source is not evidence
that the analog SADC dither injection/transfer mechanism has been reproduced.

Errors 0–6 retain their numeric assignments. New codes are 7 (SADC deadline),
8 (ADC2 deadline), 9 (reconstruction busy) and 10 (conflicting dither modes).
All constants share `rtl/params/rtl_error_codes.vh`, a static interface header
independent of the generated `rtl_params.vh`. Include it within module scope.
The status priority is bus error, calibration error, live weight rejection,
then sticky input deadline error. A successful commit clears configuration
errors; input errors persist to clear/reset. Invalid or unaligned reads return
zero and cannot alias valid weights. Zero/out-of-range weight writes remain
observable after the bus strobe is removed.

`status_clr_value=0x27` identifies only the four sticky bits (0,1,2,5), excluding
the non-sticky clipping flags. Its standalone metadata output is not mapped to
a new top-level address. `recon_core.clr_ovf` clears old flags; a new completion
on the same edge can set its own event. Epoch clear takes reset priority.

## Arithmetic and synthesis structure

- Dither rail comparison uses signed integer operands throughout.
- `sadc_enc` derives its default comparator count from `P_B1`, uses a padded
  balanced popcount tree, and checks representability of the output width.
- Reconstruction uses explicit padded reduction trees. Signed rail sums retain
  their previous precision; all floor/rounding/overflow decisions are unchanged.
- The integrated reconstruction reads only the eight active weights selected
  by fixed A/B bank muxes. Physical spare weights 16/17 remain programmable.
  Other allocator strategies must revise this selection explicitly.
- The iterative divider shifts its magnitude register and consumes fixed bit
  positions, avoiding counter-dependent bit selectors. Unroll and latency stay
  unchanged. Small-width exhaustive tests include non-divisible padding and
  unroll greater than numerator width.
- Maximum addressable weight geometries (32 slices or 128 units) use integer
  range comparisons instead of truncating the dimension into the index width.
- Reconstruction rejects unconfigured starts, cancels work when configuration
  is removed, and reports invalid slice indices while preserving prior output.

Simulation parameter guards delimit supported geometry; they are not a
substitute for DC elaboration checks, especially if synthesis ignores `initial`.
Production lint separately checks the two RTL hierarchies and fails on width,
latch, multiple-driver, combinational-loop, incomplete-case, missing-port and
out-of-range-select diagnostics. Testbench width diagnostics are kept separate.

## Verification and limits

The complete suite is `python tools/run_open_rtl.py`: two production lint tops
and eleven executable SystemVerilog benches. See
[the repair report](../rtl/COMPLETE_RTL_REPAIR_20260920.md) for results and bounds.
No new technology-mapped PPA or physical signoff follows from these changes.
