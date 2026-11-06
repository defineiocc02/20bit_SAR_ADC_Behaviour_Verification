#!/usr/bin/env bash
# Execute the pinned public SKY130HD flow in an isolated Linux container.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: bash task1/physical/sky130_run.sh NEW_OUTPUT_DIRECTORY [TOP]" >&2
  exit 2
fi
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(dirname "$task_dir")"
top="${2:-module1_top}"
case "$top" in
  module1_top)
    flow_config=sky130_config.mk
    flow_nickname=task1_module1
    ;;
  module1_bridge_top)
    flow_config=sky130_bridge_config.mk
    flow_nickname=task1_module1_bridge
    ;;
  *)
    echo "Unsupported SKY130HD top: $top" >&2
    exit 2
    ;;
esac
image="${TASK1_ORFS_IMAGE:-openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac}"
command -v docker >/dev/null
mkdir -p "$1"
out="$(cd "$1" && pwd)"
if [ -n "$(ls -A "$out")" ]; then
  echo "Output directory must be empty: $out" >&2
  exit 2
fi
mkdir "$out/delivery"

for attempt in 1 2 3; do
  if docker pull "$image"; then
    break
  fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done
digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")"
source_sha="$(git -C "$repo_dir" rev-parse HEAD)"
printf '%s\n' "$digest" > "$out/delivery/container.txt"
printf '%s\n' "$source_sha" > "$out/delivery/rtl_commit.txt"

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \
  -e "TASK1_FLOW_CONFIG=$flow_config" -e "TASK1_FLOW_NICKNAME=$flow_nickname" \
  -v "$task_dir:/work/task1:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$digest" -ec '
    set -o pipefail
    if [ -f ../env.sh ]; then source ../env.sh; fi
    {
      uname -sm
      yosys -V
      openroad -version
      klayout -v
    } > /work/out/delivery/tools.txt
    export TASK1_FINAL_DIR=/work/out/flow/results/sky130hd/$TASK1_FLOW_NICKNAME/base
    export TASK1_PROXY_LIBERTY="$PWD/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
    test -s "$TASK1_PROXY_LIBERTY"
    make DESIGN_CONFIG="/work/task1/physical/$TASK1_FLOW_CONFIG" WORK_HOME=/work/out/flow LEC_CHECK=0 finish
    openroad -no_init -exit /work/task1/physical/post_route.tcl \
      | tee /work/out/delivery/post_route_sta.rpt
    cp /work/task1/physical/sky130_license.txt /work/out/delivery/SKY130HD-LICENSE.txt
    cd "$(dirname "$TASK1_PROXY_LIBERTY")"
    sha256sum "$(basename "$TASK1_PROXY_LIBERTY")" > /work/out/delivery/liberty.sha256
  ' 2>&1 | tee "$out/flow.log"

python3 "$task_dir/physical/sky130_package.py" "$out" "$task_dir" --top "$top"
