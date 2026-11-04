#!/bin/sh
# Compile this multi-file report. This script does not validate its scientific claims.
set -eu

sar_build_dir=
fail() {
    printf '%s\n' "ERROR: $*" >&2
    if [ -n "$sar_build_dir" ]; then
        printf '%s\n' "FAILED: $*" > "$sar_build_dir/build.status"
        printf 'Diagnostics retained in: %s\n' "$sar_build_dir" >&2
    fi
    exit 1
}

[ "$#" -eq 0 ] || fail "No positional arguments. Set XELATEX to an executable path if needed."
sar_report_dir=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd)
[ -f "$sar_report_dir/report.tex" ] || fail "report.tex is missing beside this script."

# XELATEX is one executable, never a shell command or a string of options.
if [ "${XELATEX+x}" = x ]; then
    [ -n "$XELATEX" ] || fail "XELATEX is empty."
    sar_compiler=$XELATEX
elif command -v xelatex >/dev/null 2>&1; then
    sar_compiler=$(command -v xelatex)
elif [ -x /Library/TeX/texbin/xelatex ]; then
    sar_compiler=/Library/TeX/texbin/xelatex
else
    sar_compiler=
    for sar_candidate in "$HOME"/Library/TinyTeX/bin/*/xelatex; do
        if [ -x "$sar_candidate" ]; then
            sar_compiler=$sar_candidate
            break
        fi
    done
    [ -n "$sar_compiler" ] || fail "XeLaTeX not found. Set XELATEX or configure PATH; no tools will be installed."
fi
case "$sar_compiler" in
    */*)
        sar_compiler_dir=$(CDPATH= cd -P -- "$(dirname -- "$sar_compiler")" && pwd) || fail "Invalid XELATEX directory."
        sar_compiler="$sar_compiler_dir/$(basename -- "$sar_compiler")"
        ;;
    *) sar_compiler=$(command -v "$sar_compiler") || fail "XELATEX command not found." ;;
esac
[ -f "$sar_compiler" ] && [ -x "$sar_compiler" ] || fail "XELATEX is not an executable file."

# A fresh directory avoids old auxiliary files and preserves the shipped PDF.
mkdir -p "$sar_report_dir/.build"
sar_build_dir=$(mktemp -d "$sar_report_dir/.build/report.XXXXXX")
printf '%s\n' PREPARED > "$sar_build_dir/build.status"
printf '%s\n' "$sar_compiler" > "$sar_build_dir/compiler.path"
"$sar_compiler" --version > "$sar_build_dir/compiler.version" 2>&1 || fail "Cannot read XeLaTeX version."
cd "$sar_report_dir"

sar_error_pattern='^!|(^|[[:space:]])(LaTeX|Package [^:]+|Class [^:]+|pdfTeX|XeTeX) Error:|Emergency stop|Fatal error occurred|No pages of output'
sar_layout_pattern='Overfull \\[hv]box|Missing character:'
sar_rerun_pattern='(Reference|Citation).*undefined|There were undefined (references|citations)|Rerun to get|Label\(s\) may have changed|rerunfilecheck.*Warning|Please \(re\)run'

run_pass() {
    sar_pass=$1
    printf 'XeLaTeX pass %s; output: %s\n' "$sar_pass" "$sar_build_dir"
    if "$sar_compiler" -no-shell-escape -interaction=nonstopmode -halt-on-error \
        -file-line-error -recorder -output-directory="$sar_build_dir" report.tex \
        > "$sar_build_dir/pass-$sar_pass.console.log" 2>&1; then
        :
    else
        sar_rc=$?
        tail -n 35 "$sar_build_dir/pass-$sar_pass.console.log" >&2
        fail "XeLaTeX pass $sar_pass returned $sar_rc."
    fi
    [ -s "$sar_build_dir/report.log" ] || fail "XeLaTeX did not produce report.log."
    cp "$sar_build_dir/report.log" "$sar_build_dir/pass-$sar_pass.tex.log"
    if LC_ALL=C grep -nE "$sar_error_pattern" "$sar_build_dir/report.log"; then
        fail "TeX error diagnostic in pass $sar_pass."
    fi
}

save_auxiliary_state() {
    for sar_ext in aux toc out; do
        if [ -f "$sar_build_dir/report.$sar_ext" ]; then
            cp "$sar_build_dir/report.$sar_ext" "$sar_build_dir/before.$sar_ext"
        else
            : > "$sar_build_dir/before.$sar_ext"
        fi
    done
}

auxiliary_state_changed() {
    for sar_ext in aux toc out; do
        if [ -f "$sar_build_dir/report.$sar_ext" ]; then
            cmp -s "$sar_build_dir/before.$sar_ext" "$sar_build_dir/report.$sar_ext" || return 0
        elif [ -s "$sar_build_dir/before.$sar_ext" ]; then
            return 0
        fi
    done
    return 1
}

run_pass 1
save_auxiliary_state
run_pass 2
if auxiliary_state_changed || LC_ALL=C grep -qE "$sar_rerun_pattern" "$sar_build_dir/report.log"; then
    save_auxiliary_state
    run_pass 3
    auxiliary_state_changed && fail "References or contents still change after three passes."
fi
if LC_ALL=C grep -nE "$sar_error_pattern|$sar_layout_pattern|$sar_rerun_pattern" "$sar_build_dir/report.log"; then
    fail "Final log contains errors, overfull boxes, missing characters, or unresolved/rerun diagnostics."
fi
[ -s "$sar_build_dir/report.pdf" ] || fail "The output PDF is missing or empty."
printf '%s\n' COMPILE_CHECKS_PASSED_VISUAL_AND_EVIDENCE_REVIEW_REQUIRED > "$sar_build_dir/build.status"
printf '\nCompiled PDF: %s\n' "$sar_build_dir/report.pdf"
printf 'Final log: %s\n' "$sar_build_dir/report.log"
printf '%s\n' 'Compilation checks passed. Review all pages and evidence before replacing the released PDF.'
# Underfull boxes and other warnings are surfaced for review, not silently discarded.
LC_ALL=C grep -nE 'Warning|Underfull' "$sar_build_dir/report.log" || :
