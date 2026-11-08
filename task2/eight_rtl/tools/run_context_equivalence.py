# =============================================================================
# 文件名     : run_context_equivalence.py
# 模块名     : run_context_equivalence
# 版本       : v1.0
# 功能       : 在相同状态下比较两种扰动选词，并核对负注入减法，深度七拍。
#
# 设计说明   :
#   * 参考表达式与当前 RTL 共享状态，只比较选词和减法，不证明历史版本的时序等价。
#   * 参考表达式直接冻结在 ASSERTIONS，CI 浅克隆无需获取无关历史对象。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""在相同状态下比较两种扰动选词，并核对负注入减法，深度七拍。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOP = Path("task2/eight_rtl/rtl/dither8_top.sv")
DEPENDENCIES = (
    Path("task1/rtl/slice_scheduler.sv"),
    Path("task1/rtl/fig11_dem.sv"),
    Path("task1/rtl/module1_top.sv"),
    Path("task1/rtl/fig21_bridge.sv"),
    Path("task1/rtl/module1_bridge_top.sv"),
    Path("task2/rtl/dither_word_gen.sv"),
)
ASSERTIONS = """
  // 同状态核对按 ID 选词与事件选词，以及当前负注入减法。
  logic [5:0] baseline_word;
  logic [9:0] baseline_code;
  assign baseline_word = !conv_ready_o ? 6'd0 : (latest_match ? acq_word_o : previous_word_q);
  assign baseline_code = {1'b0, code_i}
                         - {7'b0, baseline_word[5], 1'b0, baseline_word[3]};
  always_comb begin
    assert (conv_word_o == baseline_word);
    if (conv_ready_o) assert (adjusted_code == baseline_code);
  end
"""


# =========================================================================
# 1. 函数组：_sha256 / _main
# =========================================================================
# -------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="new output directory")
    args = parser.parse_args()
    yosys = shutil.which(os.environ.get("YOSYS", "yosys"))
    if yosys is None:
        parser.error("Yosys was not found. Set YOSYS to an installed executable.")
    args.out.mkdir(parents=True, exist_ok=False)
    # [工具兼容] 0.33 前端直接生成 $assert，新版才可能需要降低 $check。
    # 只按工具自述的可用选项选择降低步骤，随后强制检查实际断言非空。
    help_text = subprocess.check_output([yosys, "-Q", "-T", "-p", "help chformal"], text=True)
    lowering = "chformal -lower\n" if "-lower" in help_text else ""
    candidate = (ROOT / TOP).read_text()
    if not candidate.rstrip().endswith("endmodule"):
        raise ValueError("dither8_top.sv must end with endmodule")
    wrapped = candidate.rstrip().removesuffix("endmodule") + ASSERTIONS + "endmodule\n"
    with tempfile.TemporaryDirectory(prefix="dither8-equiv-") as temp:
        work = Path(temp)
        for source in DEPENDENCIES:
            shutil.copyfile(ROOT / source, work / source.name)
        (work / TOP.name).write_text(wrapped)
        script = f"""read_verilog -sv -formal {' '.join(p.name for p in DEPENDENCIES)} {TOP.name}
prep -top dither8_top -flatten
{lowering}opt
select -assert-any t:$assert
sat -seq 7 -set-at 1 rst_ni 0 -set-def-inputs -prove-asserts -timeout 45 -verify
"""
        (work / "equiv.ys").write_text(script)
        run = subprocess.run(
            [yosys, "-s", "equiv.ys"],
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=90,
        )
    passed = run.returncode == 0 and b"SAT proof finished - no model found: SUCCESS!" in run.stdout
    log = gzip.compress(run.stdout, mtime=0)
    (args.out / "context_equivalence.log.gz").write_bytes(log)
    receipt = {
        "status": "PASS" if passed else "FAIL",
        "scope": "Same-state combinational equivalence for conversion word and legal arithmetic. "
        "This does not prove equivalence to historical positive injection. Seven steps including reset.",
        "assertion_lowering": "chformal -lower" if lowering else "frontend $assert",
        "tool": subprocess.check_output([yosys, "-V"], text=True).strip(),
        "reference_kind": "same_state_id_mux_and_negative_subtraction",
        "reference_expression_sha256": _sha256(ASSERTIONS.encode()),
        "source_sha256": {str(p): _sha256((ROOT / p).read_bytes()) for p in (*DEPENDENCIES, TOP)},
        "instrumented_candidate_sha256": _sha256(wrapped.encode()),
        "driver_sha256": _sha256(Path(__file__).read_bytes()),
        "proof_script_sha256": _sha256(script.encode()),
        "raw_log_sha256": _sha256(run.stdout),
        "compressed_log_sha256": _sha256(log),
    }
    (args.out / "context_equivalence.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"{receipt['status']} {args.out / 'context_equivalence.json'}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    _main()
