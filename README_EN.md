# 20-bit SAR ADC behavioral verification

[中文与完整使用指南](README.md) · [Model scope](docs/model_scope.md) · [Implementation evidence](docs/IMPLEMENTATION_CHECKPOINT.md)

Python behavioral verification inspired by ISSCC 2024 Session 9.8. The current
split path couples actual slice charge, causal acquisition, a shared input
network, signed reference events, finite RA/ADC2 response, noisy identifiable
calibration and checked integer reconstruction.

`Config.paper_literal()` selects nine unknown-input decision bits and a separate
fourfold known-dither port candidate. `Config()` retains the historical seven-bit
hypothesis for baseline comparisons. Known dither does not create extra decision
information. The 63+8 topology, detailed DEM exchange, conversion timing and
backend ranges remain explicitly assumed implementations.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy
pytest -q --cov
adi-run-all --results-dir ./output/verification
adi-make-report --results-dir ./output/verification
```

```python
from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.metrics import sine_fit_metrics

cfg = Config.paper_literal(dither_mode="off")
n = 16384
fin = cfg.fs * 307 / n
result = run_pipeline(cfg, sine_input(2.1, fin), n)
words = result.to_codes()
metrics = sine_fit_metrics(words.voltage, cfg.fs, fin)
```

`result.out` remains a floating diagnostic. `to_codes()` uses raw backend codes,
digital switching and nominal/frozen weights with Q30/Q32 coefficients, checked
96-bit accumulation and 20-bit offset-binary words. Analog overflow and final
range clipping remain separate. The unquantized research KTC observer is outside
this integer interface.

`run_with_split_calibration` retains requested noise during controlled static
training, rejects deficient rank, freezes coefficients and validates with an
independent waveform/noise sequence on the same physical chip. The full sweep
checks two chips and two training sizes using final integer words.

Paper (94.2 dB / 9.3 nV/√Hz) and slides (94.6 dB / 8.8 nV/√Hz) remain separate
benchmark records. RA noise inferred from either target is a fitted budget, not
a prediction. PSD uses V²/Hz with explicit ENBW and endpoint normalization.
A stationary slow process can be queried over 64 seconds at actual 40 MHz ADC
timestamps; sparse low-frequency observation does not validate the entire fast
chain or a transistor-level auto-zero noise transfer.

Results are atomic UTF-8 standard JSON with explicit null/status paths for
undefined numerical quantities. Reports are generated from actual evidence.
CI tests Python 3.10–3.13, full acceptance and byte-identical repeat runs within
one environment. No PDK, transistor, layout, silicon power or yield claims are
made. See the Chinese README for citations, references, BSD-3-Clause license
and the independent-implementation/patent-scope declaration.
