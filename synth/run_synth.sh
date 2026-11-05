#!/usr/bin/env bash
# Local explicit-input driver. Caller configures existing tools and licenses.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$SCRIPT_DIR/run_synth.py" "$@"
