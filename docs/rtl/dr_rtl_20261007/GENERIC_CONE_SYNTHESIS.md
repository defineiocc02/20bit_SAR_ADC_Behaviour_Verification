# Dr. RTL arithmetic-cone generic synthesis snapshot

Date: 2026-10-07 (Asia/Shanghai)
Source checkout HEAD: `01ed2b3e4fc4c7c9efd2153cfbfa5d456b10fb74`
Tool: native Pyosys adapter, Yosys 0.66 (`86f2ddebc`), using the already-installed project runtime.
Current artifact root: `/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_dr_rtl_analysis_20261007/cone_synthesis`.

Scope is read-only cone synthesis for current `adc2_dec`, `cal_residue_mac`, and `div_floor` at `P_STAGES=5` and `7`. The synthesis ran in the source checkout with cwd `/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260919/calibration-closure`. After capture, only this `cone_synthesis` evidence directory was moved here. The exact cwd, commands, and original relative output paths in `run_manifest.json`, `.ys`, and raw logs are preserved as run provenance; the logs and source command records were not edited to imply a new run at this destination.

## Procedure and measurement limits

For each case the preserved `.ys` script does `read_verilog -sv -Irtl/params`, (for divider variants `chparam -set P_STAGES N div_floor`), `hierarchy -check`, `proc`, `opt`, `check -assert`, `stat -width`, and writes a pre-synthesis JSON. It then runs `synth -top <module> -noabc`, repeats `check -assert` and `stat -width`, and writes generic JSON and Verilog. Exact argument vectors, working directory, source SHA-256 values, and the tool version are in `run_manifest.json`; raw logs and scripts are in `cases/`.

“Generic gates” are Yosys internal one-bit primitives after generic `synth -noabc`. They compare this exact elaboration and pass sequence; they are **not** standard-cell area, ASIC mapping, physical design, or timing results.

The unit-depth metric is computed directly from the preserved generic JSON. Every primary input bit and every sequential Q bit is a level-0 source. Each bit-level combinational primitive adds one level after its deepest input. The analyzer propagates through all combinational cell inputs and outputs, so output cones include their full combinational logic. In particular, `adc2_dec.ovf` includes range detection and `fine_q` includes the clamp selection; `cal_residue_mac.any_ovf` includes all operation, numerator, denominator, and shifted-range checks. The reported divider path to state D includes the entire combinational D cone, including arithmetic and data muxes present on that cone. Divider enable and reset input cones are measured and reported separately as sequential pin endpoints (`E` and `R`); they are not folded into the D number. Clock pins are excluded. FF counts are checked as bit-sliced generic cells (`Q` width 1 each), not counted as wide FF instances. Output loads, cell delay, drive strength, fanout delay, wire RC, setup/hold, clock-tree effects, and analog macro delays are absent. These structural depths are path indicators only—not WNS or Fmax.

## Multiplier width interpretation

The `$mul` cell widths in the pre-synthesis JSON include explicit zero/sign extension and must not be read as counts of independent variable bits or as physical multiplier dimensions. For `adc2_dec`, the RTL factor `{adc2_code,1'b1}` has **13 variable data bits** (12-bit code plus the center bit); `delta` has 65 signed data bits. The earlier `13x65` shorthand names those independent data operands. Yosys records a width-78 `$mul`, then its `alumacc` log reports removal of constant high bits leaving **14x65 generic operand-port widths**. The extra 14th A-side bit is a sign/guard bit introduced by the signed wrapper/extension, not a 14th independent variable. The 14x65 log result and 13x65 source-data description therefore refer to different representation boundaries, not two circuit designs. Do not treat either frontend port width as a fixed physical multiplier constant.

Likewise, `cal_residue_mac` multiplies 64-bit unsigned `total_s` by 64-bit signed `inj_r` (64x64 independent data bits). Its width-130 `$mul` has explicit extension; Yosys trims it to 65x64 generic operand-port widths, where the extra A-side bit is a constant/sign-protection bit rather than an additional independent variable. This is a generic lowering result, not technology mapping.

## Results

