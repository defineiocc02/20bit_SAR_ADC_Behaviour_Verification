"""Sample-domain chopping-assisted AZ research entry, disabled by default.

C32.1 Fig.6 discloses even-cycle AZ sampling, odd-cycle hold and pair difference.
The 40 MS/s -> 20 MS/s nonoverlapping readout is this project's explicit choice;
the paper's 6 MS/s headline does not resolve its raw/effective rate convention.
AZ voltage amplitudes, hold retention and transfer mismatch here are ASSUMED.
Source: Wang et al., VLSI 2025 C32.1, DOI
10.23919/VLSITechnologyandCir65189.2025.11075015, Figs. 5-6. The pair readout
uses the disclosed even-minus-odd convention. A demodulate-then-average view is
an equivalent expression for these aligned pairs, not a separate source block.
No FGA/CP-FIA transistor, dynamic chopper or 1/f circuit transfer is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._arrays import readonly
from .config import Config, ConfigError
from .fixed_point import CodeStream
from .pipeline import run_pipeline
from .sim import SimResult


@dataclass(frozen=True)
class ChoppingAZParameters:
    """Research controls; all error amplitudes are RA-output-equivalent volts.

    mode: off preserves the old raw readout; baseline has no added AZ sample
    noise but uses matched pair averaging; conventional_az resamples each raw
    cycle; chopping_assisted chops the complete ADC input and holds even AZ
    noise for odd conversion. post_az_offset_v is an explicit residual error,
    not a claimed model of an offset- or flicker-cancellation circuit.

    Args:
        mode: Explicit research mode; default off preserves ordinary raw output.
        az_noise_rms_v: Added AZ sample RMS at the RA output [V]. Independent
            ASSUMED source, not inferred from the paper's 4.3 dB measurement.
        post_az_offset_v: Constant post-AZ RA-output residual [V].
        odd_hold_retention: Held even-sample amplitude retained in odd [0, 1];
            available only for chopping_assisted.
        odd_transfer_ratio: Odd/even RA-output error transfer ratio [unitless];
            available only for chopping_assisted.

    Side effects:
        None. Validation is explicit and occurs before a simulation.
    """

    mode: str = "off"
    az_noise_rms_v: float = 0.0
    post_az_offset_v: float = 0.0
    odd_hold_retention: float = 1.0
    odd_transfer_ratio: float = 1.0

    def validate(self) -> None:
        """Reject undefined modes and nonfinite controls before generating data.

        Returns:
            None; raises ConfigError on an undefined or inactive nondefault input.

        Side effects:
            None; no random stream is consumed.
        """
        if self.mode not in ("off", "baseline", "conventional_az", "chopping_assisted"):
            raise ConfigError("unknown sampled-AZ research mode")
        values = [
            self.az_noise_rms_v,
            self.post_az_offset_v,
            self.odd_hold_retention,
            self.odd_transfer_ratio,
        ]
        if not np.all(np.isfinite(values)) or self.az_noise_rms_v < 0:
            raise ConfigError("AZ amplitudes must be finite and noise RMS nonnegative")
        if not 0 <= self.odd_hold_retention <= 1:
            raise ConfigError("AZ hold retention must lie in [0, 1]")
        if self.mode in ("off", "baseline") and self.az_noise_rms_v != 0:
            raise ConfigError("off/baseline modes require zero added AZ sample noise")
        if self.mode != "chopping_assisted" and self.odd_hold_retention != 1:
            raise ConfigError("hold retention only applies to chopping-assisted AZ")
        if self.mode != "chopping_assisted" and self.odd_transfer_ratio != 1:
            raise ConfigError("odd transfer ratio only applies to chopping-assisted AZ")
        if self.mode == "off" and self.post_az_offset_v != 0:
            raise ConfigError("off mode cannot silently ignore requested AZ perturbations")


@dataclass(frozen=True)
class ChoppingAZResult:
    """Separate the N-raw capture from its N/2 pair readout and error flags.

    Attributes:
        raw: Unfiltered raw physical record, including RA/ADC2 overflow flags.
        raw_words: Frozen bit-weight reconstruction before pair arithmetic.
        output_code: Offset-binary integer output; raw length in off mode.
        output_v: Output code bin centers [input-referred V].
        prequantized_output_v: Exact average/difference of raw bin centers [V].
        output_time_s: Raw aperture time or pair aperture midpoint [s].
        source_sample_ids: One or two raw IDs for every retained output.
        invalid: OR of raw analog overflow and digital clipping within an output.
        raw_sample_rate_hz: Raw conversion frequency [Hz].
        output_sample_rate_hz: Raw rate when off; half raw rate for paired modes.
        chop_sign: Actual aperture sign (+1/-1) for every raw conversion.
        az_refresh: True where the explicit sampled-AZ source is refreshed.
        az_sample_error_v: Held/refreshed AZ error after odd transfer [RA-output V].
        injected_ra_error_v: Total explicit error entering RA clipping [V].
        mode: Effective explicit research mode.

    Side effects:
        Array diagnostics are read-only. The owned raw record retains its
        existing SimResult semantics; no raw samples are discarded.
    """

    raw: SimResult
    raw_words: CodeStream
    output_code: np.ndarray
    output_v: np.ndarray
    prequantized_output_v: np.ndarray
    output_time_s: np.ndarray
    source_sample_ids: np.ndarray
    invalid: np.ndarray
    raw_sample_rate_hz: float
    output_sample_rate_hz: float
    chop_sign: np.ndarray
    az_refresh: np.ndarray
    az_sample_error_v: np.ndarray
    injected_ra_error_v: np.ndarray
    mode: str


def pair_readout(
    words: CodeStream, *, difference: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Combine exact offset-binary bin centers using integer pair arithmetic.

    Args:
        words: Even-length CodeStream, integer words in [0, 2**B), B in [1, 30],
            finite positive v_fs [V], and matching Boolean clipping flags.
        difference: True for even-minus-odd; False for ordinary pair averaging.

    Returns:
        Read-only (offset-binary code, decoded bin-center V, exact prequantized
        pair V, invalid). Averaging uses floor((ke+ko+1)/2); difference uses
        floor((ke-ko+2**B)/2). Invalid is the OR of all raw clipping flags.
        Unsigned public words are widened before subtraction to prevent wrap.

    Side effects:
        None. Does not infer IDs, epochs, or AZ ownership; the fresh-record
        runner validates sample ownership before invoking this arithmetic.

    Source:
        C32.1 Fig. 6 pair difference; midrise bin-center requantization is this
        project's integer readout choice. No invalid sample is filtered out.
    """
    code = np.asarray(words.code)
    if code.ndim != 1 or len(code) < 2 or len(code) % 2:
        raise ValueError("paired readout requires a positive even raw record")
    if type(words.output_bits) is not int or not 1 <= words.output_bits <= 30:
        raise ValueError("paired readout output_bits must be an integer in [1, 30]")
    count = 2**words.output_bits
    if not np.issubdtype(code.dtype, np.integer) or np.any(code < 0) or np.any(code >= count):
        raise ValueError("paired readout words must be in-range offset-binary integers")
    if not np.isfinite(words.v_fs) or words.v_fs <= 0:
        raise ValueError("paired readout v_fs must be finite and positive [V]")
    flags = [np.asarray(x) for x in (words.analog_overflow, words.clipped_low, words.clipped_high)]
    if any(x.shape != code.shape or x.dtype != np.dtype(bool) for x in flags):
        raise ValueError("paired readout requires matching Boolean raw flags")
    code = code.astype(np.int64)
    even, odd = code[::2], code[1::2]
    numerator = even - odd + count if difference else even + odd + 1
    output = numerator // 2
    raw_voltage = words.v_fs * (2 * (code + 0.5) / count - 1)
    exact = (
        (raw_voltage[::2] - raw_voltage[1::2]) / 2
        if difference
        else (raw_voltage[::2] + raw_voltage[1::2]) / 2
    )
    voltage = words.v_fs * (2 * (output + 0.5) / count - 1)
    invalid_raw = flags[0] | flags[1] | flags[2]
    invalid = invalid_raw[::2] | invalid_raw[1::2]
    return readonly(output, np.int64), readonly(voltage), readonly(exact), readonly(invalid, bool)


