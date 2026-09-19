# Complete RTL repair checkpoint

User requested complete RTL remediation, continuing PR #3. Base: 85b4b49.
Scope: all 16 RTL modules, top-level timing/configuration contracts and executable
regressions. Preserve the fixed-point numerical contract; performance claims
require actual implementation evidence. No main merge.

Milestones:
1. Core audit: signed sampling rails, parameter derivation/guards, balanced
   reduction/popcount, divider fixed-slice shifting, reconstruction cancellation.
2. Top-level: fixed-phase ready/data capture, sample validity and flag alignment,
   complete epoch reset, atomic controls, sticky write errors, strict read decode,
   explicit dither mode routing; bound parallel weight selection to actual banks.
3. Regression: independent negative controls, directed protocol and boundary
   cases, existing full-code and end-to-end vectors, warning checks.
4. Review synthesis structure and limitations, publish, verify final CI.

Initial confirmed defects: unit_therm signed/unsigned rail comparison untested by
old P1; sadc_enc default comparator count uses global B1 instead of P_B1; top
uses live data/ignores ready, clear leaves timing/DEM/switch state intact; weight
write errors can disappear; invalid reads alias a legal cell; serialized control
clear can leave partially updated fields; recon accepts starts while unconfigured.
In progress: milestone 1. All later results must distinguish RTL simulation,
script tests, synthesis and physical signoff.

Milestone 1 implemented: balanced reconstruction/popcount trees, fixed-slice
iterative divider, recon cancellation and index detection, geometry checks and
signed rail arithmetic. Existing P1/P2 arithmetic/full-code tests pass. Added
exhaustive small-width divider/rail/popcount and recon protocol benches.
Milestone 2 in progress: phase capture, drop-on-missing-ready, atomic 4-bit
controls (quantizer enable bit 3), mode separation, all timing state epoch reset,
strict read decode, sticky write errors and explicit two-bank weight mux.
P3 stimulus is being corrected to present flags with their own transaction,
rather than the previous workaround that delayed flags into the next sample.

User explicitly confirmed: preserve interfaces and cadence, finish verifiable fixes.
Milestones 1 and 2 implemented. Boundary/mode tests pass; two production lint
hierarchies now gate arithmetic and structural diagnostics separately from TBs.
Milestone 3: final full RTL/Python regressions and baseline negative controls
running. Milestone 4: ADR 0017 and module-by-module report authored. EDA host
read-only SSH probe timed out; no new mapped PPA claim is permitted.

All eleven RTL benches passed, including 1,048,576-code oracle and P3 4095 rows.
Three baseline-module negative controls detected the intended failures after
successful compilation. Latest cancellation-on-completion regression passes.
Python full run exposed three constant-parser failures after explicit int casts;
the parser now implements SV signed 32-bit conversion and all 18 mirror tests
pass. Full Python recheck is running before final publication. No pending RTL
functional changes. Draft PR/Issue updates are prepared outside the repository.

Functional repair milestone complete locally: final exact RTL passes all eleven
benches and both production lint hierarchies; Python full rerun 571 passed / 3
remote-slow deselected. Evidence manifest captures current sources and logs.

User expanded the objective: RTL must correspond to actual patent/paper circuit
structure, not stop at verification. PDF and SAR ADC skills read. Source extraction
started in ../circuit_sources; 00/00_1/12 contain text, 09/10/11/13/14 are scanned
and require figure inspection plus OCR/primary patent text. Current fixes will
be published as a completed milestone, not as full circuit reproduction.
Pending async question: preserve compatibility top + add structural top (recommended),
expand existing ports, or strictly preserve ports. Prior user confirmation was
preserve current top ports/cadence; retain compatibility until clarified.

Scope update accepted by user: directly expand the current top-level ports and
internal architecture. User explicitly requests multiple files/modules for debug
and adjustment, prioritizing the core digital calibration algorithm RTL.
Next milestone: modular physical-weight correction engine, then source-mapped
SAR/flash, 8-of-18 causal allocation, DEM, reference/AZ/auxiliary control integration.
Do not call this full transistor-level reproduction: original silicon is 40nm,
and sources do not disclose device sizes/layout or exact coefficient estimator.
Functional repair commit 508206670b7be8810bbe353d4e2c0ea51f1f8d74 was published
successfully to the existing PR #3. Issue/PR bodies updated via gh (check session
59504 if necessary). New structure work is uncommitted after that milestone.

Source findings directly checked:
- [00] PDF p1 + p2 Fig9.8.1/.2/.3; [00_1] slides12/31 visually inspected.
- Two alternating SAR quantizers (one slice each), shared **3-bit** flash, 8
  converting + 8 acquiring RDAC slices chosen from18, shared RA gain32/ADC2.
- RDAC follows resolved SAR decisions; no 511-comparator flash-equivalent model
  should be advertised as source architecture.
- Shared top-plate quiet edge samples input and prior RA residue together.
- Reference precharge buffer during conversion, accurate REF_IN during RA;
  ~65% RA time for full settling. Source says40nm, not existing28nm synth library.
