# 20-bit SAR ADC design reproduction

Clone the default `main` branch with `git clone https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification.git`. The production RTL, Python model and atlas ship together. Record `git rev-parse HEAD` when citing a result. The Python package version is [9.0.1+adi.repro](../../pyproject.toml). The SystemVerilog top module is `adc_digital_ip_v21`. [Version history](CHANGELOG.md) · [Software citation](CITATION.cff)

The digital controller sequences a two-stage SAR conversion and reconstructs a 20-bit output using a 13-bit ADC2 with a 0.75 V window. The public Python API and CLI use the production transaction model, including the accepted-word sequence, physical slice map, switching decisions and integer reconstruction. The explicit Fig.11 physical study lives under experiments. The engineering target is 40 MS/s with a ±3 V differential input. The project status records the verified scope and implementation limits.

- [Project overview in Chinese](../../README.md)
- [Engineering contract](../CONTRACT_CURRENT.md)
- [Digital IP signal flow, state machines, and verification](../algorithms/README.md)
- [Behavioral model figures and assumptions](../figures/behavioral/README.md)
- [RTL entry points](../../rtl/README.md)
- [Verification and physical implementation status](../STATUS.md)
- [Documentation index](../INDEX.md)
