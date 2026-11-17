# 20-bit SAR ADC design reproduction

The supported integrated ADC is v21. Its digital controller sequences a two-stage SAR conversion and reconstructs a 20-bit output using a 13-bit ADC2 with a 0.75 V window. The Python model uses the same ADC2 width and window for mechanism studies. The engineering target is 40 MS/s with a ±3 V differential input. The project status records the verified scope and implementation limits.

- [Project overview in Chinese](../../README.md)
- [Engineering contract](../CONTRACT_CURRENT.md)
- [Technical diagrams and modeled results](../figures/20261117/README.md)
- [RTL entry points](../../rtl/README.md)
- [Verification and physical implementation status](../STATUS.md)
- [Documentation index](../INDEX.md)