def _fresh_pair_ids(raw: SimResult, n_samples: int) -> np.ndarray:
    """Validate one even-first 32-bit-ID epoch; do not silently regroup gaps."""
    ids = np.asarray(raw.sample_id)
    if (
        ids.shape != (n_samples,)
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any(ids < 0)
        or np.any(ids >= 2**32)
    ):
        raise ConfigError("fresh AZ capture requires explicit unsigned 32-bit raw sample IDs")
    ids = ids.astype(np.int64)
    if ids[0] % 2 or np.any(((ids[1:] - ids[:-1]) & 0xFFFF_FFFF) != 1):
        raise ConfigError(
            "fresh AZ capture requires continuous IDs and an even positive first phase"
        )
    return ids


class _SampleIndexedChop:
    """Aperture-only waveform: t1 and t2 of each raw sample share its sign.

    This deliberately uses sample ownership, not floor/ceil of arbitrary time.
    It is not a continuous chopper-switch waveform and cannot enter dynamics.
    """

    def __init__(self, source, signs):
        self.source, self.signs = source, signs

    def __call__(self, time_s):
        values = np.asarray(time_s)
        if values.shape != self.signs.shape:
            raise ConfigError("sample-domain chopper requires an owned raw-sample time array")
        return self.signs * self.source(values)


