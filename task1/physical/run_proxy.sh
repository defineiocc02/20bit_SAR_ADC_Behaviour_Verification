#!/usr/bin/env bash
# Run the public reference flow in Docker without modifying the host toolchain.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: bash task1/physical/run_proxy.sh NEW_OUTPUT_DIRECTORY" >&2
  exit 2
fi
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(dirname "$task_dir")"
image="${TASK1_ORFS_IMAGE:-openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac}"
command -v docker >/dev/null
mkdir -p "$1"
out="$(cd "$1" && pwd)"
if [ -n "$(ls -A "$out")" ]; then
  echo "Output directory must be empty: $out" >&2
  exit 2
fi
mkdir "$out/delivery"

# Resolve the tag before running. The run receipt carries the immutable digest.
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
  -v "$task_dir:/work/task1:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$digest" -ec '
    set -o pipefail
    # Environment comes from the same image as the flow and platform.
    if [ -f ../env.sh ]; then source ../env.sh; fi
    {
      uname -sm
      yosys -V
      openroad -version
      klayout -v
    } > /work/out/delivery/tools.txt
    make DESIGN_CONFIG=/work/task1/physical/config.mk WORK_HOME=/work/out/flow LEC_CHECK=0 finish
    export TASK1_FINAL_DIR=/work/out/flow/results/nangate45/task1_module1/base
    export TASK1_PROXY_LIBERTY="$PWD/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
    openroad -no_init -exit /work/task1/physical/post_route.tcl \
      | tee /work/out/delivery/post_route_sta.rpt
    cp platforms/nangate45/LICENSE /work/out/delivery/Nangate45-LICENSE.txt
    cd "$(dirname "$TASK1_PROXY_LIBERTY")"
    sha256sum "$(basename "$TASK1_PROXY_LIBERTY")" > /work/out/delivery/liberty.sha256
  ' 2>&1 | tee "$out/flow.log"

python3 "$task_dir/physical/package_proxy.py" "$out" "$task_dir"
