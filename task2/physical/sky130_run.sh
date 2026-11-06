#!/usr/bin/env bash
# Run the frozen digital checkpoint on Linux/amd64 with the public ORFS image.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo 'Usage: bash task2/physical/sky130_run.sh EMPTY_DIRECTORY [dither8_top|sar20_digital_top]' >&2
  exit 2
fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
top="${2:-dither8_top}"
case "$top" in
  dither8_top) config=task2/physical/sky130_config.mk; nickname=task2_dither8 ;;
  sar20_digital_top) config=task2/physical/sky130_integrated_config.mk; nickname=task_integration_sar20 ;;
  *) echo "Unsupported SKY130HD top: $top" >&2; exit 2 ;;
esac
image=openroad/orfs@sha256:a6140cf4dc568e10238356f67b9fd73b9074955eeda6f2ee126bc60f5d7fc3ac
command -v docker >/dev/null
mkdir -p "$1"
out="$(cd "$1" && pwd)"
if [ -n "$(ls -A "$out")" ]; then
  echo "Output directory must be empty: $out" >&2
  exit 2
fi
mkdir "$out/delivery"
source_sha="$(git -C "$repo_dir" rev-parse HEAD)"
run_id="${TASK2_PHYSICAL_RUN_ID:-$top-$(date -u +%Y%m%dT%H%M%SZ)}"
python3 "$repo_dir/task2/physical/package_run.py" freeze \
  "$out" "$repo_dir" "$top" "$nickname" "$config" "$source_sha" "$run_id"

for attempt in 1 2 3; do
  if docker pull "$image"; then break; fi
  if [ "$attempt" = 3 ]; then exit 1; fi
  sleep 4
done
digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")"
if [ "$digest" != "$image" ]; then
  echo "Unexpected ORFS image: $digest" >&2
  exit 1
fi
printf '%s\n' "$digest" > "$out/delivery/container.txt"

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --entrypoint /bin/bash \
  -e HOME=/tmp -e QT_QPA_PLATFORM=offscreen \
  -e "TASK2_CONFIG=$config" -e "TASK2_NICKNAME=$nickname" \
  -v "$repo_dir:/work/repo:ro" -v "$out:/work/out" \
  -w /OpenROAD-flow-scripts/flow "$digest" \
  /work/repo/task2/physical/run_inside.sh 2>&1 | tee "$out/flow.log"

python3 "$repo_dir/task2/physical/package_run.py" package \
  "$out" "$repo_dir" "$top" "$nickname" "$config" "$source_sha" "$run_id"
