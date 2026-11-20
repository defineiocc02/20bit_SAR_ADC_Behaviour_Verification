# ADR 0014: integer split-ADC output contract

Status: implemented; arithmetic assumptions are engineering choices, not disclosed
silicon RTL. See `fixed_point.py` and `tests/unit/test_fixed_point.py`.

## Interface and units

`result.to_codes()` consumes raw ADC2 words, actual slice IDs, RDAC/DEM commands,
and known digital dither. Coefficients come from nominal geometry/gain or a
frozen independent calibration. It never reads `result.out`, input truth or
fabricated capacitances. `result.out` remains a floating diagnostic; final-code
performance must be measured on `result.to_codes().voltage` and accompanied by
all three clipping flags. Unary and unquantized KTC research paths are explicitly
outside this integer split interface.

The default register image uses signed effective C/Cf Q30 weights in 48 bits
and normalized V/Vfs Q32 voltages. Weight/voltage coefficient quantization uses
round-to-nearest, ties-to-even. Backend bin centers are computed from the raw
integer code using signed integer division with that same tie rule. Switch
signs are exactly integer; fractional mask interpolation is rejected. Known
injection commands are quantized at the defined voltage interface.

For integer weights W, signal connections a in {0,1}, rail signs s, injection I,
backend bin center F and fitted offset O, the core computes

    numerator = (F - O) * 2^30 - sum(W*s) * 2^32 - sum(W)*I
    denominator_gain = sum(W*a)
    word = floor((numerator + denominator_gain*2^32) * 2^20
                 / (2*denominator_gain*2^32))

Mask sums use bounded signed int64. Products, numerator, final shifted numerator
and divisor use checked signed 96-bit accumulators (emulated with Python wide
integers). Overflow raises an error; arithmetic does not wrap. Output words
saturate to [0, 2^20-1], with independent low/high flags and preserved analog
ADC2/RA/RDAC overflow flags. The nominal output interval is [-Vfs,+Vfs), decoded
at mid-rise bin centers. Exact upper input range is saturated. At Vfs=3 V one
final LSB is 5.7220458984375 uV. Twenty-bit words do not establish twenty ENOB.

`FixedPointReconstructor.to_dict/from_dict` exports a versioned, standard JSON
register image including the nominal interface, widths and voltage units.
Reconstruction reports its maximum required signed accumulator width.

## Independent checks and limits

* Signed positive/negative half-way rounding and checked accumulator overflow.
* Exhaustive 1,048,576-word ideal backend oracle: every final code, endpoints,
  monotonic increments and exactly one-LSB decoded width.
* Dense 1/16-LSB local ramps across all 511 nine-bit coarse carries in the actual
  production runner: no backward or skipped code; bounded local dwell width.
* Off/sampling/quantizer modes: code-center output within 0.501 LSB of the
  independently calculated nominal float result, even with floating output and
  truth buffers poisoned. Analog noise remains present.
* Frozen noisy calibration: independent holdout retains <60 uV RMS and its
  improvement over the uncalibrated path; additional quantization <0.51 LSB.
* Immutable register image and JSON round trip.

The exhaustive digital oracle does not establish full-chip INL/DNL. The carry
scan is noiseless, nominal, and locally dense. DEM makes output distributions
conditional on switching state; calibrated noisy full-chip code-density claims
still require their own stimulus coverage and uncertainty. A conventional
250-hits/code twenty-bit histogram needs about 262 million samples. This model
uses bounded-memory digital enumeration and local transition probes rather
than labeling a short conditional mean-error curve as that histogram.
