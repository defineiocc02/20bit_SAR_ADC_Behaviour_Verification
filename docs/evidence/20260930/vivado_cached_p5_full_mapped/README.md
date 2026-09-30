# Cached P5 complete full-top mapped functional witness

The three directories preserve two failed flow attempts and the final actual Vivado 2018.3/XSim pass without overwriting any raw manifest. The first attempt rejected Vivado’s checkpoint_post_synth design name; the second exposed vendor-split multidimensional ports at xelab. The final run used an explicit vendor-port testbench branch. All three bind the same 49,687,597-byte synthesis DCP by SHA-256.

The final XSim trace contains 438 exact code/flag comparisons in three modes, 3,900 configuration readbacks, 7,680 protocol checks, and three cancellation cases. The local independent audit checks trace/log agreement, IDs and completeness. This is a functional post-synthesis netlist without SDF; it does not establish setup/hold closure, analog performance or ASIC PPA.

`sha256.json` lists every preserved payload byte stream. Large synthesis DCP remains in the local campaign at the exact path bound by each manifest and in the separate synthesis archive by checksum provenance.
