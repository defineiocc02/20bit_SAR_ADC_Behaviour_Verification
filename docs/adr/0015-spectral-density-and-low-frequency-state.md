# ADR 0015: spectral power, harmonic rank and low-frequency state

Status: implemented; final acceptance sweep pending. The standard PSD
normalization is cross-checked against the official SciPy `periodogram` API:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.periodogram.html
and its spectral-analysis user guide. The noise-process approximation below is
an explicit behavioral assumption, not a measurement of the device.

## Density and identifiability

`power_spectral_density` returns V²/Hz, frequency spacing and window ENBW.
Its denominator is fs*sum(w²). Interior positive-frequency bins are doubled;
DC and even-record Nyquist bins are not. The discrete band integral is sum(P*df),
with the actual included bin centers returned. This equals window-weighted
mean-square power over the full grid (Parseval). Tone `spectrum_dbfs` instead
uses coherent amplitude gain; it cannot serve as noise density. Its Nyquist
amplitude has a separate factor. Unknown window names and malformed records
are errors. Noise-only integration requires a residual/noise input explicitly.

The time-domain harmonic fit folds frequencies into [0,fs/2], removes duplicate
aliases and DC, and uses one cosine column at Nyquist. Returned fit rank,
condition, degrees of freedom and reliability expose finite-record limitations.
FFT THD also counts Nyquist as one real RMS component; SFDR compares RMS powers.
Signals with harmonics aliased onto the fundamental remain inseparable and are
reported as dropped. Low-frequency unresolved FFT harmonic estimates retain
an explicit unreliable marker. Raw library NaN/inf denote undefined quantities;
published JSON must translate these to explicit status/null, never nonstandard
numeric tokens or silent successful gates.

## Stationary slow state and observation duration

A stationary 1/f process needs low/high cutoffs. `BandLimitedFlicker` represents
S(f)=S0*fc/f on [fl,fh] using stratified logarithmic frequency quadrature. Each
mode has independent Gaussian sine/cosine coefficients with variance
S0*fc*log(fh/fl)/M. The ensemble process is stationary; total variance equals
the band integral. Finite M approximates a continuous spectrum and its covariance.
This approximation requires mode/ensemble convergence when using fine PSD bins.

`at_adc_indices(indices, 40e6)` queries the same frozen state at actual physical
ADC timestamps. A slow observation probe may retain every 156250th sample to
observe 64 seconds at 256 Hz; the ADC clock stays 40 MHz, and only this <=40 Hz
component is sparsely observed. Such a probe does not validate the entire
40 MS/s signal chain. White noise in that observation channel must be integrated
over its own explicit antialias bandwidth, never copied as unfiltered 40 MHz
white samples and mislabeled as a low-rate density.

`flicker_series` keeps FFT generation for resolved bins and adds stationary
slow modes on [1/t_obs,min(fc,fs/N)] for unresolved power. This fixes the former
unreachable branch and avoids allocating half the unresolved variance to a
record-length-dependent linear ramp. `t_obs` defines the assumed low cutoff;
it does not change the duration N/fs. Separate wrapper calls draw independent
realizations; cross-record continuity requires the explicit frozen state.

Tests verify short/long observations on one clock, chunk agreement, ensemble
variance and covariance against the independent cosine-integral formula, plus
physically slow sub-record drift. The original strict flicker xfail is removed
only after its regression and these independent checks pass.

## Evidence source and observer limits

`benchmarks.PAPER_BENCHMARK` retains [00] 94.2 dB / 9.3 nV/sqrt(Hz);
`SLIDES_BENCHMARK` retains [00_1] pp.36-37 / 42, 94.6 dB / 8.8 nV/sqrt(Hz)
and ~40 Hz corner. Both publish rounded values; noise inferred from DR and
from a flat NSD band integral is reported separately. The candidate configuration
uses the selected DR as a fitted RA-noise anchor. Recovering that number verifies
budget consistency, not predictive device noise, power or silicon replication.

The KTC observer extension consumes noise/slope state under explicit behavioral
assumptions. Its unquantized voltage correction, independent observation noise,
ideal AZ reset and scalar noise-folding factors do not implement an observer ADC,
a switched noise transfer function, or closed-loop transistor AZ. Its limits
remain visible and `to_codes()` rejects that extension. The finite signal-pole
RA/ADC2 solver validates signal settling only; it does not establish the full
cyclostationary noise transfer, correlations or device-level power.
