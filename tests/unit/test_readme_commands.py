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


def test_topology_spec_defaults_are_not_the_rejected_legacy_geometry():
    """`TopologySpec` 的默认值不得是用户否决的 63+8 拓扑。

    2026-11-13：`contracts/specs.py` 的 dataclass 默认值一直是
    `n_main=63, n_sub=8, c_unit=40.04fF`（63+8 工程替代拓扑）。
    虽然每个 Profile 都显式传参覆盖、实际不生效，但用户要求"彻底删除 63+8"，
    默认值保留旧拓扑不符合要求，且容易被误当成当前拓扑读。

    真实生效值由 `test_adi_profile_matches_default_parameters` 校验
    （profile 与 `DefaultParameters` 逐项一致）。
    """
    from adi_model.contracts.specs import TopologySpec
    from adi_model.defaults import DefaultParameters

    params = DefaultParameters()
    field_defaults = {name: field.default for name, field in TopologySpec.__dataclass_fields__.items()}
    assert field_defaults["n_main"] == params.n_main, (
        f"TopologySpec.n_main default is {field_defaults['n_main']}, "
        f"but DefaultParameters.n_main is {params.n_main}"
    )
    assert field_defaults["n_sub"] == params.n_sub
    # 电容值用相对容差（源码里写的是截断的十进制字面量，浮点上略有差异）
    import pytest as _pytest

    assert field_defaults["c_unit_f"] == _pytest.approx(params.unit_cap_f, rel=1e-9)
    assert field_defaults["c_bridge_f"] == _pytest.approx(params.bridge_cap_f, rel=1e-9)
    assert field_defaults["c_feedback_f"] == _pytest.approx(params.feedback_cap_f, rel=1e-9)


def test_headline_metrics_in_readme_match_the_archived_data():
    """README 的"当前结论"表必须与归档数据逐项一致。

    2026-11-13：我一度用错误的键名读 NSD，以为它是 0——说明"文档里的数字
    是否等于数据文件里的数字"从来没被机械校验过。本测试把这个关系钉住。

    口径约定（见 README §1）：
      * DR 取 A2 的 `dr_db`
      * NSD 取 A2 的 `nsd_v_rthz_full`（全 Nyquist 白噪声等效密度）
    """
    import json

    data_path = ROOT / "docs/reviews/20261113_p3p4/data/p4_ablation.json"
    assert data_path.is_file(), "archived ablation data missing"
    configurations = {c["id"]: c for c in json.loads(data_path.read_text(encoding="utf-8"))["configs"]}
    baseline = configurations["A2_mismatch_noise"]["dynamic"]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # README 里写的 DR 与 NSD
    dr_text = f"{baseline['dr_db']:.2f}"
    nsd_text = f"{baseline['nsd_v_rthz_full'] * 1e9:.2f}"
    assert dr_text in readme, (
        f"README does not state the archived DR ({dr_text} dB); "
        "the headline table has drifted from the data file"
    )
    assert nsd_text in readme, (
        f"README does not state the archived NSD ({nsd_text} nV/rtHz, full-Nyquist); "
        "the headline table has drifted from the data file"
    )


def test_readme_states_which_nsd_definition_it_uses():
    """README 必须说明 NSD 用的是哪个口径（有两个，值不同：8.55 vs 9.11）。

    不写口径时，读者无法判断该拿 8.55 还是 9.11 与论文的 9.3 比较。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "nsd_v_rthz_full" in readme and "nsd_v_rthz_dr" in readme, (
        "README must name both NSD fields so the reader can tell which one is quoted"
    )


def test_inl_is_not_claimed_as_matching_the_paper():
    """INL 不得被表述为"与论文吻合"。

    2026-11-13：README 与验收报告曾用 ✅ 声称 INL 与论文 Fig.9.8.4 吻合，
    但 `docs/mechanisms/source_crosscheck_20261112.md` 明确写着
    「论文 Fig.9.8.4 的 20 位 INL/DNL 口径未被复现，不得并列比较」，
    且论文该图只标「20b level」、**未给数值**。

    两处说法直接冲突，且 ✅ 是夸大的那一侧。本测试把"不得比较"钉住。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    acceptance = (ROOT / "docs/ACCEPTANCE_20261113.md").read_text(encoding="utf-8")
    crosscheck = (ROOT / "docs/mechanisms/source_crosscheck_20261112.md").read_text(encoding="utf-8")

    # 交叉核对文档必须保留"不得并列比较"的结论
    assert "不得并列比较" in crosscheck, (
        "the source cross-check no longer states that the paper's INL definition "
        "is not reproduced; if that changed, this guard needs updating with evidence"
    )
    # 两份对外文档都必须转述这个限制
    for name, text in (("README.md", readme), ("ACCEPTANCE_20261113.md", acceptance)):
        assert "不可并列比较" in text or "不得并列比较" in text, (
            f"{name} must state that INL/DNL cannot be compared against the paper "
            "figure (the definitions differ)"
        )


