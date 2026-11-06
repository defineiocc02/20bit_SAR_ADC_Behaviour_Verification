#!/usr/bin/env bash
# Compare two public rule decks against one immutable routed checkpoint.
set -euo pipefail
if [ "$#" != 2 ]; then
  echo 'Usage: replay.sh FROZEN_ARTIFACT NEW_OUTPUT_DIR' >&2
  exit 2
fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
input="$(cd "$1" && pwd)"
mkdir "$2"
out="$(cd "$2" && pwd)"
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac
deck_revision=5eebdc0d06879cf674e86e3fb8efa2803e6e44dc

python3 - "$input" "$out" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root, out = map(Path, sys.argv[1:])
manifest = json.loads((root / 'release_manifest.json').read_text())
if (manifest['top'], manifest['rtl_commit'], manifest['run_id']) != (
    'dither8_top', '62e3e3a4f4208efe2252837020640077f337350b', '42265153017-1-dither8_top'
):
    raise ValueError('Expected the frozen optimized dither8 artifact')
checks = {}
for suffix in ('gds', 'odb', 'sdc', 'v', 'spef'):
    name = f'dither8_top.{suffix}'
    blob = (root / name).read_bytes()
    expected = manifest['artifacts'][name]
    if len(blob) != expected['bytes'] or hashlib.sha256(blob).hexdigest() != expected['sha256']:
        raise ValueError(f'Input hash mismatch: {name}')
    checks[name] = expected['sha256']
if checks['dither8_top.gds'] != '6b82f762bde5ac9fda90137904ef0c95a78daab6f7acbc497e0bed6942128e74':
    raise ValueError('Unexpected GDS')
(out / 'frozen_inputs.json').write_text(json.dumps({
    'source_run': manifest['run_id'], 'rtl_commit': manifest['rtl_commit'], 'sha256': checks,
    'method': 'One CDL export from the immutable routed ODB for both public decks.',
}, indent=2) + '\n')
PY

curl --fail --silent --show-error --retry 3 --max-time 45 \
  "https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/$deck_revision/flow/platforms/sky130hd/lvs/sky130hd.lylvs" \
  -o "$out/upstream.lylvs"
(cd "$out" && printf '%s\n' 'd2d231d7cd00a8261d6653c5f3757badefb1dc0aba29441d8406272e75cd8b90  upstream.lylvs' | sha256sum -c)
for attempt in 1 2 3; do
  if docker pull "$image"; then break; fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \
  -v "$repo_dir:/work/repo:ro" -v "$input:/work/input:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$image" -ec '
    set -o pipefail
    if [ -f ../env.sh ]; then source ../env.sh; fi
    platform="$PWD/platforms/sky130hd"
    export LVS_LIBERTY="$platform/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    {
      openroad -version
      klayout -v
    } > /work/out/tools.txt
    printf "%s\n" "1afade11dd24ea4e64d1ffba54fc17a8f110286712ab942928d2f5e021bb3caf  $platform/lvs/sky130hd.lylvs" | sha256sum -c
    python3 /work/repo/task2/physical/prepare_lvs_cdl.py "$platform/cdl/sky130hd.cdl" \
      /work/out/compatible.cdl /work/out/lvs_cdl_compat.json
    openroad -no_init -exit /work/repo/task2/physical/lvs_replay/export_cdl.tcl \
      > /work/out/export_cdl.log 2>&1
    cat /work/out/dither8_top.cdl /work/out/compatible.cdl > /work/out/reference.cdl
    sha256sum /work/out/reference.cdl > /work/out/reference.sha256
    for mode in legacy upstream; do
      dir="/work/out/$mode"
      mkdir "$dir"
      # KLayout resolves a relative LVSDB name next to the layout input.
      # Keep the frozen input read-only and give each deck an identical writable copy.
      layout="$dir/dither8_top.gds"
      cp /work/input/dither8_top.gds "$layout"
      cmp /work/input/dither8_top.gds "$layout"
      report=()
      if [ "$mode" = legacy ]; then
        deck="$platform/lvs/sky130hd.lylvs"
      else
        deck=/work/out/upstream.lylvs
        report=(-rd "report_file=$dir/lvs_report.lvsdb")
      fi
      sha256sum "$deck" > "$dir/deck.sha256"
      # The legacy deck needs the default branch to select report_lvs().
      if (cd "$dir" && timeout 300 klayout -b \
        -rd "in_gds=$layout" \
        -rd cdl_file=/work/out/reference.cdl -rd top_cell=dither8_top \
        -rd target_netlist="$dir/extracted.cir" "${report[@]}" -r "$deck") > "$dir/lvs.log" 2>&1; then
        printf "0\n" > "$dir/exit.txt"
      else
        printf "%s\n" "$?" > "$dir/exit.txt"
      fi
      cmp /work/input/dither8_top.gds "$layout"
      sha256sum "$layout" > "$dir/gds_checked.sha256"
      rm "$layout"
    done
    sha256sum -c /work/out/reference.sha256
    sha256sum /work/input/dither8_top.gds > /work/out/gds_after.sha256
  ' 2>&1 | tee "$out/replay.log"

python3 - "$out" "$deck_revision" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
identity = json.loads((root / 'frozen_inputs.json').read_text())
if (root / 'gds_after.sha256').read_text().split()[0] != identity['sha256']['dither8_top.gds']:
    raise ValueError('GDS changed during LVS replay')
variants = {}
for mode in ('legacy', 'upstream'):
    folder = root / mode
    raw = (folder / 'lvs.log').read_bytes()
    text = raw.decode(errors='replace')
    code = int((folder / 'exit.txt').read_text())
    verdict = 'BLOCKED'
    if re.search(r"Netlists (?:don't|do not) match", text, re.I):
        verdict = 'FAIL'
    elif code == 0 and re.search(r'Netlists match', text, re.I):
        verdict = 'PASS' if (folder / 'lvs_report.lvsdb').is_file() else 'PARTIAL'
    variants[mode] = {
        'status': verdict, 'exit_code': code,
        'deck_sha256': (folder / 'deck.sha256').read_text().split()[0],
        'log_sha256': hashlib.sha256(raw).hexdigest(),
        'database': 'lvs_report.lvsdb' if (folder / 'lvs_report.lvsdb').is_file() else None,
    }
report = {
    'source': identity, 'upstream_revision': sys.argv[2],
    'reference_cdl_sha256': (root / 'reference.sha256').read_text().split()[0],
    'variants': variants,
}
(root / 'replay_summary.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(variants, indent=2))
PY
