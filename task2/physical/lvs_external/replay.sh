#!/usr/bin/env bash
# Run the archived external reconstruction top through both public LVS decks.
set -euo pipefail
if [ "$#" != 1 ]; then
  echo 'Usage: replay.sh NEW_RUN_DIRECTORY' >&2
  exit 2
fi
replay_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$replay_dir/../../.." && pwd)"
mkdir "$1"
run_dir="$(cd "$1" && pwd)"
out="$run_dir/evidence"
mkdir "$out"
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac
deck_revision=5eebdc0d06879cf674e86e3fb8efa2803e6e44dc

python3 "$replay_dir/evidence.py" freeze "$repo_dir" "$run_dir"
curl --fail --silent --show-error --retry 3 --max-time 45 \
  "https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/$deck_revision/flow/platforms/sky130hd/lvs/sky130hd.lylvs" \
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
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \
  -v "$repo_dir:/work/repo:ro" -v "$run_dir/frozen_input:/work/input:ro" -v "$out:/work/out" \
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
    openroad -no_init -exit /work/repo/task2/physical/lvs_external/export_cdl.tcl \
      > /work/out/export_cdl.log 2>&1
    cat /work/out/sar20_digital_top.cdl /work/out/compatible.cdl > /work/out/reference.cdl
    sha256sum /work/out/reference.cdl > /work/out/reference.sha256

    for mode in legacy upstream; do
      dir="/work/out/$mode"
      mkdir "$dir"
      # The default legacy report follows the GDS path and needs a writable directory.
      layout="$dir/sar20_digital_top.gds"
      cp /work/input/sar20_digital_top.gds "$layout"
      cmp /work/input/sar20_digital_top.gds "$layout"
      report=()
      if [ "$mode" = legacy ]; then
        deck="$platform/lvs/sky130hd.lylvs"
      else
        deck=/work/out/upstream.lylvs
        report=(-rd "report_file=$dir/lvs_report.lvsdb")
      fi
      sha256sum "$deck" > "$dir/deck.sha256"
      sha256sum /work/out/reference.cdl > "$dir/reference_checked.sha256"
      if (cd "$dir" && timeout 300 klayout -b \
        -rd "in_gds=$layout" \
        -rd cdl_file=/work/out/reference.cdl -rd top_cell=sar20_digital_top \
        -rd target_netlist="$dir/extracted.cir" "${report[@]}" -r "$deck") > "$dir/lvs.log" 2>&1; then
        printf "0\n" > "$dir/exit.txt"
      else
        printf "%s\n" "$?" > "$dir/exit.txt"
      fi
      cmp /work/input/sar20_digital_top.gds "$layout"
      sha256sum "$layout" > "$dir/gds_checked.sha256"
      rm "$layout"
    done
    sha256sum -c /work/out/reference.sha256
    sha256sum /work/input/sar20_digital_top.gds > /work/out/gds_after.sha256
  ' 2>&1 | tee "$out/replay.log"

python3 "$replay_dir/evidence.py" report "$repo_dir" "$run_dir"