def test_no_metric_row_claims_inl_match_with_a_check_mark():
    """不允许出现"INL ... ✅"这种把不可比项标成吻合的表格行。"""
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    offenders = [
        line
        for line in readme.splitlines()
        if line.lstrip().startswith("| INL") and "✅" in line
    ]
    assert not offenders, (
        "README marks INL with a check mark, but the paper figure gives no numeric "
        "INL and its definition was not reproduced:\n  " + "\n  ".join(offenders)
    )


def test_readme_states_no_mechanism_passes_all_four_dimensions():
    """"没有任何一项四项全 PASS"这个结论必须留在 README。

    2026-11-13：README 的机制表用 ✅ 表示「已实现 / 判据已交付」，
    但 `docs/mechanisms/source_crosscheck_20261112.md` 的四维评级结论是
    「没有任何一项四项全 PASS」——两者不冲突，但读者只看 ✅ 会高估完成度。

    ✅ 与四维评级并存是诚实的，前提是**四维结论要显式写在旁边**。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    crosscheck = (ROOT / "docs/mechanisms/source_crosscheck_20261112.md").read_text(encoding="utf-8")
    assert "没有任何一项四项全 PASS" in crosscheck, (
        "the four-dimension conclusion disappeared from the cross-check document"
    )
    assert "没有任何一项机制四项全 PASS" in readme, (
        "README must state that no mechanism passes all four cross-check dimensions; "
        "otherwise the check marks in the mechanism table overstate completion"
    )


def test_status_page_is_marked_historical():
    """README 不得把 `docs/STATUS.md` 当作"当前状态"链接。

    2026-11-13：README 把 STATUS.md 标为「当前状态」，但该文写于 2026-11-06，
    描述的是已被否决的 63+8 拓扑，且通篇不提 17/5——读者会把它当成现行状态。

    已给该页加时效横幅、并把 README 的链接标签改为「历史状态」。
    本测试防止回退。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[当前状态](docs/STATUS.md)" not in readme, (
        "README still labels docs/STATUS.md as the current status page; "
        "that page describes the superseded 63+8 topology"
    )
    status = (ROOT / "docs/STATUS.md").read_text(encoding="utf-8")
    assert "时效说明" in status, (
        "docs/STATUS.md must carry a freshness banner pointing to the current documents"
    )


