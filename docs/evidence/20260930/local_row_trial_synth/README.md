# Complete synthesis report subset: local_row_trial_p5_25ns

All original synthesis reports and run/recovery records are retained. `archive_index.json` maps each archived file to its absolute original path, byte count and SHA256. Reports larger than 512 KiB are losslessly stored as deterministic gzip (level 9, mtime 0, empty filename); both uncompressed and compressed hashes are recorded and verified. Use `gzip -dc out/timing_paths.rpt.gz` to inspect the original report bytes. Source packages, status, manifests and other raw files are unchanged.

`readings.json` extracts the report values; it does not replace the original reports. Every source tar member was checked against its source-manifest hash; exact count is recorded in readings.json. The large DCP is not copied, but its complete local bytes and SHA256 were checked against the recorded remote identity and its exact external path is retained. Original recovery descriptions remain historical; the separate local DCP identity records the later completed transfer.

These are post-synthesis OOC results at the recorded divider profile and clock constraint, not placed/routed results or full-top mapped simulation. Clock frequency in the report is the constraint; negative setup or hold slack is not an achieved frequency. No Fmax is asserted. Power is vectorless and not measured. Rebuilt hierarchy totals may include migrated logic and are not isolated RTL source costs. Raw source snapshots, including whether the later MSB mask exists, are authoritative; current repository RTL must not be substituted for the archived source package.

## Same-constraint cached-P5 comparison

`comparison_vs_cached.json` validates identical Vivado/tool/part/P5/25 ns/Tcl/XDC; only `rtl/core/weight_store.sv` changed. LUT grows from 106,205 to 121,772 (+15,567, +14.6575%) while post-synthesis WNS remains +10.640 ns and hold is still negative. This is a valid post-synthesis area regression, not a physical-route or ASIC result. The local-row candidate requires routed timing benefit before any PPA claim.
