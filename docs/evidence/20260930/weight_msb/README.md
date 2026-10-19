# Weight-store invariant-bit candidate

Only `rtl/core/weight_store.sv` was changed: the accepted write explicitly masks with `W_MAX-1`. Accepted data already satisfies `0<W<2^47`; reset writes zero and all other transactions preserve values. This exposes a post-reset zero-MSB invariant without changing the 48-bit interface, Q30 precision or invalid-write rejection.

Production SHA256: `d4e8e6d6b36ce5f52c9cca6ed761ccba6fa2d4afa3fa5e4822a0d597a1f22085`. Before SHA256: `15c0a33322e6edf5fbc99ec2058ef2b720c356763362c22639d8619ceda5be8a`.

Validation: three production lint profiles PASS; existing review_leaf_tb (91142 checks), review_config_tb (624 outputs), p2_periph_tb (709 checks, zero errors) PASS. Old RTL and new RTL were concurrently compared to an independent per-word state model across production 18x71, boundary32x1 and boundary1x128 geometries: 3023 steps, 1517 accepted writes, 1184 rejected writes, 2411234 word visits checked against both implementations. Cases include exact legal maximum, zero and high-bit rejection, invalid addresses, clear/data retention, clear+write, cfg_ready lockout, complete loading, replacement, randomized traffic and reset priority.

`weight_store.before.sv` is the original source. `weight_store_before.sv` changes only its module name for simultaneous simulation. Source snapshots, exact command arguments, tool version, run logs and hashes are in `verification_manifest.json` and `snapshot_manifest.json`. The testbench is external evidence; production test files were not changed.

No synthesis or implementation was run for this candidate. Do not claim measured removal of FFs/LUTs or timing gains until the subsequent frozen-source Vivado run. The prior P7 synthesis source remains a separate version.