- Slide31: 8/18 selection +3-bit horizontal+3-bit vertical DEM, binary/unary
  bridge with P=50% and illustrated weights8/4/2/1. Exact71-unit mapping remains
  model-derived and must not be presented as disclosed layout.
- [00] coefficients derived externally, DAC weight correction on-chip.
- Patents have alternative embodiments, do not blindly combine all examples.

Text/artifacts outside repo: ../circuit_sources (PDF text, source figure PNGs,
US*_primary.txt downloaded from Google Patents). 00/00_1/12 text extractable;
other user patent PDFs scanned. primary text has Description and Claims sections.
For US10511316B2, real Description begins line1492, detailed DEM line1710+,
Figs19-21 near1748, Fig25 near1757, Fig29/30 near1770. Main paper fulltext in
[00]_...txt; PPT44pages. Bundled Python supports pypdf and pypdfium2 (not fitz).
PDF/SAR skills read; runtime path available from load_workspace_dependencies.
Do not publish third-party fulltext or copied figures in repo (existing NOTICE).

Structural/calibration milestone (uncommitted, 2026-09-20):
- Added cal_weight_reduce/cal_residue_mac/cal_output_stage + per-result ID/flags.
- Added analog_phase_ctrl, sar_trial_ctrl, slice_pool_ctrl, cal_sample_context,
  sar_structural_ctrl. Top default P_STRUCTURAL=1; old P3 vectors explicitly0.
- Physical coefficients now statically wired per slice/unit; narrow switch masks
  route to physical rows. Duplicate physical IDs produce gain_err. No extra latency.
- SAR controls implement clocked binary 9b with3b flash seed and12b backend;
  these radix/backend/time choices are assumptions, not disclosed transistor design.
- Two context slots pair prior RA residue with ADC2; pool promotes actual acquired
  IDs, picks next acquisition from complement. PRNG scan-origin shuffle is chosen
  engineering policy (not uniform subsets). TP/ref/AZ macro pins registered.
- Quantizer dither has physical injection command and opposite RDAC correction;
  sampling dither acquires a separate coefficient mask. Bridge uses sid[8] for
  50-percent activity across complete512-state DEM cycle.
- New tests passed before final small changes: all4096 backend SAR codes/stalls/
  cancel; 2048 physical calibration oracle; structural3modes438outputs across18
  slices with missing-comparator recovery; full1048576-code prior oracle.
- Independent mismatch recovery fixture:2048samples, max calibrated error4output
  counts vs504 with nominal weights (ideal backend quantization, not real INL/DR).
- 5082066 CI failure identified from downloaded artifact: Verilator5.020 requires
  explicit int'(DAC_LEVELS) before subtraction in swap_decode. Fixed; local uses
  newerVerilator. Final CI still required. Additional docs/evidence/publication pending.

Final local verification completed:24 RTL modules,3 strict lint profiles with no
warnings,all15 SV benches pass;572 Python tests pass/3slow deselected(274.78s),
ruff/format97files,mypy48files,Bashsyntax,diffcheck pass. Updated structuralbench
also checks every intermediate resolved-prefix RDAC command,not just finalcode.
Two isolated negative controls compiled and failed the intended runtimeassertion:
physical coefficients aliased toslice0; RDAC fedtrial instead ofresolved bits.
Final sourcehashes and resultmarkers captured in structural_calibration evidence.
ADR0018 and Chinese delivery report authored; remaining work:commit/publish,
updatePR/Issue toexpandedfinalscope,andcheckexactpublishedcommit LinuxCI.

Published e667ed75708335a14f8a904a24acc4c80958b9f2 to PR#3 and updated Issue#2.
Linux run35471134154 rejected unpacked-array tree aliases asUNOPTFLAT under5.020;
this is a strictlyacyclic t->2t/2t+1 tree. Replaced aggregate arrays in
cal_weight_reduce and sadc_enc with independent named generated-node wires,
without disabling warnings or changing latency/arithmetic. Strengthened structural
TB so analog comparator AND flash actually include the physical quantizer dither
command; RDAC must subtract it. This paired test passes. New local full15bench
rerun in progress,55 affectedPython tests pass,2fresh negativecontrols detected.
Pending:refresh evidence,commit/publish portability fix,check finalLinuxCI.
Portability follow-up complete locally:all15benches rerun pass with named nodes
and actual paired dither comparator stimulus;3strict lint profiles pass;
55affectedPython tests and2negativecontrols pass. Evidence refreshed toexactRTL.
Published d9140262a2774a76012012c48f0edc667ec2e061. Linux run35471530594
revealed5.020's1024loop elaboration budget truncated the4095-node tree; this
was NOT another combinational cycle. Runner now passes --unroll-count8192
for lint and simulation, covering8191nodes of32x128coefficient geometry.
Three local lintprofiles and paired structuralbench438outputs pass with the
explicit budget. RTL unchanged from prior all15bench rerun. Publish runnerfix
and check next exactcommitCI; do not stop at the failed older run.
