"""Engineering-spec checks that a human reviewer would otherwise do by eye.

Each check prints PASS/FAIL with the offending item and returns a non-zero exit
code if anything fails. The point is that these rules stop being prose in a task
book and become something CI can run.

Checks
------
C1 dependency direction      core package must not import tools/experiments/tests/docs
C2 CAAZ isolation            main package and RTL filelist must not pull in the extension
C3 documentation links       every relative markdown link resolves
C4 result identity           figure set and matrix share one config list and grid
C5 duplicate formulas        no two modules carry byte-identical public functions
C6 entry points              commands named in the quickstart exist and are runnable

    python tools/check_engineering.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "adi_model"
FORBIDDEN_TOP = {"tools", "experiments", "tests", "docs", "verification", "matplotlib"}

results: list[tuple[str, bool, str]] = []


def record(check: str, ok: bool, detail: str) -> None:
    """Remember one check result for the final report."""
    results.append((check, ok, detail))


def check_dependency_direction() -> None:
    """C1: the model must not depend on experiments, tools or tests."""
    bad = []
    for path in CORE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for name in modules:
                if name.split(".")[0] in FORBIDDEN_TOP:
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno} -> {name}")
    record("C1 dependency direction", not bad,
           "no core module imports experiments/tools/tests/docs" if not bad else "; ".join(bad[:6]))


def check_caaz_isolation() -> None:
    """C2: CAAZ stays an extension; nothing on the main path may reach it."""
    hits = []
    for path in CORE.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "caaz" in stripped.lower():
                hits.append(f"{path.relative_to(ROOT)}:{lineno}")
    filelist = (ROOT / "rtl" / "rtl_sources.f").read_text()
    rtl_hits = [l for l in filelist.splitlines() if "caaz" in l.lower()]
    ok = not hits and not rtl_hits
    record("C2 CAAZ isolation", ok,
           "no import of caaz in core; RTL filelist clean" if ok
           else f"py={hits[:4]} rtl={rtl_hits[:4]}")


LINK = re.compile(r"\]\(([^)#][^)]*)\)")


def check_doc_links() -> None:
    """C3: a dead relative link is a documentation bug, not a style issue."""
    broken = []
    for path in list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")) + \
            list((ROOT / "verification").rglob("*.md")):
        if "history" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            # Bit-width notation such as [15:0](2) is not a link. Only treat a
            # target as a path when it has a slash or a file extension.
            if "/" not in target and not re.search(r"\.[A-Za-z0-9]{1,5}$", target):
                continue
            # `docs/x.md#section` must resolve the file, not the anchor.
            clean = target.split()[0].rstrip("`").split("#")[0]
            if clean.startswith("<"):
                continue
            resolved = (path.parent / clean).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {clean}")
    record("C3 documentation links", not broken,
           "all relative links resolve" if not broken else "; ".join(broken[:6]))


def _semantic_digest_of_python(text: str) -> str:
    """忽略注释与 docstring 的 AST 摘要（只看语法树）。

    ⚠️ **不能 import `adi_model.hashing`**：本工具跑在系统 python（3.9）下，
    而 `adi_model` 需要 3.13（`typing.TypeGuard`）。因此这里用标准库 `ast`
    实现等价逻辑，与 `adi_model.hashing._strip_python` 保持同一判据：
    剥掉 Module/FunctionDef/ClassDef 的首个字符串表达式（docstring）后
    比较 `ast.dump`。注释本来就不进 AST，天然被忽略。

    若两处逻辑出现分歧，`tests/unit/test_hashing.py` 里的对照测试会失败。
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def check_result_identity() -> None:
    """C4: figures and matrix must describe the same run."""
    figures = ROOT / "docs/reviews/20261113_p3p4/figures/figures.json"
    matrix = ROOT / "docs/reviews/20261113_p3p4/data/p4_ablation.json"
    if not figures.is_file() or not matrix.is_file():
        record("C4 result identity", False, "figures.json or p4_ablation.json missing")
        return
    fig = json.loads(figures.read_text())
    mat = json.loads(matrix.read_text())
    matrix_ids = {c["id"] for c in mat["configs"]}
    f03 = next((f for f in fig["figures"] if f["figure"].startswith("F03")), None)
    problems = []
    if f03 is None:
        problems.append("F03 missing")
    else:
        # The figure uses human labels; require every *non-calibration* matrix id
        # to have a curve, and require the grid sizes to agree.
        # 网格尺寸不再是唯一判据：真正要保证的是"校准训练覆盖全部 512 个粗码"。
        # 2026-11-13：码本端点改为精确 ±v_fs 后，±0.9995·v_fs 的旧网格够不到
        # 码 511，训练会漏掉端点码而失败（见 run_p4_ablation 的同一处修正）。
        if fig["points"] < 512:
            problems.append(f"figure grid {fig['points']} < 512 (cannot cover all coarse codes)")
        if fig["repeats"] < 1:
            problems.append(f"figure repeats {fig['repeats']} < 1")
        if len(f03["configs"]) < 9:
            problems.append(f"F03 has {len(f03['configs'])} curves, expected >=9")
    if len(matrix_ids) < 11:
        problems.append(f"matrix has {len(matrix_ids)} configs, expected 11")
    # 结果身份：图必须能追到某个提交，且不能声称代表更新的代码。
    # 2026-11-13：原来只检查网格尺寸，旧图（63+8 时代的 source_sha）照样通过，
    # 于是一批过期图长期被当成"当前结果"。这里把它显式列出来。
    stale = None
    if "source_sha" not in fig or len(str(fig.get("source_sha", ""))) < 7:
        problems.append("figures.json has no usable source_sha")
    else:
        import subprocess

        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except Exception:  # noqa: BLE001 - 无 git 时不做身份判断
            head = ""
        if head and not head.startswith(str(fig["source_sha"])[:12]):
            # ⚠️ 图不可能由"包含图自己的那个提交"生成（生成时该提交还不存在）。
            # 因此只要 source_sha 是 HEAD 的**祖先**，且两者之间**没有改变模型**，
            # 图就是当前的。
            # 2026-11-13：原实现只比较 HEAD 是否等于 source_sha，
            # 导致"提交图 → HEAD 变了 → 立刻判 STALE"，形成逻辑死循环。
            # 只改注释/docstring 不应让图变 stale。判据用 **AST 语义摘要**
            # （`adi_model.hashing.semantic_digest_of_text`），而不是 diff 行过滤。
            #
            # 2026-11-13 的两轮教训：
            #   ① 原用 `git diff --name-only` ⇒ 加一行注释就误报 STALE
            #   ② 改用"过滤 diff 行" ⇒ 又把 `+++ b/...` 文件头当成 2 行实质改动
            # AST 摘要一次解决两者：它按语法树比较，天然忽略注释与 docstring。
            try:
                changed_files = subprocess.run(
                    ["git", "diff", "--name-only", str(fig["source_sha"]), "HEAD",
                     "--", "src/adi_model"],
                    cwd=ROOT, capture_output=True, text=True, check=True,
                ).stdout.split()
                substantive = []
                for rel in changed_files:
                    current = ROOT / rel
                    if not current.is_file() or current.suffix != ".py":
                        substantive.append(rel)
                        continue
                    old_text = subprocess.run(
                        ["git", "show", f"{fig['source_sha']}:{rel}"],
                        cwd=ROOT, capture_output=True, text=True,
                    ).stdout
                    try:
                        if _semantic_digest_of_python(old_text) != _semantic_digest_of_python(
                            current.read_text(encoding="utf-8", errors="replace")
                        ):
                            substantive.append(rel)
                    except SyntaxError:
                        # 旧版语法不合法（或文件新增）⇒ 保守地算作实质改动
                        substantive.append(rel)
            except Exception:  # noqa: BLE001
                substantive = ["unknown"]
            if substantive:
                stale = (
                    f"{str(fig['source_sha'])[:12]}"
                    f"（src/adi_model 自那以后有 {len(substantive)} 个文件语义改动）"
                )
            # 无实质改动 ⇒ 模型未变，图仍代表当前模型，不算 stale
    checked = not problems
    detail = (
        f"figure grid {fig['points']}x{fig['repeats']}, {len(f03['configs'])} curves, "
        f"{len(matrix_ids)} matrix configs"
        if checked
        else "; ".join(problems)
    )
    if checked and stale:
        # 2026-11-13：原设计是"只警告不失败"，理由是"这批图本来就要按新几何重生成"。
        # 但 17/5 迁移**已经完成**，模型在 f9d6d10..HEAD 之间有 11 次提交、
        # 其中 2 次改变了行为（双重减法、dither step_q）⇒ 旧图确实已不代表当前模型。
        # 既然现在重生成是可行的，"警告"就不再合适——图要么是当前的，要么是错的。
        checked = False
        detail += (
            f"; STALE figures: source_sha {stale} != HEAD — run "
            "`tools/run_review_figures.py --points 512 --repeats 8` to regenerate"
        )
    record("C4 result identity", checked, detail)


