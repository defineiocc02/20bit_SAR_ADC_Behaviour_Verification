# Security policy

Report concerns about the Python package, SystemVerilog sources, build tools, generated artifacts, or the integrity of published results for this repository. Include the affected file or artifact and the output of `git rev-parse HEAD`. For an installed distribution, include its package version and installation source. [Development branch and releases](../docs/meta/CHANGELOG.md)

## Private reporting

Use [GitHub private vulnerability reporting](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/security/advisories/new). If that channel is unavailable, contact the maintainer privately. Include the observed behavior, expected behavior, environment, and the smallest reproducer that demonstrates the issue.

The response targets are acknowledgment within five working days and an initial assessment within fifteen working days. The maintainer handles attribution and licensing concerns with the same priority as security defects. The earlier maintenance policy for the 7.0.x line is preserved in [the policy at its recorded revision](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/d97afa160a7c9c0ff2f1427656e5899b360771e2/.github/SECURITY.md).

## Attribution and licensing

Rights holders can use the same private channel to report attribution, licensing, patent, or trademark concerns. Reports remain private during review. Remedies may include correcting a source grade or removing affected material. The maintainer credits the reporter in the change history unless the reporter opts out. [Third-party notices](../docs/meta/NOTICE)

## Incorrect model or RTL results

For ordinary correctness errors use the [bug report template](ISSUE_TEMPLATE/bug_report.md). Supply the stimulus, expected output, actual output, and its basis. A sample tag and the relevant switch states or output codes help locate a digital conversion error.
