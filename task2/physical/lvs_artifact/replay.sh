#!/usr/bin/env bash
# Recheck a single streamed GDS using two immutable public SKY130HD decks.
set -euo pipefail
if [ "$#" != 2 ]; then echo 'Usage: replay.sh PHYSICAL_ARTIFACT NEW_OUTPUT_DIR' >&2; exit 2; fi
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
input="$(cd "$1" && pwd)"
mkdir "$2"
out="$(cd "$2" && pwd)"
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac
revision=5eebdc0d06879cf674e86e3fb8efa2803e6e44dc

frozen="$(python3 - "$input" "$out" <<'PY'
import hashlib
import json
import os
import re
import sys
from pathlib import Path

root, out = map(Path, sys.argv[1:])
top = os.environ['LVS_TOP']
run = os.environ['LVS_SOURCE_RUN']
commit = os.environ['LVS_EXPECTED_COMMIT']
expected_gds = os.environ['LVS_EXPECTED_GDS']
if top not in ('sar20_digital_lms_top', 'sar20_digital_top', 'dither8_top'):
    raise ValueError('Unknown LVS top')
if not (run.isdecimal() and re.fullmatch(r'[a-f0-9]{40}', commit)
        and re.fullmatch(r'[a-f0-9]{64}', expected_gds)):
    raise ValueError('Incomplete expected artifact identity')
matches = list(root.rglob('release_manifest.json'))
if len(matches) != 1:
    raise ValueError('Expected exactly one physical release manifest')
frozen = matches[0].parent
manifest = json.loads(matches[0].read_text())
if (manifest['top'] != top or manifest['rtl_commit'] != commit
        or manifest['run_id'].split('-', 1)[0] != run
        or manifest['stream']['pv_input_sha256'] != expected_gds):
    raise ValueError('Unexpected physical run, top, commit or GDS')
verified = {}
for suffix in ('gds', 'odb', 'sdc', 'v', 'spef'):
    name = f'{top}.{suffix}'
    target = frozen / name
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    receipt = manifest['artifacts'][name]
    if target.stat().st_size != receipt['bytes'] or digest.hexdigest() != receipt['sha256']:
        raise ValueError(f'Invalid artifact {name}')
    verified[name] = digest.hexdigest()
if verified[f'{top}.gds'] != expected_gds:
    raise ValueError('Streamed GDS hash changed')
(out / 'frozen_inputs.json').write_text(json.dumps({
    'top': top, 'source_run': manifest['run_id'], 'rtl_commit': commit, 'sha256': verified,
    'method': 'Both decks use the same CDL exported from the frozen routed ODB.',
}, indent=2) + '\n')
print(frozen)
PY
)"

curl --fail --silent --show-error --retry 3 --max-time 45 \
  "https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/$revision/flow/platforms/sky130hd/lvs/sky130hd.lylvs" \
  -o "$out/upstream.lylvs"
(cd "$out" && printf '%s\n' 'd2d231d7cd00a8261d6653c5f3757badefb1dc0aba29441d8406272e75cd8b90  upstream.lylvs' | sha256sum -c)
for attempt in 1 2 3; do
  if docker pull "$image"; then break; fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done
printf '%s\n' "$image" > "$out/container.txt"

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen -e LVS_TOP \
  -v "$repo:/work/repo:ro" -v "$frozen:/work/input:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$image" -ec '
    set -o pipefail
    if [ -f ../env.sh ]; then source ../env.sh; fi
    platform="$PWD/platforms/sky130hd"
    export LVS_LIBERTY="$platform/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    { openroad -version; klayout -v; } > /work/out/tools.txt
    printf "%s\n" "1afade11dd24ea4e64d1ffba54fc17a8f110286712ab942928d2f5e021bb3caf  $platform/lvs/sky130hd.lylvs" | sha256sum -c
    python3 /work/repo/task2/physical/prepare_lvs_cdl.py "$platform/cdl/sky130hd.cdl" \
      /work/out/compatible.cdl /work/out/lvs_cdl_compat.json
    openroad -no_init -exit /work/repo/task2/physical/lvs_artifact/export_cdl.tcl \
      > /work/out/export_cdl.log 2>&1
    cat /work/out/top.cdl /work/out/compatible.cdl > /work/out/reference.cdl
    sha256sum /work/out/reference.cdl > /work/out/reference.sha256
    for mode in legacy upstream; do
      dir="/work/out/$mode"
      mkdir "$dir"
      layout="$dir/$LVS_TOP.gds"
      cp "/work/input/$LVS_TOP.gds" "$layout"
      cmp "/work/input/$LVS_TOP.gds" "$layout"
      report=()
      if [ "$mode" = legacy ]; then
        deck="$platform/lvs/sky130hd.lylvs"
      else
        deck=/work/out/upstream.lylvs
        report=(-rd "report_file=$dir/lvs_report.lvsdb")
      fi
      sha256sum "$deck" > "$dir/deck.sha256"
      if (cd "$dir" && timeout 600 klayout -b -rd "in_gds=$layout" \
          -rd cdl_file=/work/out/reference.cdl -rd "top_cell=$LVS_TOP" \
          -rd target_netlist="$dir/extracted.cir" "${report[@]}" -r "$deck") > "$dir/lvs.log" 2>&1; then
        printf "0\n" > "$dir/exit.txt"
      else
        printf "%s\n" "$?" > "$dir/exit.txt"
      fi
      cmp "/work/input/$LVS_TOP.gds" "$layout"
      sha256sum "$layout" > "$dir/gds_checked.sha256"
      rm "$layout"
    done
    sha256sum -c /work/out/reference.sha256
    sha256sum "/work/input/$LVS_TOP.gds" > /work/out/gds_after.sha256
  ' 2>&1 | tee "$out/replay.log"

python3 - "$out" "$revision" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

out = Path(sys.argv[1]); source = json.loads((out / 'frozen_inputs.json').read_text())
gds_sha = source['sha256'][source['top'] + '.gds']
if (out / 'gds_after.sha256').read_text().split()[0] != gds_sha:
    raise ValueError('GDS changed during replay')
variants = {}
for mode in ('legacy', 'upstream'):
    folder = out / mode
    if (folder / 'gds_checked.sha256').read_text().split()[0] != gds_sha:
        raise ValueError(f'GDS for {mode} changed')
    raw = (folder / 'lvs.log').read_bytes(); text = raw.decode(errors='replace')
    code = int((folder / 'exit.txt').read_text())
    db = folder / 'lvs_report.lvsdb'
    db_sha = None
    if db.is_file():
        digest = hashlib.sha256()
        with db.open('rb') as stream:
            if not stream.readline().startswith(b'#%lvsdb-klayout'):
                raise ValueError(f'Invalid LVS database for {mode}')
            stream.seek(0)
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        db_sha = digest.hexdigest()
    status = 'BLOCKED'
    if re.search(r"Netlists (?:don't|do not) match", text, re.I):
        status = 'FAIL'
    elif code == 0 and re.search(r'Netlists match', text, re.I):
        status = 'PASS' if db_sha else 'PARTIAL'
    variants[mode] = {
        'status': status, 'exit_code': code, 'deck_sha256': (folder / 'deck.sha256').read_text().split()[0],
        'log_sha256': hashlib.sha256(raw).hexdigest(), 'database_sha256': db_sha,
    }
summary = {'source': source, 'upstream_revision': sys.argv[2], 'variants': variants,
           'reference_cdl_sha256': (out / 'reference.sha256').read_text().split()[0]}
(out / 'replay_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(variants, indent=2))
PY