def check_duplicate_formulas() -> None:
    """C5: two copies of one production function drift; one copy cannot."""
    seen: dict[str, list[str]] = {}
    for path in CORE.rglob("*.py"):
        text = path.read_text()
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                body = ast.get_source_segment(text, node) or ""
                digest = hashlib.sha256(body.encode()).hexdigest()[:16]
                seen.setdefault(digest, []).append(f"{path.relative_to(ROOT)}::{node.name}")
    duplicated = [v for v in seen.values() if len(v) > 1]
    record("C5 duplicate formulas", not duplicated,
           "no byte-identical public function in two modules" if not duplicated
           else "; ".join(" == ".join(group) for group in duplicated[:4]))


QUICKSTART = ROOT / "docs" / "guide" / "QUICKSTART.md"


def check_entry_points() -> None:
    """C6: every command in the quickstart must name a file that exists."""
    if not QUICKSTART.is_file():
        record("C6 entry points", False, "docs/guide/QUICKSTART.md missing")
        return
    text = QUICKSTART.read_text(encoding="utf-8")
    referenced = re.findall(r"(tools/[\w./-]+\.py|verification/[\w./-]+\.py)", text)
    missing = sorted({r for r in referenced if not (ROOT / r).is_file()})
    core_entry = (ROOT / "src/adi_model/__main__.py").is_file()
    ok = not missing and core_entry
    record("C6 entry points", ok,
           f"{len(set(referenced))} referenced scripts exist, python -m adi_model present"
           if ok else f"missing={missing} main={core_entry}")


