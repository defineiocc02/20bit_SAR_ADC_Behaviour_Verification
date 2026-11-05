#!/usr/bin/env bash
# Fixed AD4080 top entry. Required: --out NEW_EXTERNAL_DIR and any top binding inputs.
# An unregistered or unbound top fails visibly; no old binary or golden is consulted.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for argument in "$@"; do
    case "$argument" in
        --tops|--tops=*)
            printf '%s\n' 'run_top.sh selects ad4080_top_tb; use run_all.sh --tops for components' >&2
            exit 2
            ;;
    esac
done
exec "${PYTHON:-python3}" "$SCRIPT_DIR/../../tools/run_open_rtl.py" --tops ad4080_top_tb "$@"
