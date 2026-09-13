# ADR 0009: One time axis for reference recovery, RA and ADC2

Status: accepted for the production split runner. All undisclosed component
values, trial-loading details and first-order amplifier poles remain assumptions.

## Decision

The reference error is a time-dependent RA input. Replacing it with its value
at the ADC2 aperture can underestimate error even when the reference itself
has settled. `ConversionEngine` advances the shared reference, RA and ADC2
state for exactly one sample at a time inside the physical acquisition loop.
Its final reference-induced DAC perturbation updates the released capacitor
state before those slices may acquire again. Reordering sample calls raises
an error; no future conversion result participates in the current acquisition.

The engine is active when `dyn_ref_settling` or a finite signal-response control
in `Config.conversion` is enabled. The old aggregate reference proxy is disabled
on that path. `dyn_ref_dynamic_ratio` and `rdac_bitwise_bits` are explicitly
reported inactive: actual physical charge and the actual SADC decision count
replace those proxy controls. The independent aggregate/unary reference retains
its historical model for comparison.

## Reference-rail charge

Sampling clamps the main and sub top plates to common mode (zero). During
conversion the main top plate is an ideal RA summing node and the subnode
floats. Its sampled charge is conserved independently in each selected slice:

```
Vsub = (sum(Csub_j * Vbottom_j) - Qsample_sub) / (Cbridge + Csub + Cpar)
qbottom_main_j = Cmain_j * Vbottom_j
qbottom_sub_j  = Csub_j * (Vbottom_j - Vsub)
```

For each actual switch event, a rail supplies the change in bottom-plate charge
of the capacitors connected to it *after* switching. Retained connections also
supply redistribution charge when another switch moves the floating subnode.
The two reference rails have separate signed charge records. Sampled voltage,
dither sampling connections, actual capacitor mismatch and physical DEM masks
all enter this calculation. Duplicate commands produce zero switching charge.

With bitwise loading enabled, the candidate program loads each binary-search
trial and its retain/reject decision, then reaches the actual SADC result plus
transferred dither. Equal decision subintervals and this exact trial-loading
schedule are assumptions. They are not claimed as the unpublished clock
sequence. With bitwise loading disabled, the final load occurs at RA entry.

Each rail uses a lumped reference reservoir `dyn_c_decouple`, followed by coarse
or fine exponential recovery. `dyn_tau_ref` controls coarse recovery and
`conversion.fine_reference_tau_s` controls fine recovery; None uses the same
time constant for both. This models buffer selection on a shared reservoir,
not a detailed multi-pole package/PCB/buffer network.

The load program is linearized at nominal rail voltages. The rail errors enter
the physical DAC through its actual rail sensitivities. For large droop, the
switched capacitances must be included in the reference charge-sharing solve;
this reduced model is not a large-signal proof. `reference_peak_fraction` exposes
the expansion parameter. Precision studies should check convergence as reservoir
capacitance increases, and assess neglected charge redistribution against their
error budget. A small fraction alone is not a universal 20-bit accuracy bound.

## Signal response and timing

Between events, the RA target has the form `u + r*exp(-t/tau_ref)`. The model
integrates that forcing through the finite closed-loop RA pole and the ADC2
tracking pole on the same interval. Equal or nearly equal poles are supported.
Finite OTA DC gain uses the declared reduced-loop factor
`A0/(A0+1+G)`; finite slew limits the output derivative, and swing limiting acts
before ADC2. The bandwidth field is **closed-loop signal bandwidth**, not GBW.

`quantizer_time_s` sets the assumed delay to RA entry. RA duration is
`dyn_t_conv_frac/fs`; their sum must fit within one sample period. ADC2 tracks
with wide bandwidth, switches at `narrow_start_fraction`, and samples at the
end. An unset bandwidth is an ideal follower. The RA retains its previous
output during the quantizer phase unless ideal auto-zero is enabled; ADC2
holds its previous capacitor voltage until its next tracking phase.

Auto-zero here has an explicit ideal reset phase. Residual offset estimation,
switch noise and correlated noise folding are not inferred from that reset.
Existing RA/ADC2 noise modifiers remain output-equivalent aperture budgets.
Signal-pole integration does **not** turn them into a validated transient-noise
model or a physical prediction of the disclosed noise improvement.

Linear propagation uses the matrix exponential. Slew/clipping uses an adaptive
ODE solve with explicit voltage tolerances and a failed-integration exception.
SciPy is a declared runtime dependency; its documented
[matrix exponential](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.expm.html)
and [ODE solver](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html)
provide these numerical operations.

## Observations and verification

`result.vra` and `result.adc2_input_voltage` are separate nodes.
`result.conversion_trace` records signed load charge, event times, rail errors
after each event, peak rail error, RA/ADC2/rail states at phase boundaries, and
the absolute ADC2 aperture. Boundary zero is immediately before RA/ADC2 track
activation. Trace voltages exclude the separately applied aperture noise.
`result.residue` includes the final physical reference perturbation; finite RA
history means `vra == G*residue` is no longer a valid general invariant.

Independent checks cover direct capacitor/node charge equations, zero charge
for duplicate switches, signed input-dependent loads, sampling masks, and
physical DEM sensitivities. Joint checks include a closed-form convolution,
the repeated-pole limit, a separately written time-domain ODE, exact slew ramp,
RA clipping before ADC2, the ideal runner limit, actual ADC2 bandwidth influence,
reference-state determinism and reference backaction on the next acquisition.

This implementation closes those behavioral signal-path contracts. It does not
establish transistor-level power, package parasitics, reference thermal noise,
closed-loop stability margins or the exact fabricated circuit of the paper.
