# Source-bound Dr. RTL analysis evidence

2026-10-07. Source baseline `01ed2b3`, 24 modules and two headers. This snapshot includes current source SHA, source copies, same-source historical functionality identities, current-head Oct2 CI records/raw job logs, real Yosys0.66 generic cone logs and statistics, and freshly exported Vivado2018.3 frozen-P5 routed-DCP reports.

The DCP SHA is unchanged before/after re-export; no synthesis, placement, routing or constraint edits were made. P5/25ns OOC FPGA evidence is not current-default P7 or ASIC signoff. Generic primitive counts/levels are not physical area/slack/Fmax. No RTL candidate was rewritten or promoted.

Original runtime commands/logs retain their execution locations. The full 39MB generic netlists, analyzers, report/plot generators, tool adapters and all page renders are in the local complete reproduction ZIP. Raw snapshots are preserved byte-for-byte; canonical prose under docs/rtl only normalizes whitespace. Tool/source/hash manifests supply provenance, not proof of equivalence.

The native editor compiled the final standalone source successfully; local XeLaTeX export and page QA are recorded separately under report/.

`baseline/ci_tested_merge_tree.json.gz` decompresses to the exact GitHub tree metadata whose original SHA is in `ci_tree_binding.json`. CI checked merge `86499bb`; it differs from audit HEAD only in README, with all production/code/test/flow blobs identical.
