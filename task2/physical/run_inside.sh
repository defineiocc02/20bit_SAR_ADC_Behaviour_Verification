#!/usr/bin/env bash
# Linux runner. Keep stage logs and reject missing PG geometry before extraction.
set -eo pipefail
if [ -f ../env.sh ]; then source ../env.sh; fi
set -u
delivery=/work/out/delivery
project=/work/repo/task2/physical
platform_dir="$PWD/platforms/sky130hd"
export TASK1_FINAL_DIR="/work/out/flow/results/sky130hd/$TASK2_NICKNAME/base"
export TASK1_PROXY_LIBERTY="$platform_dir/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
build=(make "DESIGN_CONFIG=/work/repo/$TASK2_CONFIG" WORK_HOME=/work/out/flow LEC_CHECK=0)
test -s "$TASK1_PROXY_LIBERTY"
{
  uname -sm
  yosys -V
  openroad -version
  klayout -v
} > "$delivery/tools.txt"
python3 "$project/record_environment.py" "$platform_dir" "$delivery/environment.json"

function pg_stage() {
  local stage="$1" db="$2"
  test -s "$TASK1_FINAL_DIR/$db"
  sha256sum "$TASK1_FINAL_DIR/$db" > "$delivery/pg_${stage}_database.sha256"
  TASK2_PG_STAGE="$stage" TASK2_PG_DATABASE="$TASK1_FINAL_DIR/$db" \
    openroad -no_init -exit "$project/pg_audit.tcl" | tee "$delivery/pg_${stage}.log"
  grep -Fq "TASK2_PG_INVENTORY_COMPLETE $stage" "$delivery/pg_${stage}.log"
  for net in VDD VSS; do
    grep -Fq "All shapes on net $net are connected." "$delivery/pg_${stage}.log"
  done
}

"${build[@]}" synth
openroad -no_init -exit "$project/pre_route.tcl" | tee "$delivery/synth_sta.rpt"
grep -Fq 'TASK2_SYNTH_STA_COMPLETE' "$delivery/synth_sta.rpt"
"${build[@]}" floorplan
pg_stage floorplan 2_floorplan.odb
"${build[@]}" place
pg_stage placement 3_place.odb
"${build[@]}" cts
pg_stage cts 4_cts.odb
"${build[@]}" route
pg_stage route 5_route.odb
# Recheck a density-filled database before finish's extraction/report step.
"${build[@]}" "$TASK1_FINAL_DIR/6_1_fill.odb"
pg_stage before_extraction 6_1_fill.odb
"${build[@]}" finish
pg_stage final 6_final.odb
openroad -no_init -exit /work/repo/task1/physical/post_route.tcl \
  | tee "$delivery/post_route_sta.rpt"
cp /work/repo/task1/physical/sky130_license.txt "$delivery/SKY130HD-LICENSE.txt"

# PV is a separate measurement on the exact streamed GDS. Preserve failures.
sha256sum "$TASK1_FINAL_DIR/6_final.gds" > "$delivery/pv_input.sha256"
for check in drc lvs; do
  if timeout 300 "${build[@]}" "$check" > "$delivery/${check}_command.log" 2>&1; then
    printf '0\n' > "$delivery/${check}_exit.txt"
  else
    printf '%s\n' "$?" > "$delivery/${check}_exit.txt"
  fi
done
sha256sum -c "$delivery/pv_input.sha256"
