# Contributing

The Python API and CLI model the production digital top `adc_digital_ip_v21`. Experiments under `experiments/` identify their own stimulus and source figure. Start with the [project overview](../README.md), [engineering contract](../docs/CONTRACT_CURRENT.md), and [verification status](../docs/STATUS.md).

Record the Git commit for a measurement. Use the package version in `pyproject.toml` for software metadata and update the citation when that value changes. Document release tags, the package number and numerical test results under their own names. [Version history](../docs/meta/CHANGELOG.md)

## Evidence and compatibility

- Keep the input, physical assumptions, output format and comparison target explicit. Label disclosed facts, fitted quantities, engineering choices and unresolved items at the claim boundary.
- Keep acceptance tests independent of the code that reports the result. Put focused checks in [unit tests](../flow/tests/unit/), multi-module cases in [integration tests](../flow/tests/integration/), and counterexamples scoped to a finding in [audit regressions](../flow/tests/audit/).
- Preserve public names, port order and register formats unless the change explicitly updates their contract. Explain unit conversions, array and packet shapes, resets and error paths where they matter.
- Use a fresh output directory for experiments. Document which input, tool and source version produced a result. [Repository layout and evidence locations](../docs/meta/REPOSITORY_LAYOUT.md)

## Local checks

Create a Python environment and install the project with its development dependencies before running the relevant checks.

~~~bash
python -m pip install -e '.[dev]'
PYTHONPATH=src:flow python -m pytest flow/tests -m 'not slow'
~~~

Select tagged tests and the applicable bench for changes beyond the normal Python suite. Tests that invoke simulators require their documented runtimes. The CI workflow and static-analysis settings record the repository’s current check commands. [CI](workflows/ci.yml) · [Python project settings](../pyproject.toml) · [RTL entry points](../rtl/README.md)

Keep simulation, arithmetic proof, formal equivalence, synthesis, STA and layout evidence scoped to the exact run. New numerical results should identify stimulus, random seed, input artifacts, backend and acceptance criterion when relevant. [Provenance ADR](../docs/guide/decisions/0007-provenance-as-a-runtime-value.md)

Optional local hooks use the configuration under `.github/`. From the repository root, install or run the hooks with the explicit config path.

~~~bash
pre-commit install --config .github/pre-commit-config.yaml
pre-commit run --config .github/pre-commit-config.yaml
~~~