def _require_sample_domain(cfg: Config) -> None:
    unsupported = []
    for name in (
        "ra_autozero",
        "dyn_input_settling",
        "dyn_ref_settling",
        "dyn_crosstalk",
        "slice_timing_skew_s",
        "slice_offset_sigma_v",
        "slice_bw_spread",
        "sampling_tau_mismatch",
        "driver_noise_rms",
        "flicker_corner_hz",
        "ktc_enable",
    ):
        if getattr(cfg, name):
            unsupported.append(name)
    if cfg.conversion.dynamic:
        unsupported.append("conversion.dynamic")
    if cfg.adc2_dyn_bw_ratio is not None:
        unsupported.append("adc2_dyn_bw_ratio")
    if unsupported:
        raise ConfigError(
            "sample-domain AZ does not implement or combine: " + ", ".join(unsupported)
        )


def run_caaz_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    az: ChoppingAZParameters | None = None,
    post_az_error_v=None,
    az_rng: np.random.Generator | None = None,
    **pipeline_kwargs,
) -> ChoppingAZResult:
    """Run the full raw ADC, inject pre-clip error, then pair reconstructed codes.

    Args:
        cfg: Existing ADC configuration; fs counts raw conversions [Hz].
        input_fn: Voltage callable evaluated at owned raw aperture times [s].
        n_samples: Positive raw conversion count; paired modes require even N.
        az: Explicit mode and ASSUMED RA-output-equivalent error controls;
            None selects default off parameters.
        post_az_error_v: Optional finite raw-length RA-output error stream [V].
            A defined residual perturbation, not a device-level 1/f model.
        az_rng: Separate sampled-AZ random generator. Off/baseline do not
            consume it; conventional_az and chopping_assisted draw N innovations.
        **pipeline_kwargs: Existing run_pipeline chip/state/rng/scheduler/pool
            inputs. State may carry independently trained frozen bit weights.

    Returns:
        ChoppingAZResult preserving every raw word/flag and a separate pair
        output. Fresh paired records validate continuous even-first IDs; CAAZ
        applies + to even and - to odd at both SADC/RDAC apertures. Nonoff modes
        emit N/2 codes, pair midpoints, and fs/2 output rate. The two-tap signal
        response has gain cos(pi*fin/fs) and aliasing above output Nyquist.

    Side effects:
        Consumes supplied RNGs only for their active physical sources. Existing
        pool electrical state is reset by run_pipeline. No Config field, ADC2
        window, noise budget or ordinary default is modified. Off delegates
        unchanged; paired modes reject unimplemented dynamic-chopper, previous
        phenomenological AZ/flicker, and incompatible phase-noise combinations.
        Independent aperture sampling noise remains supported; this is not a
        continuous chopper waveform. Streaming across separate calls is absent.

    Source:
        C32.1 Figs. 5-6 disclose full-input chop and even-AZ/odd-hold paired
        difference. Nonoverlap raw40/pair20 is an ENGINEERING_CHOICE; amplitudes,
        retention and transfer ratio are ASSUMED, not a 4.3 dB fitted scalar.
    """
    az = ChoppingAZParameters() if az is None else az
    az.validate()
    if type(n_samples) is not int or n_samples < 1:
        raise ConfigError("raw sample count must be a positive integer")
    if "ra_output_error_v" in pipeline_kwargs:
        raise ConfigError("use the named AZ perturbation inputs, not two RA-error streams")
    external = (
        np.zeros(n_samples) if post_az_error_v is None else np.asarray(post_az_error_v, float)
    )
    if external.shape != (n_samples,) or np.any(~np.isfinite(external)):
        raise ConfigError("post-AZ error must be a finite RA-output-V stream of raw length")
    signs = np.ones(n_samples, dtype=np.int64)
    refresh = np.zeros(n_samples, dtype=bool)
    sampled = np.zeros(n_samples)
    injected = np.zeros(n_samples)
    if az.mode == "off":
        if post_az_error_v is not None:
            raise ConfigError("off mode cannot silently ignore a post-AZ error stream")
        raw = run_pipeline(cfg, input_fn, n_samples, **pipeline_kwargs)
    else:
        _require_sample_domain(cfg)
        if n_samples % 2:
            raise ConfigError("sampled-AZ research modes require an even raw sample count")
        if az.mode in ("conventional_az", "chopping_assisted"):
            generator = np.random.default_rng(cfg.seed + 120001) if az_rng is None else az_rng
            # Same independent innovations for matched conventional/CAAZ tests.
            # CAAZ only samples the even innovations; odd ones are unobserved.
            innovation = generator.normal(0, az.az_noise_rms_v, n_samples)
            if az.mode == "conventional_az":
                refresh[:] = True
                sampled[:] = innovation
            else:
                refresh[::2] = True
                sampled[::2] = innovation[::2]
                sampled[1::2] = az.odd_hold_retention * innovation[::2]
                signs[1::2] = -1
            sampled[1::2] *= az.odd_transfer_ratio
        injected = sampled + external + az.post_az_offset_v
        waveform = (
            _SampleIndexedChop(input_fn, signs) if az.mode == "chopping_assisted" else input_fn
        )
        raw = run_pipeline(cfg, waveform, n_samples, ra_output_error_v=injected, **pipeline_kwargs)
    words = raw.to_codes()
    invalid_raw = words.analog_overflow | words.clipped_low | words.clipped_high
    ids = np.arange(n_samples) if raw.sample_id is None else raw.sample_id
    if az.mode != "off":
        ids = _fresh_pair_ids(raw, n_samples)
    if az.mode == "off":
        output, voltage, exact, invalid = words.code, words.voltage, words.voltage, invalid_raw
        times, source_ids, output_fs = raw.sample.t1, ids[:, None], cfg.fs
    else:
        output, voltage, exact, invalid = pair_readout(
            words, difference=az.mode == "chopping_assisted"
        )
        times = (raw.sample.t1[::2] + raw.sample.t1[1::2]) / 2
        source_ids, output_fs = ids.reshape(-1, 2), cfg.fs / 2
    return ChoppingAZResult(
        raw,
        words,
        readonly(output, np.int64),
        readonly(voltage),
        readonly(exact),
        readonly(times),
        readonly(source_ids, np.int64),
        readonly(invalid, bool),
        cfg.fs,
        output_fs,
        readonly(signs, np.int64),
        readonly(refresh, bool),
        readonly(sampled),
        readonly(injected),
        az.mode,
    )