| Cone | Pre-synthesis arithmetic structure | Generic gates | FF bits | Latches / memories | Longest observed generic path |
|---|---|---:|---:|---:|---|
| `adc2_dec` (ADC2=12, V=64) | 13x65 independent multiply operands; 65-bit range subtraction; 78/79-bit add/compare/round/clamp logic | 6,533 | 0 | 0 / 0 | 81 levels to `fine_q`; 80 to overflow output `ovf` |
| `cal_residue_mac` (SUM=64, V=64) | 64x64 independent multiply operands; 132-bit subtract chain and overflow/range comparisons; 97-bit sum then 117-bit shift/check | 29,157 | 0 | 0 / 0 | 78 levels to `a1`; 95 to overflow output `any_ovf` |
| `div_floor`, P5 | five serial 64-bit trial subtract/borrow cells in the unrolled group; P_W_A=63, W_PAD=65, N_CYC=13 | 3,862 | 317 | 0 / 0 | 140 levels to deepest state D input (`q[62]` etc.); separate max `E=5`, `R=3` |
| `div_floor`, P7 | seven serial 64-bit trial subtract/borrow cells in the unrolled group; P_W_A=63, W_PAD=63, N_CYC=9 | 4,917 | 313 | 0 / 0 | 184 levels to deepest state D input (`q[62]` etc.); separate max `E=5`, `R=3` |

The divider source does not use a separate full-width compare followed by a subtract: each stage computes the widened subtraction once, takes its high borrow bit as the trial decision, then selects either the difference or the prior shifted remainder. That avoids duplicating comparison and subtraction, but serially links each stage's borrow/result into the next stage. The P7 variant cuts iterative latency from 13 to 9 cycles while increasing generic combinational gates by 27.3% and the measured unit-depth to the deepest D bit by 31.4%. P5 uses four more state bits because both `mag` and `quo` pad to 65 bits. These are architecture comparisons under this generic pass flow, not a PPA win/loss judgment.

The final divider result also has a dependent correction cone: for negative inputs, `q_next` is incremented when `r_next != 0`, then negated to implement floor toward negative infinity. This adds a wide add/negate and selection after the unrolled divide chain, and is part of the measured longest path to the output register. Thus the critical structural path is not only “N subtractors”; it includes remainder correction, sign handling, and final commit muxing.

## Frontend and structural checks

All four cases elaborated, synthesized, and passed both `check -assert` calls with zero reported structural problems. The post-synthesis JSON contains no residual internal arithmetic cells and no inferred memory/latch cells; arithmetic operators have been lowered into generic gates. The combinational ADC and MAC cones infer no state. The divider has 317/313 bit-sliced FF cells for P5/P7, each one bit, and no latch or memory cells.

Yosys emits a bounded frontend warning that unpacked combinational `r_chain` is converted to a list of registers. This explicitly lowers the source array into combinational stage signals (not an inferred RAM and not a silently omitted construct); the generated generic netlist contains all five/seven subtract stages. It does not indicate a latch.

## Artifacts

- `run_manifest.json`: exact commands, original execution cwd, source hashes, parameters, Yosys version, exit codes, and original run-time output paths.
- `cases/*.ys`: exact Yosys pass scripts as executed.
- `cases/*.log`: complete raw synthesis logs, preserved unchanged.
- `cases/*.pre.json`: elaborated/pre-generic-synthesis cell structures and operator widths/signedness.
- `cases/*.generic.json`, `cases/*.generic.v`: lowered generic netlists.
- `synthesis_summary.json`: cell counts, primitive families, sequential/latch/memory checks, residual arithmetic check, output depths, and sequential D/E/R pin depths.
- `sources/rtl/...`: frozen byte copies of the exact input RTL and parameter header.
- `analyze_generic.py`: parser for the preserved generic JSON netlists; rerunning it recalculates summaries only and does not invoke synthesis.
- `sha256sum.txt`: outer checksums of all evidence files except the checksum file itself; regenerate after any artifact edit.

The user-facing timing question still requires a target library, constraints, and STA. These generic cone results identify where a real path report should be inspected first—especially `cal_residue_mac.any_ovf` and `div_floor` P7's output-commit path—but cannot establish WNS, Fmax, or 640 MHz feasibility.
