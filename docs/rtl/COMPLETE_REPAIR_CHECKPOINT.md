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
