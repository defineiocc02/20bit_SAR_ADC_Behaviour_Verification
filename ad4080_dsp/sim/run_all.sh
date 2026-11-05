#!/usr/bin/env bash
# Explicit selected-component runner. Required: --tops NAME... --out NEW_EXTERNAL_DIR.
# The historical filename does not select a default suite or imply complete coverage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$SCRIPT_DIR/../../tools/run_open_rtl.py" "$@"
