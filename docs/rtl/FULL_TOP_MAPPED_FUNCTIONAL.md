# Full-top post-synthesis functional witness

This flow exports the **existing full production `sar20_digital_core` post-synthesis DCP**, then runs the public-port `structural_mapped_tb` against the resulting functional netlist with XSim. It does not synthesize RTL, implement/place/route a design, annotate SDF, or prove a clock frequency. Keep the existing synthesis and timing conclusions separate.

The same Python helper runs locally on an EDA machine or inside a unique directory reached through the existing SSH/PowerShell workflow. The helper has no SSH transport and never starts EDA unless `--execute` is specified. Run P5 and P7 serially, with distinct directories. Python 3.9 or newer is sufficient. An accepted synthesis manifest, its corresponding checkpoint, and the expected DCP SHA-256 must already be available on that host.

## Provenance gate

`run_vivado_full_mapped.py` requires `arguments.stages` and `vivado_status.P_RECON_STAGES` in the synthesis manifest to equal the requested TB label (5 or 7), and both part fields to agree. `FAILED`, undriven-diagnostic, incomplete or inconsistent synthesis records are rejected. A negative synthesis WNS is permitted for this **functional** experiment and remains recorded as negative. Nothing promotes it to timing closure.

If the synthesis manifest contains `remote_dcp_identity.sha256`, that identity is mandatory. Legacy manifests without a DCP hash require an explicit `--dcp-sha256` obtained from the corresponding, independently checked synthesis output. This is recorded as caller-supplied identity; it must not be guessed from a filename. If both identities are supplied they must agree. The helper snapshots and hashes the original manifest, DCP, TB and export Tcl. It never modifies the original synthesis manifest, including any failed historical record. Flattened DCPs do not retain a usable top-level HDL generic: the profile binding comes from this synthesis provenance and content hash, not an invented DCP parameter query.

The export Tcl rechecks the actual checkpoint top name, FPGA part, required public port presence, and absence of black boxes before `write_verilog -mode funcsim`. It writes an export-complete record, **not a simulation PASS**. The helper then uses `xvlog`, `xelab`, `xsim` and `data/verilog/src/glbl.v` from the same specified Vivado installation, with `unisims_ver`. The simulation top receives `-generic_top P_RECON_STAGES=5` or `7`; the synthesized DUT receives no parameter override. Reset stays low until 220 ns, after glbl's initial 100 ns GSR.

## Run on the Windows EDA host

Deploy these files under their repository-relative paths in a new directory: `synth/run_vivado_full_mapped.py`, `synth/run_vivado_full_mapped.tcl`, `synth/audit_full_mapped.py`, and `sim/tb/structural_mapped_tb.sv`. The checkpoint and its synthesis manifest can be elsewhere on the same host. Use literal verified paths/hashes; the following PowerShell template leaves the input variables explicit:

```powershell
$Vivado = 'D:\Academic\Vivado2018\Vivado\2018.3\bin\vivado.bat'
$Dcp = 'C:\path\to\verified\out\post_synth.dcp'
$SynthManifest = 'C:\path\to\matching\manifest.json'
$DcpSha256 = '<previously verified 64-hex checkpoint digest>'
python synth/run_vivado_full_mapped.py --dcp $Dcp --synthesis-manifest $SynthManifest --dcp-sha256 $DcpSha256 --stages 7 --vivado $Vivado --out full_mapped_p7 --timeout 3600 --execute
```

Omit `--execute` to prepare/capture inputs without starting tools. Prepared directories are not resumed or reused: select a new output directory for a subsequent execution. For P5 use its matching manifest/DCP/hash, `--stages 5`, and a distinct output directory. On Linux use the installation's absolute `bin/vivado` path; the other command-line options are identical. The helper itself does not submit SSH or start any additional remote task. The existing encoded PowerShell launch method can invoke this command when working through SSH.

Every Windows tool call is in a unique local `.cmd` file with delayed expansion disabled, literal quoted arguments and the raw vendor return code retained. Percent, quote and control-character arguments are rejected. Tool output is captured as bytes so localized diagnostics cannot break decoding. Per-step timeouts terminate only the process tree started by the helper. A raw return code of zero alone never establishes success: export identity, simulator diagnostics, the complete marker, and the CSV audit must all agree. A new output directory prevents reuse of stale snapshots or success markers.

## Acceptance and independent audit

The required completion marker contains 438 output comparisons, 3,900 configuration readbacks, 7,680 public protocol observations, 18 physical slices, and exactly three epoch cancellations. Each mode must deliver the independently computed 146-ID validity schedule in order; each cancellation is ID 159 at frame 159. The CSV must match every logged `MAPPED_ROW`, contain only finite decimal integer fields, and show equality of observed/expected code and flags. Missing or duplicate markers, unknown values, missing rows, duplicate/reordered IDs, mismatched stage tags, simulator fatal/errors, and failed subprocesses are rejected. Input snapshots are rehashed after the run.

The standalone auditor can recheck the CSV and simulator log without running EDA:

```powershell
python synth/audit_full_mapped.py --trace full_mapped_p7/trace.csv --log full_mapped_p7/xsim.log --stages 7 --out full_mapped_p7/independent_audit.json
```

This auditor checks trace/log consistency and independently derives valid sample IDs. It does not create a second code oracle or establish DCP identity; the 256-bit TB formula is the arithmetic oracle, while the run manifest binds the DCP. It deliberately does not assert internal reconstruction-start latency. This fixture's result flags are all zero, so nonzero overflow/clipping propagation is not claimed as covered here.

The directory retains `manifest.json`, immutable `input/`, `export/full_top_funcsim.v`, exported status, each tool's raw return code and logs, `trace.csv`, and SHA-256 hashes. `FULL_MAPPED_FUNCTIONAL_PASS` is written only after the real tool chain and audit pass. Unit tests of this flow use mocks and Tcl command stubs; their PASS does not establish Vivado or mapped functionality. At preparation time the auditor was additionally checked against the existing real local P5/P7 RTL traces; those remain RTL qualification, not netlist results.

## Command references

The functional-netlist/UNISIM method follows [AMD UG900, Generating a Functional Netlist](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Generating-a-Functional-Netlist). The `-generic_top` syntax and standalone `xvlog`/`xelab` flow are also documented in [UG900 v2015.4, pp. 120–126](https://docs.amd.com/api/khub/documents/9RBWS8fF1CFGgLp0JNlfVQ/content), predating the target installation. The repository campaign already used the same Vivado 2018.3 `write_verilog -mode funcsim`, `glbl`, `unisims_ver`, and `xsim -R -testplusarg` commands for its smaller mapped witness. The new full-top flow still requires its own real execution; static/mocked checks do not substitute for it.
