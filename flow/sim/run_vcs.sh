#!/usr/bin/env bash
# Local VCS component execution. Required: --tops NAME... --out NEW_EXTERNAL_DIR.
# VCS and license configuration belong to the invoking host; no fallback is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$SCRIPT_DIR/../tools/run_rtl_sim.py" "$@"
