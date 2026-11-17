# Contributing

Use the [project overview](../README.md) to choose the Python, v21, or subsystem entry point. Before changing the model or RTL, check the [engineering contract](../docs/CONTRACT_CURRENT.md), the [source and evidence status](../docs/STATUS.md), and the relevant directory README.

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

Keep simulation, arithmetic proof, formal equivalence, synthesis, STA and layout evidence scoped to the exact run. New numerical results should identify stimulus, random seed, input artifacts, backend and acceptance criterion when relevant. [Provenance ADR](../docs/adr/0007-provenance-as-a-runtime-value.md)

Optional local hooks use the configuration under `.github/`. From the repository root, install or run the hooks with the explicit config path.

~~~bash
pre-commit install --config .github/pre-commit-config.yaml
pre-commit run --config .github/pre-commit-config.yaml
~~~
