"""README 里承诺的命令必须真的能跑 —— 静态守卫 + 冒烟。

## 为什么需要这个测试

2026-11-13 发现两个长期隐藏的问题：
1. `tools/check_audit_findings.py` 在 README 里写着"应 4/4 PASS"，
   实际却因为 63+8 几何残留**抛异常崩溃**。它一直在管道里跑，
   `echo $?` 拿到的是 `tail` 的退出码，所以看起来永远成功。
2. README §7 的矩阵与图命令用 `PYTHONPATH=src`，但 `experiments/` 在仓库根目录，
   缺 `.` 导致 `ModuleNotFoundError`。

两次都是"README 承诺了、但没人原样执行"造成的。

## 本测试守什么

* **静态**：README 里引用的 `.py` 脚本必须存在；引用 `experiments` 的命令
  其 PYTHONPATH 必须包含仓库根目录。
* **冒烟**：`check_audit_findings.py` 真的以 exit 0 结束（它最快，1 秒内）。
  其余重命令不做冒烟（矩阵/图要几分钟），只做静态检查。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def test_referenced_scripts_exist():
    """README 里出现的 `tools/xxx.py` 必须真实存在。"""
    text = _readme_text()
    referenced = sorted(set(re.findall(r"tools/[\w./-]+\.py", text)))
    assert referenced, "README should reference at least one tool script"
    missing = [name for name in referenced if not (ROOT / name).is_file()]
    assert not missing, f"README references missing scripts: {missing}"


def test_experiments_commands_have_repo_root_on_the_path():
    """引用 `experiments` 的命令，其 PYTHONPATH 必须含仓库根目录。

    `experiments/` 是仓库根下的包，只在 `PYTHONPATH=src` 下 import 会失败。
    """
    text = _readme_text()
    offenders = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("PYTHONPATH="):
            continue
        # 只检查会 import experiments 的工具
        if not any(
            tool in stripped for tool in ("run_p4_ablation.py", "run_review_figures.py")
        ):
            continue
        path_part = stripped.split()[0]  # 形如 PYTHONPATH=src:.
        entries = path_part.split("=", 1)[1].split(":")
        if "." not in entries:
            offenders.append(stripped)
    assert not offenders, (
        "these commands import `experiments` but PYTHONPATH lacks the repo root ('.'):\n  "
        + "\n  ".join(offenders)
    )


def test_simulate_commands_warn_about_new_output_directory():
    """`simulate --output` 要求新目录；README 必须提到这一点，否则读者会撞错。"""
    text = _readme_text()
    if "simulate" not in text or "--output" not in text:
        pytest.skip("README does not document a simulate command")
    assert "新目录" in text or "new directory" in text, (
        "README should note that --output must be a new directory "
        "(the CLI rejects an existing one with SIMULATION_REJECTED)"
    )


def test_audit_findings_really_exits_zero():
    """冒烟：`check_audit_findings.py` 必须以 exit 0 结束。

    这是 README 明确承诺"应 4/4 PASS"的那条命令。
    直接检查 returncode，不走管道（管道会掩盖真实退出码）。
    """
    result = subprocess.run(
        [sys.executable, "tools/check_audit_findings.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, (
        "check_audit_findings.py failed (README promises 4/4 PASS):\n"
        + (result.stdout or "")[-1500:]
        + (result.stderr or "")[-1500:]
    )
    assert "4/4" in result.stdout, f"expected 4/4 resolved, got:\n{result.stdout[-800:]}"
