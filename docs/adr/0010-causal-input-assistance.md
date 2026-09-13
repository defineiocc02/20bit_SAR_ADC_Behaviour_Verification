# ADR 0010: Causal pretracking and auxiliary input in the physical loop

Status: accepted as reduced behavioral mechanisms. These controls live in
`Config.input_network` and act when `dyn_input_settling=True`. Overridden controls
with that switch off are explicitly reported inactive. The existing standalone
`aux_input` and `interleave_tracking` modules remain comparison/scaling models;
their ideal-input estimators are not used by the production loop.

## Pretracking

`pretracking.QuantizedPretracker` accepts integer SADC decisions, known dither,
nominal code weights, physical slice IDs and decision-availability timestamps.
It has no interface for ideal input or physical capacitor/threshold truth.
The nominal estimate is the coarse-bin center, corrected for known injection
and nominal sampling attenuation. It therefore retains real quantization and
SADC errors. It is not an already available 20-bit estimate.

The decision becomes available at `sample_time + quantizer_time_s`. A pending
queue prevents access before that time, even in an offline simulation that has
already constructed the input record. Pretracking starts before the main
acquisition by `pretrack_time_s`; its start time sets the decision deadline.
Startup without available history uses a zero command with source ID -1.

The supported policies are:

| Policy | Command source |
|---|---|
| `residue` | Keep the existing electrical state; no precharge operation |
| `reset_mid` | Precharge toward zero |
| `latest` | Most recent available SADC decision |
| `own` | Last available decision involving each physical slice |
| `weighted` | Up to three latest available decisions with configurable weights |

The default newest-to-oldest weights are 0.6/0.3/0.1, normalized over available
history. The patent gives such a weighted example; using it here does not imply
it is optimal. The SADC's local precharge command is the nominal arithmetic
mean of the selected slice predictions, not a physical-capacitance-weighted
digital estimate.

Each slice has an assumed independent finite-resistance precharge driver.
The actual capacitor voltage advances toward its predicted voltage over the
pretrack interval. The following shared-source acquisition starts from that
voltage. The filter bus is isolated from these local precharge drivers and
continues its own idle recovery. Pretracking plus acquisition must fit within
one sample period. Precharge-driver charge is logged separately from the input
driver's acquisition charge.

A delayed prediction can help low-frequency signals and harm rapidly changing
ones. Reduced charge is not converted directly into a noise or power benefit;
the main RC network must show the resulting tracking error. Dielectric-absorption
predistortion, exact DAC implementation and precharge driver noise are not
implemented by this reduced model.

## Auxiliary input

`auxiliary_cap_f` is the aggregate active clocked parasitic capacitance. It is
distinct from the actual signal-capacitor load. With `auxiliary_bypass=False`,
this branch draws acquisition charge from the shared filtered input bus and
therefore changes the actual bus and SADC/RDAC acquisition states.

With bypass enabled, the same parasitic branch draws charge from a separate
source through `auxiliary_resistance_ohm`, while the signal capacitors retain
their original main-input path. That branch is integrated continuously with
the same waveform/resolution contract. The model implements the mechanism of
moving a parasitic load away from the filtered signal pin, not an arbitrary
gain or SNDR improvement factor.

An assumed clock reset discharges the parasitic while disconnected from both
input sources, before tracking. Its charge is recorded against the reset source.
History is associated with the actual physical slice IDs. The equivalent
aggregate branch assumes equal parasitic loading and driver response among the
active slices; detailed backgate/bootstrapping waveforms require a circuit model.

## Observable and validation contract

`input_assist_trace` records predicted voltages, contributing sample IDs,
availability times, pretracking times and source charge. Auxiliary observations
separate branch charge, auxiliary-source charge and clock-reset charge. Main
input acquisition charge remains `input_source_charge_c`. Zero initial-charge
KCL tests verify that a bypassed parasitic is not charged to both sources.

`sadc_acquisition_error` exposes the decision-domain tracking perturbation.
Nominal sampling attenuation acts on that error as well as on the signal;
raw capacitor voltage and SADC decision voltage are different domains.

Tests exercise unavailable/future decisions, own versus weighted history,
finite precharge influence on real capacitor state, low-frequency usefulness,
auxiliary source/reset charge closure and actual bus improvement. A combined
nine-bit run includes sampling or quantizer dither, physical reference loads,
finite RA response, auxiliary bypass and pretracking, and checks nominal digital
provenance and backend/range headroom.
