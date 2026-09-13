"""Integer split-ADC reconstruction with explicit coefficient and accumulator widths.

Voltages use signed normalized-Vfs Q32; effective C/Cf weights use signed Q30.
A 96-bit signed accumulator is checked before division (Python integers emulate
wide hardware without wrap). The output is 20-bit offset binary, mid-rise bins
on [-Vfs,+Vfs). Float output is only a decoded diagnostic, never an input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .weight_calibration import CalibrationSpec, DigitalObservation, _readonly, _terms


def round_even_divide(n: int, d: int) -> int:
    """Round a signed integer ratio to nearest, with ties to even."""
    if d <= 0:
        raise ValueError("denominator must be positive")
    q, r = divmod(n, d)
    return q + int(2 * r > d or (2 * r == d and q % 2 != 0))


@dataclass(frozen=True)
class FixedPointFormat:
    """Hardware arithmetic contract; widths include a sign bit where applicable."""

    weight_fraction_bits: int = 30
    voltage_fraction_bits: int = 32
    coefficient_bits: int = 48
    accumulator_bits: int = 96
    output_bits: int = 20

    def __post_init__(self):
        """Reject unsupported or ambiguous integer formats."""
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not (
            self.weight_fraction_bits < self.coefficient_bits <= 62
            and self.voltage_fraction_bits <= 48
            and 8 <= self.accumulator_bits <= 256
            and self.output_bits <= 30
        ):
            raise ValueError("unsupported fixed-point format")


@dataclass(frozen=True)
class CodeStream:
    """Final offset-binary words and separately retained analog/digital clipping flags."""

    code: np.ndarray
    clipped_low: np.ndarray
    clipped_high: np.ndarray
    analog_overflow: np.ndarray
    v_fs: float
    output_bits: int
    peak_accumulator_bits: int

    @property
    def voltage(self) -> np.ndarray:
        """Decode final words to input-referred mid-rise bin centers [V]."""
        return self.v_fs * (2 * (self.code + 0.5) / 2**self.output_bits - 1)


@dataclass(frozen=True)
class FixedPointReconstructor:
    """Frozen quantized coefficients; record processing uses integer arithmetic."""

    spec: CalibrationSpec
    format: FixedPointFormat
    weights_q: np.ndarray
    offset_q: int
    adc2_min_q: int
    adc2_max_q: int

    def __post_init__(self):
        """Validate register values and the bounded int64 mask-sum stage."""
        w = np.asarray(self.weights_q)
        if (
            w.shape != self.spec.shape
            or not np.issubdtype(w.dtype, np.integer)
            or np.any(w <= 0)
            or np.any(w >= 2 ** (self.format.coefficient_bits - 1))
            or sum(map(int, w.flat)) >= 2**60
        ):
            raise ValueError("weights must fit positive signed coefficient and mask-sum registers")
        if any(type(v) is not int for v in (self.offset_q, self.adc2_min_q, self.adc2_max_q)):
            raise ValueError("voltage registers must contain integers")
        if self.adc2_min_q >= self.adc2_max_q:
            raise ValueError("quantized backend range is empty")
        object.__setattr__(self, "weights_q", _readonly(w, np.int64))

    @classmethod
    def from_weights(cls, spec, weights, offset_v=0.0, *, format=None):
        """Quantize nominal or independently calibrated coefficients once, ties to even."""
        fmt = FixedPointFormat() if format is None else format
        w = np.asarray(weights, dtype=float)
        scale = 2**fmt.weight_fraction_bits
        if np.any(~np.isfinite(w)) or np.any(w <= 0) or np.any(w * scale >= 2**62):
            raise ValueError("weight values cannot be represented")
        v = np.asarray([offset_v, spec.adc2_v_min, spec.adc2_v_max]) / spec.v_fs
        if np.any(~np.isfinite(v)) or np.any(np.abs(v) >= 2**30):
            raise ValueError("invalid normalized voltage coefficients")
        q = [int(np.rint(x * 2**fmt.voltage_fraction_bits)) for x in v]
        return cls(spec, fmt, np.rint(w * scale).astype(np.int64), *q)

    @classmethod
    def from_result(cls, result, *, format=None):
        """Build digital registers from nominal geometry or a frozen training result."""
        data = DigitalObservation.from_result(result)
        model = result.state.weight_calibration
        if model is not None:
            return cls.from_weights(data.spec, model.weights, model.offset_v, format=format)
        spec = data.spec
        beta = 1 / spec.dac_n_sub
        unit = result.state.estimated_gain / (
            spec.n_active * (spec.dac_n_main + beta * spec.dac_n_sub)
        )
        weights = np.full(spec.shape, unit)
        weights[:, spec.dac_n_main :] *= beta
        return cls.from_weights(spec, weights, format=format)

    def reconstruct(self, data: DigitalObservation) -> CodeStream:
        """Merge raw backend codes and realizable switch masks into final integer words.

        Out-of-range analog flags are retained; final input-range saturation is
        separate. Arithmetic overflow raises instead of silently wrapping. The
        experimental floating KTC observer has no accepted integer interface.
        """
        data.validate()
        if data.spec != self.spec:
            raise ValueError("digital record does not match the frozen register interface")
        if np.any(data.rdac_code != np.rint(data.rdac_code)):
            raise ValueError("fixed-point reconstruction requires discrete RDAC commands")
        n = len(data.rdac_code)
        output = np.empty(n, dtype=np.int64)
        low, high = np.zeros(n, bool), np.zeros(n, bool)
        vscale, wscale = 2**self.format.voltage_fraction_bits, 2**self.format.weight_fraction_bits
        count = 2**self.format.output_bits
        limit = 2 ** (self.format.accumulator_bits - 1)
        peak = 1
        for start in range(0, n, 256):
            stop = min(start + 256, n)
            ids, signal, b_minus_dac = _terms(data, start, stop)
            # This extraction converts ideal digital switch signs to integers;
            # no analog node voltage, floating output or unknown input is read.
            injection = data.common_injection_v[start:stop]
            rail_sign = (b_minus_dac - injection[:, None, None]) / self.spec.v_fs
            if not np.allclose(rail_sign, np.rint(rail_sign), atol=1e-12, rtol=0):
                raise ValueError("fractional switch masks have no accepted integer interface")
            weights = self.weights_q[ids]
            rails = np.sum(weights * np.rint(rail_sign).astype(np.int64), axis=(1, 2))
            gains = np.sum(weights * signal.astype(np.int64), axis=(1, 2))
            totals = np.sum(weights, axis=(1, 2))
            for j in range(stop - start):
                k = start + j
                fine_q = self.adc2_min_q + round_even_divide(
                    (2 * int(data.adc2_code[k]) + 1) * (self.adc2_max_q - self.adc2_min_q),
                    2 ** (self.spec.adc2_n_bits + 1),
                )
                inj_q = int(np.rint(injection[j] / self.spec.v_fs * vscale))
                gain = int(gains[j])
                if gain <= 0:
                    raise ValueError("quantized signal gain must be positive")
                numerator = (
                    (fine_q - self.offset_q) * wscale
                    - int(rails[j]) * vscale
                    - int(totals[j]) * inj_q
                )
                shifted = (numerator + gain * vscale) * count
                denominator = 2 * gain * vscale
                operands = (
                    (fine_q - self.offset_q) * wscale,
                    int(rails[j]) * vscale,
                    int(totals[j]) * inj_q,
                    numerator,
                    shifted,
                    denominator,
                )
                peak = max(peak, *(abs(x).bit_length() + 1 for x in operands))
                if any(not -limit <= x < limit for x in operands):
                    raise OverflowError(
                        "fixed-point accumulator overflow; widen the declared format"
                    )
                # Floor implements the specified half-open mid-rise ADC bins.
                word = shifted // denominator
                low[k], high[k] = word < 0, word >= count
                output[k] = min(max(word, 0), count - 1)
        return CodeStream(
            _readonly(output, np.int64),
            _readonly(low, bool),
            _readonly(high, bool),
            _readonly(data.overflow, bool),
            self.spec.v_fs,
            self.format.output_bits,
            peak,
        )

    def to_dict(self) -> dict:
        """Export a standard JSON register image with explicit units and schema."""
        return {
            "schema_version": 1,
            "voltage_unit": "normalized V/Vfs",
            "spec": asdict(self.spec),
            "format": asdict(self.format),
            "weights_q": self.weights_q.tolist(),
            "offset_q": self.offset_q,
            "adc2_min_q": self.adc2_min_q,
            "adc2_max_q": self.adc2_max_q,
        }

    @classmethod
    def from_dict(cls, value: dict):
        """Restore and validate a register image without physical model objects."""
        data = dict(value)
        if (
            data.pop("schema_version", None) != 1
            or data.pop("voltage_unit", None) != "normalized V/Vfs"
        ):
            raise ValueError("unknown fixed-point register schema or units")
        data["spec"] = CalibrationSpec(**data["spec"])
        data["format"] = FixedPointFormat(**data["format"])
        data["weights_q"] = np.asarray(data["weights_q"])
        return cls(**data)
