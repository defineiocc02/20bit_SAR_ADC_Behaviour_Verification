# ADR 0016: Complete RTL configuration epochs and sampling dither

Status: accepted for the review-fix branch, 2026-09-19.
Supersedes incomplete configuration and uniform-integer dither assumptions in
P1/P2 interface notes. These are implementation decisions, not paper disclosures.

## Configuration contract

The external top-level ports and address map remain the same. Internal modules
add `weight_store.clear_load/load_complete` and
`calib_regs.weights_ready/config_busy`.

1. Reset or `cfg_clear_valid` starts an uncommitted load epoch. Every one of the
   18 × 71 weights and all three scalar coefficients must be explicitly written
   in that epoch, including a zero offset. A bitmap records accepted addresses;
   duplicate or rejected writes cannot replace missing addresses.
2. Weight writes require correct alignment, valid indices, zero unused bus bits,
   `0 < W < 2^47` and the existing sum bound. Scalar values retain their signed
   64-bit representation. `min < max` is required at commit.
3. `cfg_validate` is an exclusive commit request. Any simultaneous external
   write, internal control serialization, or busy reconstruction rejects it with
   error 5 (`ERR_CFG_WRITE`). A rejected commit preserves the current ready
   state and accepts no colliding external write. The control sequencer pauses
   on validate so that no serialized bit is lost.
4. An idle commit with missing fields reports error 6 (`ERR_INCOMPLETE`). Error
   3 still has priority for an empty/reversed ADC2 range. Successful commit
   atomically sets ready. While ready, all configuration writes are rejected.
5. Writing 0x1018 now captures four control bits and applies them atomically
   on the third subsequent rising edge (ADR 0017). Wait for all three edges
   before validate or another write.
   There is no external ready/ack for individual writes: software must observe
   this fixed timing. Rejected writes are not queued; software must retry them.
6. `cfg_clear_valid` has highest priority, deasserts ready, clears completeness,
   cancels pending control writes and synchronously resets reconstruction and
   its divider. In-flight samples are discarded. Stored coefficients and
   controls remain readable, but cannot be reused without a full coefficient
   reload. Controls may be retained or explicitly rewritten. Reset defaults
   remain unchanged. This protocol does not provide uninterrupted hot update.
7. The four overflow/gain flags remain sticky until clear/reset. The encoded
   error field is the latest priority-selected diagnostic, not an event history;
   successful validation clears configuration diagnostics. See ADR 0017 for
   input deadline errors, which persist until epoch clear/reset.

The bitmap costs 1278 state bits plus three scalar presence bits. Timing and
area must be remeasured on this version; historical synthesis is not signoff.

## Dither contract

For D>0, sampling dither follows the PMF of `round(uniform(-D,D))`: endpoints
have probability `1/(4D)` and interior integers `1/(2D)`. At D=2 this gives
`[1,2,2,2,1]/8`, zero mean and variance 1.5. D=0 outputs zero. D=0..63 and
nonzero 32-bit seeds are supported; invalid parameters fail elaboration/simulation.

The right-shift Galois recurrence shifts in zero, XORs mask `0x80200003` on
feedback and advances eight bits per enabled draw. The polynomial's maximal
nonzero period is independently checked with GF(2) exponentiation. Eight-step
decimation preserves that period and avoids overlapping raw-byte windows.
It does not establish cryptographic quality or physical ADC noise performance.
Rejection removes modulo bias; `valid=0` is not a new sample. Top-level D=2
has no rejections. Other top-level ranges require a separate timing/consumption
review because the current top holds its prior code on a rejected draw.

The RTL sequence differs from Python PCG64. Statistical agreement is scoped
to this sampling PMF; L3 quantizer-dither vectors remain injected separately.
Regression thresholds, warm-up and fixed seeds are in `review_dither_tb`.

## Evidence

`tools/run_open_rtl.py` compiles and runs actual SystemVerilog, including
configuration adversarial cases, the internal dither path and existing P1/P2/P3
testbenches. Python independently checks the feedback period and offset-boundary
counterexamples. See `docs/rtl/REVIEW_20260919.md` for limits and remaining work.
