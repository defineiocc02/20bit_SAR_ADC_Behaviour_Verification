#!/usr/bin/env bash
# Bytecode-compile Tcl text; never execute vendor commands or claim API semantics.
set -euo pipefail
if (( $# == 0 )); then
    printf '%s\n' 'Usage: check_syntax.sh FILE.tcl [FILE.tcl ...]' >&2
    exit 2
fi
TCL_EXECUTABLE="${TCLSH:-$(command -v tclsh || true)}"
if [[ -z "$TCL_EXECUTABLE" || ! -x "$TCL_EXECUTABLE" ]]; then
    printf '%s\n' 'An existing independent tclsh is required; configure TCLSH' >&2
    exit 2
fi
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
cat > "$TEMP_DIR/check.tcl" <<'TCL'
if {![llength [info commands ::tcl::unsupported::disassemble]]} {
    error "This tclsh cannot compile script text without execution"
}
set count 0
foreach path $argv {
    if {![file isfile $path]} {error "Missing Tcl input: $path"}
    set fh [open $path r]
    fconfigure $fh -encoding utf-8
    set script [read $fh]
    close $fh
    if {![info complete $script]} {error "Incomplete Tcl script: $path"}
    ::tcl::unsupported::disassemble script $script
    incr count
}
puts "TCL_SCRIPT_PARSE_COMPLETE files=$count vendor_commands_executed=0 semantic_API_validation=NOT_ASSESSED"
TCL
"$TCL_EXECUTABLE" "$TEMP_DIR/check.tcl" "$@"
