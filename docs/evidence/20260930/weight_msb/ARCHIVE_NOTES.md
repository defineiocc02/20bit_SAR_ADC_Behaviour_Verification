# Compact weight-store MSB evidence archive

The copied original files, including README.md and both original manifests, retain their original bytes. `archive_manifest.json` maps each copied file to its external source. `sha256.json` hashes every local file except itself.

## Verified scope

The accepted-write mask exposes a high bit already proven zero after reset: accepted weights satisfy `0 < wr_data < 2^47`. Ports, accepted precision, rejected writes, reset, replacement and clear behavior are unchanged. The original and optimized stores were compared to an independent word/bitmap oracle for 3 geometries, 3,023 steps and 2,411,234 word visits (each against both implementations). Existing `review_leaf_tb`, `review_config_tb`, and `p2_periph_tb`, plus three strict lint profiles, passed. Counts and actual commands are in the original verification manifest and logs.

The original 85-file source snapshot, build logs, build objects, executables and tool installation remain outside Git. `snapshot_manifest.json` identifies that full external snapshot; it does not imply all its files are included here. `verification_manifest.json` retains the hash of an omitted build log for provenance. The header is copied to this directory to make the focused old/new/oracle test self-contained.

## Focused replay

From the repository root, with an installed compatible Verilator:

```sh
evidence=docs/evidence/20260930/weight_msb
verilator --binary --timing --assert --unroll-count 8192 -Wno-fatal \
  -CFLAGS -std=c++20 -j 2 --top-module weight_msb_equiv_tb \
  --Mdir /tmp/sar_weight_msb_replay -I"$evidence" \
  "$evidence/weight_store.after.sv" "$evidence/weight_store_before.sv" \
  "$evidence/weight_msb_equiv_tb.sv"
/tmp/sar_weight_msb_replay/Vweight_msb_equiv_tb
```

The recorded local Verilator identifies itself as `rev vUNKNOWN-built20260516-4e853d8` in the archived `version.log` (not an official version-tag proof). Its macOS toolchain also needed `-MAKEFLAGS CFG_CXXFLAGS_PCH_I=-include`; exact arguments and environment are preserved in `verification_manifest.json`. No replay was performed as part of archiving.

## Limits and snapshot boundary

This archive establishes tested functional equivalence for this change; it is not a complete-chip proof, STA, routed implementation or analog verification. No synthesis was run for this candidate when these logs were made, so area or timing savings are unmeasured here. The earlier P7 source freeze does not contain this mask. A later P5 result containing it changes both profile and source and cannot isolate a stage-count-only PPA effect.
