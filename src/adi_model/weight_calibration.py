"""Identifiable split-unit charge calibration from noisy digital observations.

The static charge equation is fine = sum(w*(a*x_known + b - Vdac)) + offset,
where w is each physical unit's effective C/Cf (beta*C/Cf for sub units), a is
its nominal signal connection and b its known sampling/injection voltage.
Training sees a separately supplied reference input and digital masks/codes.
It never sees fabricated capacitance, residue error, actual gain or noise truth.

Weights are estimated once with a rank-revealing SVD and frozen. Validation
uses only independent digital observations and solves the same charge equation
for unknown input. The model covers static charge gain; finite settling, slew,
reference droop and the experimental KTC observer are not calibrated away.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields, replace

import numpy as np
from scipy.linalg import svd

from .dem import SplitSwitchGeometry, split_switch_command


def _readonly(value, dtype=float):
    a = np.ascontiguousarray(value, dtype=dtype)
    # Immutable backing storage also prevents setflags(write=True).
    return np.frombuffer(a.tobytes(), dtype=a.dtype).reshape(a.shape)


@dataclass(frozen=True)
class CalibrationSpec(SplitSwitchGeometry):
    """Nominal digital interface only; physical mismatch/noise fields are absent."""

    n_slices: int
    v_fs: float
    adc2_n_bits: int
    adc2_v_min: float
    adc2_v_max: float
    dither_mode: str
    dither_split_bank: str
    dither_units_range: float
    dither_discrete: bool

    @classmethod
    def from_config(cls, cfg) -> CalibrationSpec:
        """Whitelist nominal dimensions, switching controls and backend code units."""
        cfg.check_legal()
        if cfg.dac_arch != "split":
            raise ValueError("split weight calibration requires a split topology")
        return cls(**{f.name: getattr(cfg, f.name) for f in fields(cls)})

    @property
    def shape(self) -> tuple[int, int]:
        """Physical weight-array dimensions, without any weight values."""
        return self.n_slices, self.dac_n_main + self.dac_n_sub

    @property
    def adc2_step_v(self) -> float:
        """Nominal backend code step [V]."""
        return (self.adc2_v_max - self.adc2_v_min) / 2**self.adc2_n_bits


@dataclass(frozen=True)
class DigitalObservation:
    """Read-only digital data and nominal units; no analog reference input."""

    spec: CalibrationSpec
    slice_ids: np.ndarray
    rdac_code: np.ndarray
    dem_state: np.ndarray
    adc2_code: np.ndarray
    bank_dither: np.ndarray
    common_injection_v: np.ndarray
    overflow: np.ndarray
    static_charge_domain: bool

    @classmethod
    def from_result(cls, result) -> DigitalObservation:
        """Extract whitelisted digital records and acquisition-validity flags.

        Physical flags reject clipped training records; their magnitudes do not
        become estimator features. Observer-corrected fine voltage is not used.
        """
        cfg = result.cfg
        if result.adc2_code is None or result.conv_slice_ids is None:
            raise ValueError("calibration requires raw backend codes and actual digital slice IDs")
        if cfg.ktc_enable:
            raise ValueError(
                "the KTC observer is outside the static unit-charge calibration domain"
            )
        n = len(result.adc2_code)
        bank = result.sample.dither_bank_code
        if cfg.dither_mode == "quantizer":
            injection = np.broadcast_to(result.sample.rdac_dither, (n,))
        elif cfg.dither_mode == "analog":
            injection = result.sample.dither
        else:
            injection = np.zeros(n)
        static = cfg.ra_gain_model == "charge" and not (
            cfg.dyn_input_settling
            or cfg.dyn_ref_settling
            or cfg.dyn_crosstalk
            or cfg.conversion.dynamic
            or cfg.slice_timing_skew_s
            or cfg.slice_offset_sigma_v
        )
        return cls(
            CalibrationSpec.from_config(cfg),
            _readonly(result.conv_slice_ids, np.int64),
            _readonly(result.k),
            _readonly(result.sid, np.int64),
            _readonly(result.adc2_code, np.int64),
            _readonly(np.zeros(n) if bank is None else bank),
            _readonly(injection),
            _readonly(result.rdac_over | result.adc2_over | result.ra_sat, bool),
            bool(static),
        )

    def validate(self) -> None:
        """Reject malformed digital data before allocating a calibration matrix."""
        n = len(self.rdac_code)
        spec = self.spec
        if self.slice_ids.shape != (n, spec.n_active) or n == 0:
            raise ValueError("digital slice allocation has the wrong shape")
        if not np.issubdtype(self.slice_ids.dtype, np.integer) or not np.issubdtype(
            self.dem_state.dtype, np.integer
        ):
            raise ValueError("slice IDs and DEM states must be integer digital data")
        if np.any(self.slice_ids < 0) or np.any(self.slice_ids >= spec.n_slices):
            raise ValueError("digital slice IDs are outside the nominal pool")
        if np.any(np.diff(np.sort(self.slice_ids, axis=1), axis=1) == 0):
            raise ValueError("duplicate physical slice in one digital allocation")
        for a in (
            self.rdac_code,
            self.dem_state,
            self.adc2_code,
            self.bank_dither,
            self.common_injection_v,
            self.overflow,
        ):
            if a.shape != (n,) or np.any(~np.isfinite(a)):
                raise ValueError("digital observation vectors must be finite and equally sized")
        if (
            not np.issubdtype(self.adc2_code.dtype, np.integer)
            or np.any(self.adc2_code < 0)
            or np.any(self.adc2_code >= 2**spec.adc2_n_bits)
        ):
            raise ValueError("calibration needs legal raw integer ADC2 codes")
        if spec.dither_mode == "sampling" and not spec.dither_discrete:
            raise ValueError("unit calibration requires discrete physical sampling-dither masks")
        if spec.dither_mode == "sampling" and (
            np.any(self.bank_dither != np.floor(self.bank_dither))
            or np.any(np.abs(self.bank_dither) > spec.dither_units_range)
        ):
            raise ValueError("sampling-dither bank codes must be legal discrete commands")

    @property
    def fine_v(self) -> np.ndarray:
        """Decode raw backend codes without an observer correction [V]."""
        return self.spec.adc2_v_min + (self.adc2_code + 0.5) * self.spec.adc2_step_v


def _terms(data: DigitalObservation, start: int, stop: int):
    spec = data.spec
    code = data.rdac_code[start:stop]
    command = split_switch_command(spec, code, data.dem_state[start:stop])
    shape = (len(code), spec.n_active, spec.shape[1])
    plus = np.empty(shape)
    for offset, size, order, count in (
        (0, spec.dac_n_main, command.main_order, command.main_counts),
        (spec.dac_n_main, spec.dac_n_sub, command.sub_order, command.sub_counts),
    ):
        take = np.clip(count[..., None] - np.arange(size), 0, 1)
        np.put_along_axis(plus[:, :, offset : offset + size], order[:, None, :], take, axis=2)
    b_minus_dac = data.common_injection_v[start:stop, None, None] - spec.v_fs * (2 * plus - 1)
    signal = np.ones(shape)
    if spec.dither_mode == "sampling":
        mask_count = int(2 * spec.dither_units_range)
        end = spec.dac_n_main if spec.dither_split_bank == "main" else spec.shape[1]
        if mask_count:
            mask = slice(end - mask_count, end)
            signal[:, :, mask] = 0
            d = data.bank_dither[start:stop]
            voltage = spec.v_fs * (
                2 * (np.arange(mask_count)[None, :] < mask_count // 2 + d[:, None]) - 1
            )
            b_minus_dac[:, :, mask] += voltage[:, None, :]
    return data.slice_ids[start:stop], signal, b_minus_dac


class CalibrationUnidentifiableError(ValueError):
    """Training cannot independently identify the requested physical weights."""


@dataclass(frozen=True)
class FrozenCalibration:
    """Frozen effective C/Cf weights and an independently estimated backend offset.

    Standard errors are marginal linear-model estimates conditional on the
    supplied reference inputs. They do not include unknown reference-source
    systematic error, temporal drift or unmodeled analog dynamics.
    """

    weights: np.ndarray
    offset_v: float
    standard_error: np.ndarray
    rank: int
    condition: float
    residual_rms_v: float
    training_samples: int
    training_digest: str
    v_fs: float

    def __post_init__(self):
        """Validate loaded coefficients and remove mutable array backing storage."""
        weights, se = np.asarray(self.weights), np.asarray(self.standard_error)
        if weights.ndim != 2 or np.any(~np.isfinite(weights)) or np.any(weights <= 0):
            raise ValueError("calibrated weights must be finite and positive")
        if se.shape != (weights.size + 1,) or np.any(~np.isfinite(se)) or np.any(se < 0):
            raise ValueError("standard errors must match every weight and the offset")
        if self.rank != weights.size + 1 or self.training_samples <= self.rank:
            raise ValueError(
                "frozen calibration must identify all coefficients with residual degrees of freedom"
            )
        if (
            not np.all(np.isfinite([self.offset_v, self.condition, self.residual_rms_v, self.v_fs]))
            or self.condition < 1
            or self.residual_rms_v < 0
            or self.v_fs <= 0
        ):
            raise ValueError("calibration diagnostics and voltage scale must be finite and legal")
        object.__setattr__(self, "weights", _readonly(weights))
        object.__setattr__(self, "standard_error", _readonly(se))

    def reconstruct(self, data: DigitalObservation) -> np.ndarray:
        """Reconstruct independent unknown inputs using frozen digital coefficients [V]."""
        data.validate()
        if data.spec.shape != self.weights.shape or data.spec.v_fs != self.v_fs:
            raise ValueError(
                "calibrated geometry/reference scale does not match the digital record"
            )
        out = np.empty(len(data.rdac_code))
        fine = data.fine_v
        for start in range(0, len(out), 256):
            stop = min(start + 256, len(out))
            ids, signal, b_minus_dac = _terms(data, start, stop)
            weights = self.weights[ids]
            numerator = (
                fine[start:stop] - self.offset_v - np.sum(weights * b_minus_dac, axis=(1, 2))
            )
            denominator = np.sum(weights * signal, axis=(1, 2))
            if np.any(denominator <= 0):
                raise ValueError("calibrated signal gain is nonpositive")
            out[start:stop] = numerator / denominator
        return out

    def to_dict(self) -> dict:
        """Export units, coefficients and fit diagnostics without physical truth."""
        result = asdict(self)
        result["weights"] = self.weights.tolist()
        result["standard_error"] = self.standard_error.tolist()
        result["schema_version"] = 1
        result["weight_unit"] = "effective C/Cf"
        return result

    @classmethod
    def from_dict(cls, value: dict) -> FrozenCalibration:
        """Restore a serialized frozen fit, rejecting unknown or malformed versions."""
        values = dict(value)
        if (
            values.pop("schema_version", None) != 1
            or values.pop("weight_unit", None) != "effective C/Cf"
        ):
            raise ValueError("unknown calibrated-weight schema or units")
        values["weights"] = _readonly(values["weights"])
        values["standard_error"] = _readonly(values["standard_error"])
        if (
            values["weights"].ndim != 2
            or np.any(~np.isfinite(values["weights"]))
            or np.any(values["weights"] <= 0)
        ):
            raise ValueError("calibrated weights must be finite and positive")
        return cls(**values)


def fit_unit_weights(
    data: DigitalObservation, known_input_v, *, rank_rtol: float = 1e-10
) -> FrozenCalibration:
    """Fit effective unit charge weights with a separately supplied calibration input.

    Noise settings are not modified. SVD must identify every unit and the offset;
    otherwise an actionable rank error is raised, without hiding null directions
    under nominal priors. Reference-source errors remain part of the fit budget.
    """
    data.validate()
    if not data.static_charge_domain:
        raise ValueError("unit-weight training requires a controlled static charge-gain dataset")
    if np.any(data.overflow):
        raise ValueError(
            f"training has {np.count_nonzero(data.overflow)} clipped conversions; fix headroom"
        )
    x = np.asarray(known_input_v, dtype=float)
    n = len(data.rdac_code)
    p = int(np.prod(data.spec.shape)) + 1
    if x.shape != (n,) or np.any(~np.isfinite(x)) or not 0 < rank_rtol < 1:
        raise ValueError(
            "training reference must be finite, match the record and use legal rank tolerance"
        )
    if n <= p:
        raise CalibrationUnidentifiableError(
            f"{n} samples cannot identify {p} coefficients with residual degrees of freedom"
        )
    design = np.zeros((n, p))
    for start in range(0, n, 256):
        stop = min(start + 256, n)
        ids, signal, b_minus_dac = _terms(data, start, stop)
        terms = signal * x[start:stop, None, None] + b_minus_dac
        block = design[start:stop, :-1].reshape(stop - start, *data.spec.shape)
        for active in range(data.spec.n_active):
            block[np.arange(stop - start), ids[:, active], :] = terms[:, active]
    design[:, -1] = 1
    u, s, vh = svd(design, full_matrices=False, check_finite=False, lapack_driver="gesdd")
    rank = int(np.count_nonzero(s > rank_rtol * s[0]))
    if rank < p:
        raise CalibrationUnidentifiableError(
            f"rank {rank}/{p}: change DEM allocation and dither excitation; more repetitions alone cannot recover null directions"
        )
    theta = vh.T @ ((u.T @ data.fine_v) / s)
    residual = data.fine_v - design @ theta
    variance = float(np.dot(residual, residual) / (n - p))
    se = np.sqrt(variance * np.sum((vh.T / s) ** 2, axis=1))
    weights = theta[:-1].reshape(data.spec.shape)
    if np.any(weights <= 0):
        raise ValueError(
            "estimated physical weights are nonpositive; improve excitation/noise or check model validity"
        )
    digest = hashlib.sha256()
    digest.update(json.dumps(asdict(data.spec), sort_keys=True).encode())
    digest.update(x.tobytes())
    for array in (
        data.slice_ids,
        data.rdac_code,
        data.dem_state,
        data.adc2_code,
        data.bank_dither,
        data.common_injection_v,
    ):
        digest.update(array.tobytes())
    return FrozenCalibration(
        _readonly(weights),
        float(theta[-1]),
        _readonly(se),
        rank,
        float(s[0] / s[-1]),
        float(np.sqrt(np.mean(residual**2))),
        n,
        digest.hexdigest(),
        data.spec.v_fs,
    )


def apply_frozen_calibration(result, calibration: FrozenCalibration):
    """Apply frozen digital coefficients, then update separate simulation scores.

    Only DigitalObservation reaches the reconstruction algorithm. Clean/ideal
    input values are read afterward by this harness solely to report errors.
    """
    out = calibration.reconstruct(DigitalObservation.from_result(result))
    if result.uncalibrated_out is None:
        result.uncalibrated_out = result.out.copy()
    result.out = out
    result.err = out - result.x_ref
    result.err_to_x1 = out - result.sample.x1
    result.err_to_x2 = out - result.sample.x2
    clean = result.sample.x1_clean if result.sample.x1_clean is not None else result.sample.x1
    result.err_vs_clean = out - clean
    result.state.weight_calibration = calibration
    result.calibration_applied = tuple(dict.fromkeys((*result.calibration_applied, "unit_weights")))
    return result


def run_with_split_calibration(
    cfg,
    input_fn,
    n_samples: int,
    *,
    pool=None,
    chip=None,
    n_cal: int = 8192,
    calibration_input_fn=None,
    rng=None,
):
    """Train noisy physical-unit weights, freeze them and validate on the same chip.

    Args:
        cfg: Requested split configuration; charge gain and KTC-off are required.
        input_fn: Independent validation input waveform [V] versus time [s].
        n_samples: Validation record length.
        pool: Optional fixed fabricated pool, reused in training and validation.
        chip: Optional aggregate split realization when constructing the pool.
        n_cal: Training sample count; must exceed the number of fitted coefficients.
        calibration_input_fn: Known reference waveform; default is a distinct
            full-range sine. Driver noise is not substituted into this reference.
        rng: Optional validation noise generator; training uses its own stream.

    Returns:
        SimResult with frozen state.weight_calibration, raw and corrected outputs
        and a serializable calibration_report including the actual training setup.

    Notes:
        Training is an explicitly controlled static transfer measurement, with
        DEM permutation and discrete sub-bank dither. Sampling/RA/driver noise
        remain enabled as requested. Validation retains the requested dynamics.
        This is a behavioral calibration experiment, not an on-chip calibration
        timing/power implementation or a claim about reference-source accuracy.
    """
    from .conversion import ConversionParameters
    from .pipeline import run_pipeline
    from .reconstruction import initialize_state
    from .sampler import sine_input
    from .scheduler import ShuffledScheduler
    from .slice_pool import PhysicalSlicePool

    cfg = replace(cfg, dac_arch="split", calibration="weights")
    cfg.check_legal()
    if cfg.ra_gain_model != "charge" or cfg.ktc_enable:
        raise ValueError("split-unit calibration requires charge gain and a disabled KTC observer")
    if n_cal <= cfg.n_slices * cfg.dac_n_units + 1:
        raise CalibrationUnidentifiableError(
            "training needs more samples than unit weights plus offset"
        )
    if pool is not None and chip is not None:
        raise ValueError("supply a fixed pool or an aggregate chip, not both")
    if pool is None:
        pool = PhysicalSlicePool(cfg, np.random.default_rng(cfg.seed + 8101), chip=chip)
    training_cfg = replace(
        cfg,
        calibration="none",
        dem_enable=True,
        dem_bridge_enable=True,
        dem_mode="permute",
        dither_mode="sampling",
        dither_split_bank="sub",
        dither_discrete=True,
        dither_units_range=min(2, cfg.dac_n_sub // 2),
        dyn_input_settling=False,
        dyn_ref_settling=False,
        dyn_crosstalk=False,
        conversion=ConversionParameters(),
        slice_timing_skew_s=0,
        slice_offset_sigma_v=0,
    )
    train_fn = calibration_input_fn
    if train_fn is None:
        cycles = 103 if n_cal > 206 else 31
        train_fn = sine_input(0.8 * cfg.v_fs, cfg.fs * cycles / n_cal)
    training = run_pipeline(
        training_cfg,
        train_fn,
        n_cal,
        pool=pool,
        rng=np.random.default_rng(cfg.seed + 19001),
        scheduler=ShuffledScheduler(training_cfg, np.random.default_rng(cfg.seed + 19003)),
    )
    known = np.asarray(train_fn(training.sample.t1), dtype=float)
    model = fit_unit_weights(DigitalObservation.from_result(training), known)
    state = initialize_state(cfg)
    state.weight_calibration = model
    validation = run_pipeline(
        cfg,
        input_fn,
        n_samples,
        pool=pool,
        state=state,
        rng=np.random.default_rng(cfg.seed + 19002) if rng is None else rng,
    )
    validation.calibration_report = {
        "method": "static_unit_charge_svd",
        "training_config": training_cfg.to_dict(),
        "training_samples": n_cal,
        "validation_samples": n_samples,
        "rank": model.rank,
        "condition": model.condition,
        "training_residual_rms_v": model.residual_rms_v,
        "training_digest": model.training_digest,
        "same_physical_pool": training.pool is validation.pool,
        "sampling_noise_retained": training_cfg.enable_sampling_noise,
        "ra_noise_retained": training_cfg.ra_enable_noise,
        "reference_source_uncertainty": "not inferred; must be included in a hardware calibration budget",
    }
    return validation
