#!/usr/bin/env bash
# Run the same mapped synthesis and pre-route STA without starting P&R.
set -euo pipefail
if [ "$#" != 1 ]; then echo 'Usage: run.sh NEW_OUTPUT_DIR' >&2; exit 2; fi
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mkdir "$1"
out="$(cd "$1" && pwd)"
mkdir "$out/delivery"
commit="$(git -C "$repo" rev-parse HEAD)"
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac
config=task2/physical/sky130_lms_config.mk
run_id="${TASK2_PHYSICAL_RUN_ID:-sar20-lms-mapped-$(date -u +%Y%m%dT%H%M%SZ)}"
python3 "$repo/task2/physical/package_run.py" freeze \
  "$out" "$repo" sar20_digital_lms_top task_integration_sar20_lms "$config" "$commit" "$run_id"
for attempt in 1 2 3; do
  if docker pull "$image"; then break; fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done
printf '%s\n' "$image" > "$out/delivery/container.txt"
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen -e "TASK2_CONFIG=$config" \
  -v "$repo:/work/repo:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$image" -ec '
    set -o pipefail
    if [ -f ../env.sh ]; then source ../env.sh; fi
    platform="$PWD/platforms/sky130hd"
    export TASK1_PROXY_LIBERTY="$platform/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    export TASK1_FINAL_DIR=/work/out/flow/results/sky130hd/task_integration_sar20_lms/base
    python3 /work/repo/task2/physical/record_environment.py "$platform" /work/out/delivery/environment.json
    make "DESIGN_CONFIG=/work/repo/$TASK2_CONFIG" WORK_HOME=/work/out/flow LEC_CHECK=0 synth
    openroad -no_init -exit /work/repo/task2/physical/pre_route.tcl \
      | tee /work/out/delivery/synth_sta.rpt
    grep -Fq TASK2_SYNTH_STA_COMPLETE /work/out/delivery/synth_sta.rpt
  ' 2>&1 | tee "$out/flow.log"

python3 - "$out/delivery" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
plan = json.loads((root / 'plan.json').read_text())
text = (root / 'synth_sta.rpt').read_text()
timing = {}
for mode, sense in (('setup','max'), ('hold','min')):
    match = re.search(r'^worst slack ' + sense + r' ([+-]?[\d.]+)$', text, re.M)
    if match is None:
        raise ValueError(f'Missing {mode} slack')
    tns = re.search(r'^tns ' + sense + r' ([+-]?[\d.]+)$', text, re.M)
    path = re.findall(r'^(?:Startpoint|Endpoint): (.+)$', text[match.end():], re.M)[:2]
    timing[mode] = {'worst_slack_ns': float(match[1]), 'tns_ns': float(tns[1]), 'first_path': path}
evidence = {
    'run_id': plan['run_id'], 'rtl_commit': plan['rtl_commit'], 'clock_ns': plan['clock_period_ns'],
    'scope': 'Mapped cells with ideal clocks and no routed RC. Diagnostic, final routed STA NOT_RUN.',
    'timing': timing,
    'artifact_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in ('synth_sta.rpt','mapped_netlist.v','plan.json','inputs.json','environment.json')},
}
(root / 'mapped_sta_summary.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(timing, indent=2))
PY
