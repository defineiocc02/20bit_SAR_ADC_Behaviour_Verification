# 20-bit SAR ADC — Behavioural Verification Model

[![CI](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml/badge.svg)](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-green.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2f6f9f.svg)](https://mypy-lang.org/)
[![Tests](https://img.shields.io/badge/tests-189%20passed-brightgreen.svg)](#5-the-test-suite-and-the-gates)

> **A behavioural model of a two-stage residual SAR ADC, written to be _audited_
> rather than believed.**
> Inspired by ISSCC 2024 Session 9.8 (Bodnar et al., 20-bit / 40 MS/s precision
> SAR). Every number it produces carries a machine-checkable statement of where
> that number came from.

**中文版（默认首页）：[README.md](README.md)** ｜ Licence and open-source declaration：[§12](#12-license-and-open-source-declaration)

---

## Contents

| § | Section |
|:-:|:---|
| 1 | [What this is](#1-what-this-is) |
| 2 | [Why it looks the way it does](#2-why-it-looks-the-way-it-does) |
| 3 | [Source grading — the load-bearing idea](#3-source-grading--the-load-bearing-idea) |
| 4 | [Install](#4-install) |
| 5 | [The test suite and the gates](#5-the-test-suite-and-the-gates) |
| 6 | [What this model does *not* support](#6-what-this-model-does-not-support) |
| 7 | [Reproducibility](#7-reproducibility) |
| 8 | [Repository layout](#8-repository-layout) |
| 9 | [Contributing](#9-contributing) |
| 10 | [Citation](#10-citation) |
| 11 | [References](#11-references) |
| 12 | [License and open-source declaration](#12-license-and-open-source-declaration) |

---

## 1. What this is

A phase-accurate Python model of the ADC described in `[00]`: a first stage that
coarsely quantises with a small, fast SADC, drives an 18-slice RDAC through a
residue amplifier, and hands the residue to a backend ADC2; a digital
reconstruction that combines the two readings; and DEM / dither / gain-β
calibration layered on top.

It is a *design-exploration* model: you can switch mechanisms on and off and
watch where the error goes. It is **not** a transistor-level simulator, not a
PDK-accurate mismatch study, and not a substitute for silicon — see
[§6](#6-what-this-model-does-not-support).

**What you get out of the box**

| | |
|:---|:---|
| 27 modules, ~12.5 kLOC of library code | graded parameters, physical slice pool, two independent signal chains |
| 24 acceptance stages | each returns data **and** an explicit pass/fail criterion |
| 189 tests | including one adversarial regression per audit finding |
| 8 ADRs | the reasoning behind every structural decision |

---

## 2. Why it looks the way it does

An external audit of the previous release (`adi_model_release_v6.1`, kept frozen
outside this repository for reference) found eleven defects that the tree's own
self-checks could not catch, because each stage validated its output against its
own assumptions. The most damaging:

| # | Finding (v6.1) | Effect |
|:-:|:---|:---|
| A01 | read "9b quantization in the first stage" as 6b coarse + 3b codeword | only 64 input decision levels, not 512 |
| A02 | re-drew an independent 8-of-18 slice permutation every cycle | 3.56/8 converting slices held the sample being converted |
| A03 | dither code mixed coarse-step and RDAC-unit-step units | missing factor of 8; overflow 32.7% → 16.0% once fixed |
| A04 | RA noise reverse-solved from the target DR | reproducing 94.6 dB was an identity, not a prediction |
| A05 | KTC observer presented alongside disclosed mechanisms | original work reading as a published capability |
| A06 | dynamic reference buffer / shared RA not modelled at all | −1.6 dB and +1.3 dB unexplained |
| A07 | static / low-frequency / figure evidence over-claimed | 6 sub-findings, incl. a Monte-Carlo histogram drawn from 400 resampled points and an `np.gradient` slope that is 25.6 dB low at 19 MHz |

The response was structural, not cosmetic:

- source grading became a **runtime value** instead of a comment tag;
- the slice pool became a **physical object** with memory across cycles;
- the two signal chains share **one constructor** for the quantiser grid;
- the noise budget is **labelled as an anchor**, not a prediction;
- the KTC branch is graded `RESEARCH_EXTENSION` and **off by default**;
- the slope used by interleaving skew comes from an **exact / spectral**
  derivative, not a central difference.

Full mapping, finding by finding, in
[`docs/audit_response.md`](docs/audit_response.md); the reasoning behind each
decision is in [`docs/adr/`](docs/adr/).

> **Note on labels.** The audit report numbers its findings `A01`–`A07`. The
> adversarial tests use shorter `F1`–`F10` labels as stable test identifiers.
> The mapping is in the module docstring of
> `tests/audit/test_audit_findings.py` and in `docs/audit_response.md`.

---

## 3. Source grading — the load-bearing idea

Every `Config` field must declare *where its number comes from*, and the grade
travels with the value:

```python
>>> from adi_model import Config, SourceGrade, annotate_config, audit_provenance
>>> ann = annotate_config(Config.paper_consistent())
>>> ann["g0"]
Graded(32.0, [披露], '[00_1] figure annotation: G0 = 32', unit='')
>>> ann["mismatch_sigma0"].require(SourceGrade.DISCLOSED)
Traceback (most recent call last):
    ...
adi_model.provenance.GradingError: value graded FITTED ([拟合]) from
'100 ppm behavioural calibration; NOT a PDK value' is not valid here; ...
```

| Grade | Meaning | Quotable as fact |
|:---|:---|:--:|
| `DISCLOSED` `[披露]` | transcribed from the paper / slides | ✅ |
| `DERIVED` `[推导]` | algebra on disclosed values, no free parameter | ✅ |
| `FITTED` `[拟合]` | chosen so the model reproduces a published figure — **an anchor, not a prediction** | ❌ |
| `ASSUMED` `[假设]` | no published source; sensitivity study only | ❌ |
| `RESEARCH_EXTENSION` `[研究扩展]` | original to this repository, not in the literature being modelled | ❌ |

`audit_provenance(cfg)` is the first thing a reviewer should read — it lists
which live assumptions a headline number rests on:

```python
>>> rep = audit_provenance(Config())
>>> rep["verdict"]
'OK: no ungraded parameters'
>>> rep["counts"]
{'disclosed': 11, 'derived': 9, 'assumed': 57, 'fitted': 3, 'research_extension': 8}
```

A `Config` field added without a grade fails the test suite. A grade-table entry
for a field that no longer exists fails too. That is the whole mechanism: it
turns "someone forgot to label a parameter" from a post-publication correction
into a red build.

---

## 4. Install

Requires Python ≥ 3.10.

```bash
git clone https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification.git
cd 20bit_SAR_ADC_Behaviour_Verification

python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

### Sixty seconds to the first number

```python
from adi_model import Config, sine_input, run_sim_split, sine_fit_metrics

cfg = Config(dac_arch="split", dem_enable=True, dither_mode="sampling")
res = run_sim_split(cfg, sine_input(0.9 * cfg.v_fs, 2.5e6), 2**15)
print(round(sine_fit_metrics(res.out, cfg.fs, 2.5e6)["SNDR_dB"], 2))
# -> 91.98   (single run, fixed seed; not a claim about the published chip)
```

### The full acceptance sweep

```bash
adi-run-all        # all 24 stages -> tools/results/results.json + 7 figures
adi-make-report    # pack results.json + figures into one self-contained HTML
```

Or without the console scripts:

```bash
PYTHONPATH=src python tools/run_all.py
```

Set `ADI_MODEL_RESULTS_DIR` (or pass `--results-dir`) to write outputs somewhere
other than `tools/results/`.

---

## 5. The test suite and the gates

```bash
pytest                  # 189 tests, ~22 s, no long sweeps
pytest -m audit         # only the adversarial regressions from the audit
pytest --cov=adi_model  # branch coverage, floor 35%
```

| Gate | Command | Before 7.0.0 | Now |
|:---|:---|--:|--:|
| Lint | `ruff check .` | **6797** errors | **0** |
| Format | `ruff format --check .` | 37 / 38 files | **0** |
| Types | `mypy --config-file=pyproject.toml` | **147** errors | **0** |
| Tests | `pytest` | — | **189 passed, 4 xfailed** |

The lint and type gates were *configured* but could never pass, which is the same
as not having them. They are recorded as findings **C3** and **C4** in
[`docs/audit_response.md`](docs/audit_response.md), alongside two CI defects
(**C1**: a flag the CLI does not define; **C2**: a "full sweep" that collected
zero tests). None of the changes these fixes required altered a single number:
`tools/results/results.json` is **byte-identical** before and after.

---

## 6. What this model does *not* support

Read [`docs/model_scope.md`](docs/model_scope.md) before quoting anything. The
short version:

- **No 20-bit code-domain claim.** `n_bits_target` defines the LSB reference; the
  output is an analog-voltage equivalent, not a 20-bit encoder output. A test
  asserts that changing `n_bits_target` does not change a single output sample,
  precisely so this boundary stays visible.
- **No yield prediction.** The unit-mismatch sigma is either a 100 ppm
  behavioural calibration (`FITTED`) or a Pelgrom-style area-law estimate
  (`ASSUMED`). Neither is a PDK measurement. Monte-Carlo results are sensitivity
  studies.
- **No area / power claim.** The capacitor-shrinking study scales capacitance,
  not a layout.
- **The 1/f corner is below the main record.** At the default `fs`/`N` the FFT
  bin is ~1.2 kHz and the 40 Hz corner is unresolvable. The flicker generator is
  validated separately at a low `fs`; the main record carries no flicker.
- **The KTC observer is our idea, not the paper's.** It is modelled, graded
  `RESEARCH_EXTENSION`, and off by default. It is not evidence about the
  published chip.

---

## 7. Reproducibility

- **No global random state.** Every stochastic call takes an explicit
  `numpy.random.Generator`; `tests/integration/test_pipeline_equivalence.py`
  scans the sources to keep it that way.
- **Bitwise reproducibility.** Two runs with the same seed give identical output
  arrays; asserted for both signal chains. CI runs the sweep twice and diffs
  `results.json` byte for byte.
- **Two independent chain implementations must agree.** `sim_split.py` and
  `pipeline.py` implement the same signal flow independently and are asserted to
  agree **bit for bit** with all non-idealities off. They silently disagreed for
  two revisions because each derived the quantiser step itself — see
  [ADR 0004](docs/adr/0004-single-source-of-truth-quantiser-grid.md).

---

## 8. Repository layout

```
20bit_SAR_ADC_Behaviour_Verification/
├── src/adi_model/             the library (27 modules)
│   ├── provenance.py          source grading: SourceGrade / Graded / PARAM_GRADES
│   ├── config.py              every parameter, graded, with validate()
│   ├── slice_pool.py          physical 18-slice pool, cross-cycle causality
│   ├── sadc.py                the one true quantiser-grid constructor
│   ├── dac_arch.py            unary vs. segmented (main/sub + bridge cap) DAC
│   ├── pipeline.py            phase-accurate signal chain
│   ├── sim_split.py           independent split-DAC chain
│   ├── experiments.py         24 acceptance stages; each returns data + criterion
│   └── cli.py                 console entry points
├── tests/
│   ├── unit/                  provenance, slice pool, CLI wiring
│   ├── integration/           cross-module invariants (equivalence, determinism)
│   ├── audit/                 one test per audit finding — all failed on v6.1
│   └── regression/            defects found by *running* the model, not reading it
├── tools/
│   ├── run_all.py             full sweep  -> results.json + figures
│   └── make_report.py         self-contained HTML report
├── docs/
│   ├── model_scope.md         what can and cannot be claimed   <- read this
│   ├── audit_response.md      finding-by-finding response
│   ├── review_response_2026-09-11.md    external review, adjudicated (round 2)
│   ├── review_response_2026-09-11b.md   external review, adjudicated (round 3)
│   ├── review_response_2026-09-11c.md   external review, adjudicated (round 4)
│   ├── STATUS.md              pre-release snapshot of the project state
│   └── adr/                   8 architecture decision records (0001-0008)
├── CITATION.cff               machine-readable citation metadata
├── NOTICE                     third-party references and attribution
└── LICENSE                    BSD-3-Clause + scope of rights
```

---

## 9. Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). In short: `ruff`, `mypy` and `pytest`
must pass; new parameters must be graded; new mechanisms must be switched off by
default and graded `RESEARCH_EXTENSION`.

Security issues: see [`SECURITY.md`](SECURITY.md).
Community expectations: see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

---

## 10. Citation

If this model is useful to your work, please cite it — the citation records the
version, so a reader can reproduce your numbers.

```bibtex
@software{zhao_2026_sar_adc_behaviour_model,
  author    = {Zhao, Reed},
  title     = {20-bit SAR ADC Behavioural Verification Model},
  version   = {7.0.6},
  year      = {2026},
  publisher = {GitHub},
  url       = {https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification},
  license   = {BSD-3-Clause},
  note      = { Independent academic behavioural model. Not affiliated with,
                endorsed by, or licensed from Analog Devices, Inc. }
}
```

Machine-readable metadata for GitHub's *Cite this repository* button lives in
[`CITATION.cff`](CITATION.cff).

**Please also cite the architecture being modelled** (`[00]` in
[§11](#11-references)) — this repository studies that work; it does not replace
it.

---

## 11. References

| Tag | Document |
|:---|:---|
| `[00]` | R. Bodnar et al., "A 9.3 nV/√Hz 20 b 40 MS/s 94.2 dB DR Signal-Chain Friendly Precision SAR Converter", **ISSCC 2024**, Session 9.8, pp. 182–183. DOI: `10.1109/ISSCC49657.2024.10454329` |
| `[00_1]` | the same work's slide deck (figures and annotations cited by page) |
| `[09]`–`[14]` | the patent family covering the quantiser/RDAC split, sampling-side dither, DEM ordering, interleaving, auxiliary input charge and the low-power reference |

Parameter-level citations live in `provenance.PARAM_GRADES`, one line per
parameter. The complete third-party list, including background architectures that
are *not* part of the target chip, is in [`NOTICE`](NOTICE).

---

## 12. License and open-source declaration

**Code licence: BSD-3-Clause — see [`LICENSE`](LICENSE).**
You may use, modify and redistribute this software, including commercially,
subject to the three BSD conditions (retain the copyright notice, reproduce it in
binary distributions, and do not use the authors' names for endorsement).

### 12.1 Independence and non-affiliation

This repository is an **independent academic behavioural model**. It is
**not** affiliated with, endorsed by, sponsored by, or licensed from
**Analog Devices, Inc.** The author has no affiliation with Analog Devices.

"Analog Devices", "ADI" and "LTC" are trademarks of Analog Devices, Inc. Their
appearance in this repository is **nominal use** solely to identify the published
work being studied, and implies no endorsement of this repository.

### 12.2 What was and was not used

| | |
|:---|:---|
| ✅ Used | **published** literature only: the ISSCC 2024 paper and slide deck, published patents, and public datasheets. All are cited in [`NOTICE`](NOTICE) and none is redistributed here. |
| ❌ Not used | no silicon, no netlist, no layout, no PDK, no design database, no internal document, no non-public information of any kind. |

Every quantity is either transcribed from a public source (`[披露]`), derived
algebraically from such values (`[推导]`), **calibrated to reproduce a published
figure** (`[拟合]`), or assumed for sensitivity study (`[假设]`). Reverse-engineered
parameters are **not** presented as independent measurements of any product.

### 12.3 No patent licence; no warranty

Nothing in this repository grants any licence, express or implied, under any
patent or other intellectual property right of any third party. The patents
listed in `NOTICE` are cited as **mechanism references only**; the embodiments
described in them are not the implementation shipped here.

The software is provided **"AS IS"**, without warranty of any kind, and the
authors are not liable for any claim or damage arising from its use. It is a
research model, **not** a design-signoff tool: do not use it to make production,
safety-critical, or yield decisions.

### 12.4 Original contributions

The **KTC-noise cancellation branch** (`adi_model/ktc.py`) is an **original
research extension** by this repository's authors. It is graded
`RESEARCH_EXTENSION` in code, disabled by default, and is **not** a feature
disclosed in `[00]` or in patents `[09]`–`[14]`. **Do not attribute it to
Analog Devices.**

### 12.5 Reporting a problem with this declaration

If you are a rights holder and believe any content here misattributes your work
or oversteps a licence, please open a
[confidential-advisory issue](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/security/advisories)
or see [`SECURITY.md`](SECURITY.md). Attribution and licensing corrections are
treated as high priority and acted on promptly.
