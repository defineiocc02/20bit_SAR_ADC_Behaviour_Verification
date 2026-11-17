# 20-bit / 40 MS/s SAR ADC — Digital IP

[中文主文档 (README.md)](README.md) · [IP interface contract](docs/D3D4_IP_INTERFACE.md) · [Status (authoritative)](docs/STATUS.md) · [Acceptance](docs/ACCEPTANCE_STATUS.md)

> **Note**: the authoritative documentation is in Chinese. This page is a short
> English pointer only — it deliberately does **not** duplicate the content, to
> avoid a second copy that drifts out of date.

---

## What this repository delivers

`rtl/_legacy/tops/d3d4_ip.sv` — a synthesizable, single-clock-domain digital IP that turns an
RDAC code `k` (0..520) plus a 6-bit dither word into:

| Output | Meaning |
|:---|:---|
| `mask_o[159:0]` | 8 slices × 20 units physical switch mask (`bit = 20*s + j`) |
| `pre_o[31:0]` | 8 slices × 4 bits sampling preset (`bit = 4*s + b`) |
| `acq/conv/idle_mask_o` | 18-slice pool partitioning (8 acquire / 8 convert / 2 spare) |
| `acq_ids_o`, `conv_ids_o` | physical slice IDs |
| `out_valid_o`, `out_k_err_o` | validity/error **aligned with `mask_o`** |

It does **not** output a 20-bit code — weight reconstruction (D5) and top-level
transaction control (D6) live in the legacy chain `rtl/_legacy/tops/digital_top_w.sv`.

## Verification (one command)

```bash
D3D4_RUNNER=<ssh-host> bash tools/verify_d3d4_ip.sh
```

Four layers, each must print `RESULT: PASS`:

| Layer | Scope | Coverage |
|:---|:---|:---|
| L1 | leaf modules vs Python reference | 133,376 DEM + 512 Dither, exhaustive |
| L2 | `_legacy/tops/d3d4_ip` vs reference | 512 transactions |
| L2.5 | `sched18` randomness | 20,000 cycles, χ²/IDLE coverage |
| L2.6 | per-event alignment | k / 160-bit mask / flags from the **same** event |

## Baseline

| Item | Value |
|:---|:---|
| Area | 1770.08 µm² (3017 cells, 322 FF) — TSMC 28HPC+ SS |
| Critical path | 2.82 ns (natural implementation 2.788 ns) |
| Setup @25 ns | +19.88 ns |
| Hold @25 ns | +0.00095 ns — **not a signoff number** (zero wire load) |

See [docs/SYNTH_D3D4.md](docs/SYNTH_D3D4.md) for the full caveats.

## What is *not* claimed

- No signoff: no P&R, no CTS, no DRC/LVS, no power signoff
- No formal equivalence (gate-level sim only)
- Legacy chain (`_legacy/tops/digital_top_w`) uses the older 4+3+2 mapping and `k = c − U`;
  its 8509-transaction regression **does not** prove the new mapping
- The follower still uses 9 events and `prefix − U`, while contract v2.1
  requires 7 events (Flash + 6 SAR) and `k = c + U` — see
  [docs/STATUS.md](docs/STATUS.md) §1B

## Repository layout

```
rtl/                 d3d4_ip.sv + D1–D4 leaf modules + legacy top
tb/                  the three main testbenches + golden vectors
tools/verify_d3d4_ip.sh   the single verification entry point
flow/build/filelists/     4 compile filelists (read the README first)
flow/synth/d3d4/          synthesis scripts and SDC
docs/                14 current docs; the rest live in docs/_history/
```
