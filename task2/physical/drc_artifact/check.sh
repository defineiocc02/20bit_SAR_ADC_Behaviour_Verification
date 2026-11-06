#!/usr/bin/env bash
# Re-run the same SKY130HD DRC deck without rebuilding the GDS.
set -euo pipefail
if [ "$#" != 2 ]; then echo 'Usage: check.sh PHYSICAL_ARTIFACT NEW_OUTPUT_DIR' >&2; exit 2; fi
input="$(cd "$1" && pwd)"
mkdir "$2"
out="$(cd "$2" && pwd)"
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac

frozen="$(python3 - "$input" "$out" "$image" <<'PY'
import hashlib
import json
import os
import re
import sys
from pathlib import Path

root, out = map(Path, sys.argv[1:3])
top = 'sar20_digital_lms_top'
run = os.environ['DRC_SOURCE_RUN']
commit = os.environ['DRC_EXPECTED_COMMIT']
gds_sha = os.environ['DRC_EXPECTED_GDS']
if not (run.isdecimal() and re.fullmatch(r'[a-f0-9]{40}', commit)
        and re.fullmatch(r'[a-f0-9]{64}', gds_sha)):
    raise ValueError('Incomplete expected GDS identity')
matches = list(root.rglob('release_manifest.json'))
if len(matches) != 1:
    raise ValueError('Expected exactly one physical release manifest')
frozen = matches[0].parent
receipt = json.loads(matches[0].read_text())
if (receipt['top'] != top or receipt['rtl_commit'] != commit
        or receipt['run_id'].split('-', 1)[0] != run
        or receipt['stream']['pv_input_sha256'] != gds_sha
        or (frozen / 'container.txt').read_text().strip() != sys.argv[3]):
    raise ValueError('Unexpected physical run, source, GDS or image')
name = f'{top}.gds'
path = frozen / name
digest = hashlib.sha256()
with path.open('rb') as stream:
    for block in iter(lambda: stream.read(1 << 20), b''):
        digest.update(block)
if digest.hexdigest() != gds_sha or path.stat().st_size != receipt['artifacts'][name]['bytes']:
    raise ValueError('Frozen GDS checksum or size changed')
environment = json.loads((frozen / 'environment.json').read_text())
deck_sha = environment['technology_files']['drc/sky130hd.lydrc']
if not re.fullmatch(r'[a-f0-9]{64}', deck_sha or ''):
    raise ValueError('Original DRC deck SHA-256 missing')
(out / 'drc_input.json').write_text(json.dumps({
    'top': top, 'source_run': receipt['run_id'], 'rtl_commit': commit,
    'gds_sha256': gds_sha, 'deck_sha256': deck_sha, 'container': sys.argv[3],
    'timeout_seconds': 2100,
}, indent=2) + '\n')
print(frozen)
PY
)"

for attempt in 1 2 3; do
  if docker pull "$image"; then break; fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \
  -v "$frozen:/work/input:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$image" -ec '
    set -o pipefail
    if [ -f ../env.sh ]; then source ../env.sh; fi
    deck="$PWD/platforms/sky130hd/drc/sky130hd.lydrc"
    expected_sha=$(python3 -c "import json; print(json.load(open(\"/work/out/drc_input.json\"))[\"deck_sha256\"])" )
    printf "%s  %s\n" "$expected_sha" "$deck" | sha256sum -c
    gds=/work/input/sar20_digital_lms_top.gds
    klayout -v > /work/out/tools.txt
    if timeout -k 30 2100 ./scripts/klayout.sh -zz \
      -rd "in_gds=$gds" -rd report_file=/work/out/drc.lyrdb -r "$deck" \
      > /work/out/drc.log 2>&1; then
      printf "0\n" > /work/out/exit.txt
    else
      printf "%s\n" "$?" > /work/out/exit.txt
    fi
    sha256sum "$gds" > /work/out/gds_after.sha256
  ' 2>&1 | tee "$out/replay.log"

python3 - "$out" <<'PY'
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(sys.argv[1])
source = json.loads((root / 'drc_input.json').read_text())
if (root / 'gds_after.sha256').read_text().split()[0] != source['gds_sha256']:
    raise ValueError('GDS changed during DRC')
exit_code = int((root / 'exit.txt').read_text())
report = root / 'drc.lyrdb'
result = {'source': source, 'exit_code': exit_code, 'status': 'BLOCKED', 'marker_count': None,
          'log_sha256': hashlib.sha256((root / 'drc.log').read_bytes()).hexdigest()}
if report.is_file():
    result['report_sha256'] = hashlib.sha256(report.read_bytes()).hexdigest()
if exit_code == 0 and report.is_file():
    items = ET.parse(report).getroot().find('items')
    if items is None:
        raise ValueError('Missing DRC markers section')
    count = len(items.findall('item'))
    result.update(status='PASS' if count == 0 else 'FAIL', marker_count=count)
elif exit_code in (124, 137):
    result['reason'] = 'Timeout; any partial marker file is excluded from the verdict.'
else:
    result['reason'] = 'Tool did not complete or did not produce a marker database.'
(root / 'drc_summary.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
PY