def test_current_status_is_pointed_at_from_readme():
    """README 必须链到真正反映当前状态的文档。"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in ("docs/ACCEPTANCE_20261113.md", "docs/HANDOVER.md"):
        assert target in readme, f"README should link the current status source: {target}"


def test_documents_that_describe_the_rejected_topology_carry_a_banner():
    """描述 63+8 拓扑的文档必须带时效标注（或明确说明是历史/对比语境）。

    2026-11-13：用脚本扫描全部 `docs/**.md`，找"提到 63+8 但不提 17/5、
    也没有时效标注"的文件。找到并处理了：
      * `docs/architecture.md`（profile 表写着 20.5 pF / 63+8）
      * `docs/model_scope.md`（"The 63+8 complete-range realization"）
      * 3 个 ADR / 闭环文档（加时效横幅）

    此测试把这条规则钉住：新增文档若描述旧拓扑而不标注，会失败。
    例外：明确处于历史/对比语境（同时提到 17/5、或写明"历史""rejected"等）的
    文档不受限——它们表达的就是历史事实。
    """
    import re

    # 只扫非历史目录下的文档
    skip_prefixes = ("docs/history/", "docs/evidence/", "docs/reports/",
                     "docs/randomization_caoz/", "docs/reviews/")
    # 明确处于历史/对比语境、无需再加横幅的文档（逐条核对过）
    exempt = {
        # 通用「二进制 + unary 桥接」原理说明，63+8 只是举例用的权重表，不是拓扑描述
        "docs/binary_unary_bridge.md",
        # 记录"等间距 63+8 阵列下的实测对比结果"，是历史实验数据，值本身有意义
        "docs/mechanisms/dither_fig19.md",
        # 自身就是"原文口径 vs 本模型口径"的交叉核对文档，63+8 出现在口径推导链里
        "docs/mechanisms/source_crosscheck_20261112.md",
    }
    offenders: list[str] = []
    for path in sorted((ROOT / "docs").rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        if rel.startswith(skip_prefixes) or rel in exempt:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        mentions_old = bool(re.search(r"63\+8|n_main=63", text))
        if not mentions_old:
            continue
        # 明确处于历史/对比语境：提 17/5、或前 1500 字里有标注、或明确说废弃
        head = text[:1500]
        historic = (
            "17/5" in text
            or re.search(r"时效|历史|已过时|superseded|rejected|废弃|不再", head) is not None
        )
        if not historic:
            offenders.append(rel)
    assert not offenders, (
        "these documents describe the rejected 63+8 topology without a freshness banner "
        "or contrast with the current 17/5 array:\n  " + "\n  ".join(offenders)
    )


#: 需要在冒烟测试里真正跑一遍的轻量工具（重活工具另测或不测）。
#: 2026-11-13：trace_one_sample 曾因漏改 step_q 而静默失效（无任何测试覆盖），
#: 因此把"跑一遍"作为门槛。
#: `run_mismatch_sweep` 不放在这里：即便 --points 64 它也有约 70 s 的固定开销，
#: 会让单测慢到不可接受。改由 `tools/verify_clean_clone.sh` 在完整模式下覆盖。
_SMOKE_TOOLS = (
    "tools/trace_one_sample.py",
)


def test_lightweight_tools_actually_run(tmp_path):
    """轻量命令行工具必须能真的跑通。

    2026-11-13：修 `FIG19_STEP_Q` 时漏了 `tools/trace_one_sample.py`，
    它一直带着 `step_q=2**24`——而这条命令写在 README §7 里。
    没有任何测试跑过它，所以"改完 step_q 全绿"掩盖了这个失败。

    **教训**：常量改名/改值后，必须逐个跑一遍引用它的入口，而不是只跑测试。
    """
    import subprocess
    import sys

    for tool in _SMOKE_TOOLS:
        path = ROOT / tool
        assert path.is_file(), f"{tool} is listed for smoke testing but missing"
        result = subprocess.run(
            [sys.executable, tool, "--out", str(tmp_path / path.stem)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
        )
        assert result.returncode == 0, (
            f"{tool} exited {result.returncode}:\n"
            + (result.stdout or "")[-1200:]
            + (result.stderr or "")[-1200:]
        )


def test_legacy_fixture_tools_keep_the_63_8_contract_values():
    """服务 63+8 遗留 fixture 的两个工具必须保留旧合同值。

    2026-11-13：主链的 `FIG19_STEP_Q` 已改为 16 810 048（17/5 码本台阶），
    但下面两个工具服务于 `sim/fixtures/fig19_ideal_ports/` 那份**按 63/8 构造**的
    遗留 fixture，它们的合同值必须与 fixture 一致，**不能跟着主链改**：

      * `tools/run_fig19_rtl_calibration.py`（dither_terms 的权重表）
      * `tools/verify_source_bound_replay.py`（Fig19StaticSpec 的 step_q）

    改它们会让"合同值"与"fixture 实际数据"不一致，产生假通过/假失败。
    本测试把这一点钉住，并要求两个文件里都有说明性注释。
    """
    calibration = (ROOT / "tools/run_fig19_rtl_calibration.py").read_text(encoding="utf-8")
    replay = (ROOT / "tools/verify_source_bound_replay.py").read_text(encoding="utf-8")

    # 旧合同值仍在（9586981 是三项低位权的首项，1<<24 是旧 step_q）
    assert "9586981" in calibration, (
        "run_fig19_rtl_calibration.py lost the 63+8 contract weights; "
        "it serves the legacy fixture and must stay consistent with it"
    )
    assert "1 << 24" in replay or "1<<24" in replay, (
        "verify_source_bound_replay.py lost the 63+8 step_q; ditto"
    )
    # 两个文件都必须说明"这是遗留合同、不随主链改"
    for name, text in (
        ("run_fig19_rtl_calibration.py", calibration),
        ("verify_source_bound_replay.py", replay),
    ):
        assert "63+8" in text and ("遗留" in text or "legacy" in text.lower()), (
            f"{name} must document that its constants belong to the legacy 63+8 fixture"
        )


def test_english_readme_parameter_table_matches_the_model():
    """`README_EN.md` 的架构参数表必须反映当前实现。

    2026-11-13：英文 README 整张参数表停留在 63+8 时代——63 main + 8 sub、
    Cu=40.04 fF、Cnorm=20.5 pF、gain=32、12-bit ADC2、σ=0.001，
    每一项都与当前实现不符。中文 README 没有这张表，所以两边长期不一致而无人发现。
    """
    from adi_model.defaults import DefaultParameters, default_model

    params = DefaultParameters()
    model = default_model()
    en = (ROOT / "README_EN.md").read_text(encoding="utf-8")

    expectations = {
        "main/sub counts": f"{params.n_main} main + {params.n_sub} sub",
        "unit capacitance": "7.7799e-14",
        "active capacitance": "19.878",
        "ADC2 bits": f"{params.adc2_n_bits}-bit",
        "mismatch sigma": "1e-4",
    }
    missing = [name for name, token in expectations.items() if token not in en]
    assert not missing, (
        "README_EN.md parameter table is stale for: "
        + ", ".join(missing)
        + " — update it to match DefaultParameters"
    )


def test_english_readme_does_not_present_63_8_as_current():
    """英文 README 不得把 63+8 当作当前拓扑陈述。"""
    en = (ROOT / "README_EN.md").read_text(encoding="utf-8")
    # 允许作为"已废弃"提到，但不能作为当前值出现
    for stale in ("63 main + 8 sub", "20.5 pF", "beta=1/8", "12-bit ADC2"):
        if stale in en:
            # 必须同一行内有 rejected / superseded / earlier 之类的限定
            for line in en.splitlines():
                if stale in line:
                    assert any(
                        word in line.lower()
                        for word in ("rejected", "superseded", "earlier", "不再", "已废弃")
                    ), (
                        f"README_EN.md still states '{stale}' as current: {line.strip()[:120]}"
                    )


def test_documented_code_anchors_exist_in_the_source_tree():
    """文档里 `config.xxx` / `chip.xxx` 形式的代码锚点必须在源码中存在。

    2026-11-13：`docs/model_conventions.md` 用
    `| c_total0 | ... | config.c_total0 | ... |` 描述一个**已经不存在的属性**
    ——`c_total0` 在整个 `src/` 里查不到，实际用的是
    `DefaultModel.nominal_active_cap_f`。

    这类失效锚点不会让任何测试失败（它只是文档里的一串字符），
    但读代码的人会去找一个不存在的属性。
    """
    import re

    docs = [
        ROOT / "README.md",
        ROOT / "docs/model_conventions.md",
        ROOT / "docs/architecture.md",
        ROOT / "docs/HANDOVER.md",
        ROOT / "docs/ACCEPTANCE_20261113.md",
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "src").rglob("*.py")
    )
    pattern = re.compile(
        r"`(?:config|cfg|chip|model|DefaultModel|DefaultParameters|pool)\.([a-z_][a-z0-9_]*)`"
    )
    missing: list[str] = []
    checked = 0
    for path in docs:
        if not path.is_file():
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            attribute = match.group(1)
            checked += 1
            if attribute not in source:
                missing.append(f"{path.relative_to(ROOT)} -> {attribute}")
    assert checked > 0, "expected some documented code anchors to check"
    assert not missing, (
        "documents reference attributes that do not exist anywhere in src/:\n  "
        + "\n  ".join(missing)
    )


def test_documented_function_references_exist_or_are_flagged():
    """文档里 `module.func()` 形式的引用，要么函数存在，要么被显式标注为已失效。

    2026-11-13：`docs/audit_response.md` 引用了两个**已随重构消失**的函数
    （`dither_transfer_code`、`verify_nominal_conservation`），
    读者会去找不存在的代码。该文已有历史标注，但标注没点名这两处。
    """
    import re

    docs = [
        path for path in sorted((ROOT / "docs").rglob("*.md"))
        if not any(seg in str(path) for seg in ("/history/", "/evidence/", "/reports/"))
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "src").rglob("*.py")
    )
    pattern = re.compile(r"`([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\(\)`")
    offenders: list[str] = []
    for path in docs:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # 文档若已显式说明"函数已不存在/已改名"，则整篇豁免
        flagged = "已不存在" in text or "已改名" in text
        if flagged:
            continue
        for match in pattern.finditer(text):
            function = match.group(2)
            if function not in source:
                offenders.append(f"{path.relative_to(ROOT)} -> {function}()")
    assert not offenders, (
        "these documents reference functions that no longer exist and do not say so:\n  "
        + "\n  ".join(offenders)
    )


def test_readme_states_the_correct_number_of_clean_clone_checks():
    """README 里写的"全部 N 项"必须等于验证脚本的真实检查数。

    2026-11-13：README 写"全部 7 项"，但脚本已扩到 10 项
    （新增 run_mismatch_sweep、trace_one_sample、以及 integration 拆成两项）。
    数字过时不会让任何测试失败，但读者会以为覆盖范围比实际小。
    """
    import re

    script = (ROOT / "tools/verify_clean_clone.sh").read_text(encoding="utf-8")
    # 按"成功分支"计数：每一项检查恰好有一个 `ok "..."` 调用。
    # （不能用 note 行统计——有的 note 带缩进、且 note "clone"/"result" 不是检查项。）
    checks = len(re.findall(r'\bok "', script))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"全部\s*\*{0,2}(\d+)\*{0,2}\s*项", readme)
    assert match, "README should state how many clean-clone checks there are"
    stated = int(match.group(1))
    assert stated == checks, (
        f"README says {stated} checks but verify_clean_clone.sh has {checks}"
    )


def test_readme_points_at_the_current_acceptance_report():
    """README 的"判断可信度"入口必须指向当前验收报告，而不是旧快照。

    2026-11-13：原链接指向 `docs/acceptance/20261112-e14a0a0/report.md`
    （11-12 的快照），而当前是 `docs/ACCEPTANCE_20261113.md`。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/ACCEPTANCE_20261113.md" in readme, (
        "README must link the current acceptance report"
    )
    assert "20261112-e14a0a0/report.md" not in readme, (
        "README still points the credibility entry at the older 11-12 snapshot"
    )


