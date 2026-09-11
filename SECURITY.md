# Security Policy

## Scope

This is a **research model**, not a networked service: it reads no untrusted
input, opens no sockets, and executes no code from its data files. The attack
surface is therefore small, and the concerns below are about *supply chain and
reproducibility integrity* rather than remote exploits.

In scope:

- anything that lets a third party change the numbers this model reports;
- anything that lets a malicious result file (`results.json`) or figure execute
  code or exfiltrate data on a reader's machine;
- undisclosed use of non-public or third-party material (see below).

Out of scope:

- "vulnerabilities" in the modelled circuit. This repository studies a published
  ADC architecture; it contains no firmware, no RTL, and no product code.

## Supported versions

| Version | Supported |
|:--:|:--:|
| 7.0.x | ✅ |
| < 7.0 | ❌ (superseded; `adi_model_release_v6.1` is kept frozen for audit reference only) |

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting** — do *not* open a public issue:

<https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/security/advisories>

If that is unavailable, open a
[private security advisory](https://docs.github.com/en/code-security/security-advisories)
or contact the maintainer directly. Please include:

1. what you observed and why you believe it is a security issue;
2. the commit or version tested, and the Python / OS / dependency versions;
3. a minimal reproduction (a script is worth a thousand words).

**Response targets:** acknowledge within 5 working days; triage and an initial
assessment within 15 working days. Attribution and licensing corrections are
treated as **high priority** — see the next section.

## Attribution and licensing corrections

Because this repository studies published third-party work (see `NOTICE`), a
*mis-attribution* is a defect of the same severity as a code vulnerability.

If you are a rights holder — or you believe any content here misattributes your
work, reproduces material beyond its licence, or oversteps a patent or trademark
— please report it through the same private channel. Such reports are:

- **not** disclosed publicly while under review;
- acted on promptly, including removal or re-grading of the affected parameter or
  mechanism;
- credited in `CHANGELOG.md` unless you ask otherwise.

## Reporting an *incorrect number* (not a vulnerability)

A model that produces a wrong physical result is a **correctness bug**, not a
security issue. Please open a normal issue instead, using the *Bug report*
template, and include the config, seed, and stage that reproduce it.

The fastest useful report states: *which* quantity is wrong, *what* you expected
and why (paper equation, hand calculation, or an independent simulator), and the
smallest `Config` that shows it.
