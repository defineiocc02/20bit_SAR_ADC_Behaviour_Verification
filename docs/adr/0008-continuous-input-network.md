# ADR 0008: Continuous tracking through a shared input network

Status: accepted for the split behavioral runner. This is an assumed reduced
passive circuit, not a reconstruction of unpublished transistor circuitry.

## Problem

An endpoint step followed by `exp(-Tacq/tau)` loses continuous high-frequency
tracking lag. Treating the complete source resistance as eight independent
branches also gives an incorrect settling improvement. The sampled SADC and
RDAC loads must see the same driver current during acquisition.

## Decision and equations

Include every selected physical slice load and the SADC capacitance behind
individual switch resistances. A common source resistance drives their bus.
With no filter capacitor, eliminate the algebraic bus voltage by KCL:

```
vb = (Vin/Rs + sum(vi/Ri)) / (1/Rs + sum(1/Ri))
Ci * dvi/dt = (vb - vi)/Ri
```

With a filter capacitor, add its voltage as a persistent state:

```
Cf * dvb/dt = (Vin - vb)/Rs - sum((vb - vi)/Ri)
```

For equal slice capacitances and switch resistances in common mode, the time
constant is `(N*Rs + Ron)*Cslice`. Splitting the *source* term among the slices
does not follow from these equations. The SADC is a separate branch, not a
second independent copy of the common source impedance.

The conductance matrix is symmetric in capacitance-normalized coordinates.
`input_network.PassiveTrackingNetwork` diagonalizes it and integrates DC and
real-phasor sinusoidal forcing exactly over each linear interval. Arbitrary
callables use a first-order hold; nonlinear Ron uses midpoint piecewise values:
`Ron(t)=Ron0*(1+rho*(Vin(t)/Vfs)**2)`. The source resistance is not modulated by
the slice bandwidth spread. Nonlinear and arbitrary-input studies must show
convergence versus `InputNetworkParameters.integration_steps`; the default is
an integration resolution, not a promise of 20-bit accuracy for every stimulus.

`filter_cap_f` preserves shared-bus state through acquisition and idle intervals.
Every independent record starts from zero electrical state and explicitly primes
its first acquisition. Noise remains an aperture-level stochastic model;
sampled driver noise is not a continuous noise spectrum filtered by this RC
solver. Analog dither is held through its acquisition interval; sampling and
quantizer dither remain their distinct ideal charge-injection ports.

## Observable contract and limitations

Both production split entries expose acquisition capacitor voltages, bus
voltage, acquisition-only source charge, sample IDs and acquisition timestamps.
The source charge is the net capacitor charge change in this passive network;
it excludes idle charge and subsequent reference switching. It must not be
used as a complete chip input-current spectrum without those events.

Slice skew/offset remain explicitly first-order aperture perturbations. A skew
counterfactual must isolate that perturbation or subtract a matched no-skew
record: total error includes the much larger common RC lag. Subnode dynamics
are reduced to the physical equivalent sampling capacitance; parasitic internal
poles require an expanded circuit or extracted model. Auxiliary input and
quantized pretracking integration are separate forthcoming extensions.

Configuration exports are JSON compatible. `Config.from_dict` reconstructs
typed parameter groups, rejects unknown fields and validates physical values.
The unary/aggregate comparison does not silently acquire this split solver;
an overridden split-only group is reported in effective run metadata.

## Independent evidence

`tests/unit/test_input_network.py` uses RK4 on directly written branch currents
and a separately integrated source current. It checks mixed capacitors, a
filter state, DC common-mode decay, ramps, a 19 MHz sine, charge conservation,
second-order convergence of nonlinear Ron, and an independent AC transfer
function through the complete pipeline. The tests do not use the production
modal matrix as their oracle.
