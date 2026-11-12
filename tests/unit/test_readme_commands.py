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

#: 会 `import experiments.configs.matrix`、因此需要仓库根目录在 PYTHONPATH 里的工具。
_EXPERIMENTS_TOOLS = ("run_p4_ablation.py", "run_review_figures.py")

#: **历史文档**目录：其中的脚本引用属于当时快照，不要求当前仓库里存在。
#: 这些目录下的文档本身也写了"仅描述原记录"之类的状态说明。
_HISTORICAL_PREFIXES = (
    "docs/history/",            # 历史快照
    "docs/caaz/",               # af8/9bdf 历史专题，页首已声明"仅描述原记录"
    "docs/randomization_caaz/", # 同上
    "docs/evidence/",           # 带日期的证据快照
    "docs/reports/",            # 一次性复核报告
    "docs/reviews/20261112_w0/",# 某轮 W0 的过程记录
    "docs/adr/0021-",           # 该 ADR 已声明"引用的驱动当前不在本仓库内"
)

#: 逐文件的历史/评审记录（其脚本引用是当时的，工具后来被重构掉）。
_HISTORICAL_FILES = (
    "docs/reproduction_results.md",                 # 页首已写"历史版本文档"
    "docs/review_response_2026-09-11.md",           # 外部复核裁定记录
    "docs/review_response_2026-09-11b.md",
    "docs/review_response_2026-09-11c.md",
    "docs/rtl/MUTATION_AUDIT_VS_VERIBUGBENCH.md",   # 变异审计记录
    "docs/ACCEPTANCE_20261112.md",                  # 已加时效标注，数字过时
    "docs/reviews/20261113_p3p4/09_audit_response.md",   # 审查处置记录
    "docs/reviews/20261113_p3p4/12_review2_response.md", # 审查处置记录
    "docs/reviews/20261113_p3p4/11_review_request_email.md",  # 发给外部审查者的邮件稿
    "docs/reviews/20261113_p3p4/README.md",             # 该轮评审的索引页
    "docs/reviews/PR9_RESPONSE_20261112.md",            # PR #9 响应记录
    "docs/review_response_2026-09-11d.md",              # 第五份外部复核的裁定记录
)


def _is_historical(path: Path) -> bool:
    """判断文档是否属于历史快照（其脚本引用不要求存在）。"""
    rel = str(path.relative_to(ROOT))
    return rel.startswith(_HISTORICAL_PREFIXES) or rel in _HISTORICAL_FILES


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _documented_command_lines() -> list[tuple[Path, str]]:
    """收集 README + docs/ 下所有 markdown 里的 PYTHONPATH 命令行。

    2026-11-13：README 修好后发现 `docs/guide/QUICKSTART.md` 里有同样的问题
    （run_p4_ablation / run_review_figures 缺 `.`）。守卫因此扩展到全部文档，
    不再只盯 README。
    """
    files = [README, *sorted((ROOT / "docs").rglob("*.md"))]
    rows: list[tuple[Path, str]] = []
    for path in files:
        if not path.is_file() or _is_historical(path):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("PYTHONPATH="):
                rows.append((path, stripped))
    return rows


def test_referenced_scripts_exist():
    """README 与 docs/ 里出现的 `tools/xxx.py` 必须真实存在。"""
    files = [README, *sorted((ROOT / "docs").rglob("*.md"))]
    missing: list[str] = []
    total = 0
    for path in files:
        if not path.is_file() or _is_historical(path):
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(set(re.findall(r"tools/[\w./-]+\.py", text))):
            total += 1
            if not (ROOT / name).is_file():
                missing.append(f"{path.relative_to(ROOT)} -> {name}")
    assert total > 0, "documents should reference at least one tool script"
    assert not missing, "documents reference missing scripts:\n  " + "\n  ".join(missing)


