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
2. **Label every number.** Any value that leaves a public function and could be
   quoted in a paper must carry a `SourceGrade`. If you introduce a parameter,
   declare `DISCLOSED`, `FITTED`, `ASSUMED`, `DERIVED` or
   `RESEARCH_EXTENSION` and say why. See `docs/provenance.md`.
3. **No silent anchors.** If a value is chosen so the model reproduces a
   published figure, it must be tagged `FITTED` and the resulting figure must
   not be described as a prediction.
4. **Determinism is a contract.** All randomness flows through an injected
   `np.random.Generator`. Bare `np.random.*` calls are a bug. Two runs of
   `adi-run-all` must produce identical `results.json` (CI enforces this).

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pre-commit install
```

## Before you open a PR

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy                    # types
pytest -m "not slow"    # fast suite, includes audit regressions
pytest -m "slow"        # full sweeps (minutes)
```

If your change touches the signal path, run the full sweep. A change to a shared
helper has broken downstream modules before; the fast suite will not catch it.

## Docstrings

Google style, enforced by `ruff`'s `D` rules. Every public function needs a
one-line summary, `Args`, `Returns`, and — if it mutates anything —
`Side effects`. Every module needs a header with: purpose and boundary,
physical model and literature reference, unit contract, source grading, and
domain of applicability.

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