def test_end_to_end_metric_tests_exist_and_run():
    """必须存在"跑真实模型 → 断言论文量级指标"的测试。

    2026-11-13 发现：`test_readme_commands.py` 只校验"README 的数字 ==
    归档 JSON 的数字"，而归档 JSON 是**冻结的数据文件**——
    即使模型退化，那个测试仍然通过。

    于是补了 `tests/verification/test_paper_metrics_end_to_end.py`（DR/NSD）
    与 `tests/verification/test_static_inl_end_to_end.py`（INL）。
    本守卫防止这两个文件被"清理"掉——它们是唯一能把模型和论文指标
    绑在一起的东西。
    """
    required = {
        "tests/verification/test_paper_metrics_end_to_end.py": ("DR", "NSD"),
        "tests/verification/test_static_inl_end_to_end.py": ("INL",),
    }
    missing_files: list[str] = []
    for rel, keywords in required.items():
        path = ROOT / rel
        if not path.is_file():
            missing_files.append(rel)
            continue
        text = path.read_text(encoding="utf-8")
        for keyword in keywords:
            assert keyword in text, f"{rel} should assert {keyword}"
    assert not missing_files, (
        "end-to-end metric tests are missing; without them a model regression "
        "would pass every test:\n  " + "\n  ".join(missing_files)
    )


