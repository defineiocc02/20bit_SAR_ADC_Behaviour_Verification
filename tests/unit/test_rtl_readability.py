"""Counterexamples for the finite source-token identity guard."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools/audit_rtl_readability.py"
SPEC = importlib.util.spec_from_file_location("rtl_readability_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
tokens = MODULE.tokens


def test_comment_and_layout_only():
    assert tokens("wire [7:0] x; // text\nassign x = 8'd1;") == tokens(
        "/* engineering note */ wire [7:0] x;\nassign x = 8'd1;\n"
    )


@pytest.mark.parametrize(
    ("original", "mutated"),
    [
        ("assign q=a+b;", "assign q=a-b;"),
        ("assign q=8'd1;", "assign q=8'd2;"),
        ('initial $fatal(1,"a//b");', 'initial $fatal(1,"a//c");'),
        ("wire x /* verilator split_var */;", "wire x;"),
        ("if (x<=y) q=0;", "if (x < = y) q=0;"),
        ('`include "a.vh"\n', '`include "b.vh"\n'),
        ("`define X 1\nwire a;", "`define X 1 wire a;\n"),
        ("wire \\x.y ;", "wire x.y;"),
    ],
)
def test_effective_change_is_detected(original, mutated):
    assert tokens(original) != tokens(mutated)


def test_unsupported_text_is_visible():
    with pytest.raises(ValueError, match="Unsupported lexical input"):
        tokens("wire 中文;")
    with pytest.raises(ValueError, match="Unterminated block comment"):
        tokens("/* unfinished")
    with pytest.raises(ValueError, match="Continued directive"):
        tokens("`define X \\\n 1")
