# Contributing

Thanks for considering a contribution. This repository tries to hold itself to
a stricter standard than typical research code, because its entire value
proposition is *being trustworthy about what it does and does not show*.

## Ground rules

1. **Never let a test grade its own homework.** A check belongs in
   `tests/audit/` or in `run_all.py`, not inside the experiment that produces
   the number being checked. The v6.1 audit showed what goes wrong otherwise:
   several `PASS` results only verified conditions the model had set for
   itself.
2. **Label parameters and reported claims.** Register each top-level `Config`
   field in `PARAM_GRADES` and explain nested model parameters at their owning
   interface. Report the source and assumptions behind physical results; use
   `Graded.require()` at provenance-aware claim boundaries. The numerical
   datapath does not automatically attach a grade to every intermediate array.
   See [`provenance.py`](src/adi_model/provenance.py) and
   [ADR 0007](docs/adr/0007-provenance-as-a-runtime-value.md).
3. **No silent anchors.** If a value is chosen so the model reproduces a
   published figure, it must be tagged `FITTED` and the resulting figure must
   not be described as a prediction.
4. **Determinism is a contract.** All randomness flows through an injected
   `np.random.Generator`. Bare `np.random.*` calls are a bug. Two runs of
   `adi-run-all` must produce identical `results.json` (CI enforces this).

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'
pre-commit install
```

## Before you open a PR

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy --config-file=pyproject.toml  # types
pytest -m "not slow"    # fast suite, includes audit regressions
pytest -m "slow"        # targeted slow tests; this is not the acceptance sweep
adi-run-all --results-dir /tmp/sar-adc-acceptance-new  # full acceptance experiments
```

Use a fresh output directory for each acceptance run. If a change touches the
signal path, run the full sweep as well as relevant tests. The `slow` marker
selects a separate test subset and does not replace `adi-run-all`.

RTL changes require the applicable production lint profiles and self-checking
benches through `python tools/run_open_rtl.py`. Keep simulation, formal,
CDC/RDC, synthesis and target-library STA results separate; an unavailable
stage is `NOT_RUN` or `BLOCKED`, not `PASS`.

## Code and interface contracts

Follow [Engineering standards](docs/ENGINEERING_STANDARDS.md) for Python and
SystemVerilog naming, units, interfaces, reset/error behavior, comments and
verification evidence. Existing public names, port ordering and register
formats are compatibility contracts; a style change must not silently alter
them.

Python docstrings use Google style. Document argument/return units and array
shapes, applicable exceptions and state mutation where relevant. Ruff checks
formatting and docstring structure; the reviewer still checks the engineering
content. Module headers explain purpose, model/source boundary and legal
operating domain. Keep historical debugging narratives in an ADR or evidence
record rather than in a production interface contract.

## Adding a test

- Unit tests: `tests/unit/` — pure functions, no full-simulation runs.
- Integration: `tests/integration/` — multi-module, still fast (< 5 s).
- Audit regressions: `tests/audit/` — one file per audit finding. Each test
  must recompute the quantity *independently*; it may not call the experiment's
  own `PASS` field.
- Slow sweeps: mark with `@pytest.mark.slow`.

## What not to do

- Do not add a stage whose purpose is to make a headline number look better.
- Do not fit a parameter to a published number and then report the reproduced
  number as validation.
- Do not move a `PASS` criterion into the code that produces the value.