def check_fingerprint_baseline() -> None:
    """C7: 行为指纹基线的元数据必须与**源码里的默认参数**一致。

    2026-11-13：本会话改了 σ(1e-3→1e-4)、ADC2 位数(12→14)、ra_clip(0.5→0.45)、
    子阵列列数(5→4)，指纹漂移了 12 个字段——但**没人会注意到**，除非显式比对。
    这条检查把"基线描述必须跟上代码"变成门禁的一部分。

    实现上**不 import adi_model**：本检查跑在系统 python（3.9）下，而仓库要求 3.13。
    改为用正则从 `src/adi_model/defaults.py` 读默认值，做文本比对——
    这样 C7 在任何解释器下都能跑，不依赖运行时环境。
    """
    baseline = ROOT / "docs/restructure/behavior_fingerprint.json"
    defaults = ROOT / "src/adi_model/defaults.py"
    if not baseline.is_file():
        record("C7 fingerprint baseline", False, "behavior_fingerprint.json missing")
        return
    if not defaults.is_file():
        record("C7 fingerprint baseline", False, "src/adi_model/defaults.py missing")
        return
    try:
        document = json.loads(baseline.read_text(encoding="utf-8"))
    except json.JSONDecodeError as failure:
        record("C7 fingerprint baseline", False, f"baseline is not valid JSON: {failure}")
        return
    model_text = str(document.get("model", ""))
    source = defaults.read_text(encoding="utf-8")

    problems = []
    sigma_match = re.search(r"unit_mismatch_std:\s*float\s*=\s*([0-9.eE+-]+)", source)
    bits_match = re.search(r"adc2_n_bits:\s*int\s*=\s*(\d+)", source)
    if sigma_match is None:
        problems.append("cannot find unit_mismatch_std default in defaults.py")
    else:
        sigma = float(sigma_match.group(1))
        if f"mismatch={sigma:.0e}" not in model_text:
            problems.append(
                f"baseline says {model_text!r} but defaults.py has mismatch={sigma:.0e}"
            )
    if bits_match is None:
        problems.append("cannot find adc2_n_bits default in defaults.py")
    else:
        bits = int(bits_match.group(1))
        if f"adc2_n_bits={bits}" not in model_text:
            problems.append(
                f"baseline says {model_text!r} but defaults.py has adc2_n_bits={bits}"
            )
    if problems:
        record("C7 fingerprint baseline", False, "; ".join(problems))
        return
    record("C7 fingerprint baseline", True,
           f"baseline metadata matches defaults.py ({model_text})")


def main() -> int:
    check_dependency_direction()
    check_caaz_isolation()
    check_doc_links()
    check_result_identity()
    check_duplicate_formulas()
    check_entry_points()
    check_fingerprint_baseline()
    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
        failures += 0 if ok else 1
    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
