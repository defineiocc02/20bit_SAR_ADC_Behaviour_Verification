# Vivado 2018.3 generated-tree mapping evidence

Final decision: **PASS_WITH_NEGATIVE_CONTROLS** for the repaired small combinational witness. These records are independent of the full-top PPA campaign and do not claim 640 MHz closure, full-top formal equivalence, or ASIC PPA signoff.

| Version | RTL checks | Mapped checks | Inputs with mapped endpoints | LUT |
|---|---:|---:|---:|---:|
| Forward hierarchical references (negative control) | 12,904 PASS | Fails Flash check 1; isolated reducer check 1 also fails | 10 / 969 | 2 |
| Explicit tree nets, column-sharing formula | 12,904 PASS | 12,904 PASS | 969 / 969 | 1,837 |
| Explicit tree nets, original formula | 12,904 PASS | 12,904 PASS | 969 / 969 | 2,834 |

All three wrappers have 0 FF and 0 DSP; LUT counts apply only to this 3-slice combinational witness. The two repaired mapped CSVs are byte-identical (SHA-256 `64accf5fcae0e656766d74231cc6e003ecaa2563d1b0e40eef858ff80a5f60f7`). The original forward-reference RTL passing simulation is retained to demonstrate why RTL simulation alone missed the synthesis defect.

`negative_flash.csv` is the actual first failing mapped row: code3 and code9 are Z while both references are 0. `negative_reducer.csv` is the first failing row with `+reducer_only`: actual total/gain/rails are 0 but all three references are 912 (0x390); Flash comparisons are disabled only for this isolated negative control. Every CSV row was flushed before the failing assertion. These are observed failures, not manufactured plots.

The optimized and original-formula repaired reducers independently pass the unchanged original-reference miter with 1,430,848 comparisons each, across 7 parameter profiles. The production profile covers all 43,758 sets of 8 active slices from 18. Three strict lint runs pass with WIDTH/LATCH/MULTIDRIVEN/UNOPTFLAT/CASEINCOMPLETE/PINMISSING/SELRANGE treated as errors. `review_leaf` passes 91,142 checks. The lint manifest explicitly excludes an older structural ADC result from final integrated freeze claims; final full-top regressions are archived separately.

`sources_sha256.json` identifies the exact synth inputs and final CSV-enabled TB. `mapped_final_manifest.json` is the final decision; the external earlier runner manifest remains FAILED because its initial XSim launcher did not finish. That earlier status has not been edited or repurposed as a functional result. The successful final XSim logs and pass markers are copied here.

`mapped_after_selected.csv` retains actual checks 1–128, 641–768 and 12,904 for a compact review. It is explicitly a subset; the full two 12,904-row CSVs remain in the external artifact directory linked by `evidence_index.json`, with their SHA-256 values. The before/after/original-formula input-fanout CSVs are complete (969 rows each). `figure38_source_hashes.json` links the report figure and plotting script to their complete raw inputs.

The functional witness uses an independent scalar oracle: 128 binary Flash patterns + 512 thermometer inputs + 3,072 valid allocation/switch/dither/mode combinations + 8,192 address cases + 1,000 random full-width cases. See `docs/rtl/VIVADO2018_GENERATED_TREE_AUDIT.md` and the persistent `sim/tb/tree_mapping_*`/`synth/check_tree_mapping.tcl` sources for method and reproduction.