def test_experiments_commands_have_repo_root_on_the_path():
    """引用 `experiments` 的命令，其 PYTHONPATH 必须含仓库根目录。

    `experiments/` 是仓库根下的包，只在 `PYTHONPATH=src` 下 import 会失败。
    **覆盖 README 与 docs/ 下所有 markdown**（QUICKSTART 曾漏掉）。
    """
    offenders = []
    for path, stripped in _documented_command_lines():
        if not any(tool in stripped for tool in _EXPERIMENTS_TOOLS):
            continue
        path_part = stripped.split()[0]  # 形如 PYTHONPATH=src:.
        entries = path_part.split("=", 1)[1].split(":")
        if "." not in entries:
            offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
    assert not offenders, (
        "these documented commands import `experiments` but PYTHONPATH "
        "lacks the repo root ('.'):\n  " + "\n  ".join(offenders)
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


def test_document_images_resolve():
    """文档里的图片引用必须都能解析（39 处，全部有效）。

    与"数据文件路径"不同：图片是真实链接，必须存在；而
    ``data/p4_ablation.json`` 这类是**相对命令执行目录**的路径，
    不是相对文档的链接，不能按链接校验（否则大量误报）。

    本会话曾三次遇到"文档承诺但实际不成立"，图片断链是最容易被忽略的一类：
    读者点开看不到图，但没有任何检查会报错。
    """
    missing: list[str] = []
    total = 0
    files = [README, *sorted((ROOT / "docs").rglob("*.md"))]
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
            target = match.group(1).split()[0].strip("\"'")
            if target.startswith(("http://", "https://", "data:")):
                continue
            total += 1
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert total > 0, "documents should reference at least one image"
    assert not missing, "documents reference missing images:\n  " + "\n  ".join(missing)


#: 迁移/评审类文档：其中出现的代码路径是**当时**的位置（如迁移前路径、已删模块），
#: 不要求当前仓库里存在。这类文档表达的是历史事实，改写会破坏可追溯性。
_PATH_HISTORY_FILES = (
    "docs/migration.md",
    "docs/rtl/P2_INTERFACE.md",
)


def test_current_documents_reference_existing_modules():
    """现行文档里引用的 `src/adi_model/*.py` 必须真实存在。

    92 处模块引用，其中 8 处指向已重构移除的模块（`chopping_az.py`、
    `sim_split.py`、`acceptance.py`、`noise_phase.py`、`scheduler_edge.py`）。
    逐一核对后确认这 8 处**全部落在历史/评审文档里**（迁移记录记的是迁移前路径、
    评审响应记的是当时的模块名），不是缺陷。本测试把"现行文档不得引用不存在的模块"
    固定下来，防止后续重构留下断链。
    """
    missing: list[str] = []
    total = 0
    for path in [README, *sorted((ROOT / "docs").rglob("*.md"))]:
        if not path.is_file() or _is_historical(path):
            continue
        rel = str(path.relative_to(ROOT))
        if rel in _PATH_HISTORY_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"`((?:src/adi_model/|adi_model/)[\w/]+\.py)`", text):
            target = match.group(1)
            total += 1
            resolved = ROOT / (target if target.startswith("src/") else "src/" + target)
            if not resolved.exists():
                missing.append(f"{rel} -> {target}")
    assert total > 0, "documents should reference at least one module"
    assert not missing, (
        "current documents reference modules that no longer exist:\n  "
        + "\n  ".join(missing)
        + "\n(if these are historical references, add the file to _PATH_HISTORY_FILES "
        "or _HISTORICAL_FILES with a reason)"
    )


#: 63+8 时代的遗留 fixture 目录：其 manifest 自述为 63+8，与当前 17/5 不匹配。
_LEGACY_FIXTURES = ("sim/fixtures/fig19_ideal_ports",)


def test_legacy_fixtures_declare_their_geometry_and_are_not_cited_as_current():
    """遗留 fixture 必须（1）在 manifest 里自述几何，（2）有自己的 README 警告。

    2026-11-13：`sim/fixtures/fig19_ideal_ports` 整套是按 63+8 构造的，
    而主链已切到 17/5。它不能改一个字段就复用（权重、位宽、prefix table 全变），
    本机又没有 VCS 无法重新验证 ⇒ 保留为历史证据，但必须有明确警告，
    防止有人把它当成"当前模型的 golden 向量"。
    """
    for rel in _LEGACY_FIXTURES:
        directory = ROOT / rel
        manifest = directory / "fixture_manifest.json"
        assert manifest.is_file(), f"{rel} has no fixture_manifest.json"
        text = manifest.read_text(encoding="utf-8")
        # manifest 必须自述几何（这里是 63+8），否则无从判断它属于哪个时代
        assert "63+8" in text or "17/5" in text, (
            f"{rel}/fixture_manifest.json does not state its geometry; "
            "a fixture without a declared geometry cannot be checked against the model"
        )
        # 必须有 README 说明它是遗留/适用范围
        readme = directory / "README.md"
        assert readme.is_file(), (
            f"{rel} lacks a README warning that it is a legacy (63+8) fixture"
        )
        warning = readme.read_text(encoding="utf-8")
        assert "63+8" in warning and ("遗留" in warning or "legacy" in warning.lower()), (
            f"{rel}/README.md must state it is a legacy 63+8 fixture"
        )