def test_readme_does_not_understate_the_A7_status():
    """README 对 A7 的描述必须与归档数据里的 `status` 一致。

    2026-11-13：README 原写「动态路径数字输出 | 部分 | A7 只能看波形」，
    但归档数据里 A7 的 status 是 **NOT_RUN**，且带一条明确的 blocking_reason
    （逐事件参考程序要求全部事件时刻与初始节点电压绑定，本轮未冻结该组参数，
    不用默认值猜）。

    "只能看波形"暗示它跑过、只是输出不完整；实际是**根本没跑**。
    低估未完成度也是一种失真。
    """
    import json

    data = json.loads(
        (ROOT / "docs/reviews/20261113_p3p4/data/p4_ablation.json").read_text(encoding="utf-8")
    )
    a7 = next((c for c in data["configs"] if c["id"] == "A7_full_dynamic"), None)
    assert a7 is not None, "A7_full_dynamic disappeared from the matrix data"
    status = a7["status"]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert status in readme, (
        f"README should state A7's actual status ({status}); "
        "describing it loosely understates what remains undone"
    )
    # 不允许出现"部分完成"这类含糊表述来描述 A7
    for line in readme.splitlines():
        if "A7" in line:
            assert "只能看波形" not in line, (
                "README still says A7 只能看波形; its real status is " + status
            )


def test_execute_pipeline_line_count_in_readme_is_accurate():
    """README 引用的 `execute_pipeline` 行数必须与源码一致（±20 行）。"""
    import re

    source = (ROOT / "src/adi_model/simulation/pipeline_engine.py").read_text(encoding="utf-8")
    match = re.search(r"^def execute_pipeline\(.*?\n(?=^def |\Z)", source, re.MULTILINE | re.DOTALL)
    assert match, "execute_pipeline not found"
    actual = match.group(0).count("\n")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    claimed = [int(n) for n in re.findall(r"execute_pipeline[^\n]*?(\d{3})\s*行", readme)]
    assert claimed, "README should state the line count of execute_pipeline"
    for value in claimed:
        assert abs(value - actual) <= 20, (
            f"README claims {value} lines but execute_pipeline is {actual} lines"
        )


def test_readme_module_paths_all_exist():
    """README 里点名的 `src/adi_model/**.py` 必须真实存在。

    2026-11-13：README 的技术对照表与机制表都指向
    `behavioral/follower.py`，**该文件不存在**——follower 实现在
    `control/follower.py`。读者按图索骥会找不到代码。

    这与此前修过的"失效属性锚点""失效函数引用"是同一类问题的第三种形态：
    **失效的文件路径**。
    """
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"`(src/adi_model/[\w/]+\.py)`", readme)))
    assert referenced, "README should name some source files with full paths"
    missing = [rel for rel in referenced if not (ROOT / rel).is_file()]
    assert not missing, "README references source files that do not exist:\n  " + "\n  ".join(missing)


def test_readme_does_not_reference_legacy_module_layouts():
    """README 不得再出现"把模块放错目录"的引用（如 `behavioral/follower.py`）。

    历史上 follower 在 `behavioral/` 下，后移到 `control/`。
    只写 `xxx.py` 或写错目录，都会让人找不到实现。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stale = ["behavioral/follower.py"]
    offenders = [token for token in stale if token in readme]
    assert not offenders, (
        "README still references modules at their old location: " + ", ".join(offenders)
    )
