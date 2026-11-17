# =============================================================================
# 文件名     : check_engineering.py
# 模块名     : check_engineering
# 版本       : v1.0
# 功能       : 检查依赖边界、文档和脚本路径、发布图集与 ADC2 合同。
# 设计说明   :
#   * CI 可直接运行本工具。所有检查仅读取源码、合同和图集，使用 Python
#     标准库，因此临时检出时无需安装模型依赖或 EDA 工具。
#   * 检查结果附失败对象，全部检查结束后以退出码报告整体状态。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 图集按发布字节校验，ADC2 按现行 v21 的定点码制核对。
# =============================================================================
"""运行 `python tools/check_engineering.py`。任一检查失败时退出码为 1。"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from fractions import Fraction
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


# =============================================================================
# 3. 文档中的本仓相对链接
# =============================================================================
LINK = re.compile(r"!?\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s)]+)")
FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")


def check_doc_links() -> None:
    """核对当前文档的相对目标。历史记录按其运行时目录解释。"""
    from urllib.parse import unquote, urlsplit

    archived = {"_history", "_superseded", "_legacy", "archive"}
    sections = ("docs", "flow", "rtl", "src", "subsys", "tools", "references")
    paths = list(ROOT.glob("*.md"))
    for section in sections:
        paths.extend((ROOT / section).rglob("*.md"))
    broken = []
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if archived.intersection(relative.parts):
            continue
        # 代码段内的旧命令、正则或临时路径只是例子，不属于 Markdown 导航。
        # Markdown 允许四个反引号包含三引号，退出时检查围栏字符与长度。
        fence = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            marker = FENCE.match(line)
            if marker:
                current = marker[1]
                if not fence:
                    fence = current
                elif fence[0] == current[0] and len(current) >= len(fence):
                    fence = ""
                continue
            if fence:
                continue
            for token in LINK.findall(line):
                href = token.strip("<>")
                # 同页锚点和外部 URL 由网页渲染器处理。这项只验证工作树
                # 文件与目录。没有扩展名的普通文件也可能是有效链接（如 LICENSE）。
                target = urlsplit(href)
                if target.scheme or target.netloc or not target.path or target.path.startswith("/"):
                    continue
                # 旧接口表的位段说明 `[15:0](2)` 不表示文件路径。
                if re.fullmatch(r"[0-9]+(?:[.][.][0-9]+)?", target.path):
                    continue
                local = (path.parent / unquote(target.path)).resolve()
                if not local.exists():
                    broken.append(f"{relative} -> {target.path}")
    record("C3 documentation links", not broken,
           "all active relative links resolve" if not broken else "; ".join(broken[:6]))



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


# =============================================================================
# 4. 图集发布文件
# =============================================================================
FIGURE_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(png|svg)")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _unique_json_object(items):
    """拒绝重复键，避免后一个摘要覆盖清单中的另一个摘要。"""
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError(f"duplicate JSON key {name!r}")
        result[name] = value
    return result


def check_figure_assets() -> None:
    """按当前发布清单核对每幅图的 PNG/SVG 及文件字节哈希。

    source_files_sha256 记录出图时的源码身份。资产内容须与记录的摘要一致，
    当前代码迭代时也适用。结果通过 record 记录，缺失或损坏均定位到文件名。
    """
    check = "C4 figure assets"
    directory = ROOT / "docs/figures/20261117"
    try:
        manifest = json.loads(
            (directory / "figures.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
        if not isinstance(manifest, dict):
            raise ValueError("figure manifest must be an object")
        figures = manifest.get("figures")
        digests = manifest.get("figure_files_sha256")
        if not isinstance(figures, list) or not figures:
            raise ValueError("figure manifest has no figures")
        if not isinstance(digests, dict) or not digests:
            raise ValueError("figure manifest has no asset hashes")
        # 由图目推出两种格式的完整集合。仅遍历摘要表会漏检未登记的另一种格式。
        # 图目保留 PNG 文件名，SVG 复用其词干，以便目录与文档共享同一个图号。
        expected = set()
        for row in figures:
            name = row.get("figure") if isinstance(row, dict) else None
            # 只允许同目录的简单文件名，拒绝把目录外文件或软链接当作图集内容。
            if (
                not isinstance(name, str)
                or not FIGURE_FILE.fullmatch(name)
                or not name.endswith(".png")
            ):
                raise ValueError(f"invalid published PNG filename {name!r}")
            if name in expected:
                raise ValueError(f"duplicate published figure {name}")
            expected.update((name, str(Path(name).with_suffix(".svg"))))
        for name, digest in digests.items():
            if (
                not FIGURE_FILE.fullmatch(name)
                or not isinstance(digest, str)
                or not SHA256_HEX.fullmatch(digest)
            ):
                raise ValueError(f"invalid asset name or SHA-256 for {name!r}")
        if digests.keys() != expected:
            raise ValueError(
                f"manifest asset set differs: missing={sorted(expected - digests.keys())}, "
                f"unexpected={sorted(digests.keys() - expected)}"
            )
        # 对目录全集核对可发现迁移时遗留的图，文档与 JSON 等说明文件可同目录共存。
        present = {path.name for path in directory.iterdir() if path.suffix.lower() in (".png", ".svg")}
        if present != expected:
            raise ValueError(
                f"figure directory differs: missing={sorted(expected - present)}, "
                f"unexpected={sorted(present - expected)}"
            )
        for name in sorted(expected):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{name}: expected regular asset file")
            content = path.read_bytes()
            if not content:
                raise ValueError(f"{name}: expected a nonempty asset file")
            if hashlib.sha256(content).hexdigest() != digests[name]:
                raise ValueError(f"{name}: asset SHA-256 differs from figures.json")
    except (ValueError, OSError) as failure:
        record(check, False, str(failure))
        return
    record(check, True, f"{len(figures)} PNG/SVG pairs match recorded SHA-256")


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
    referenced = re.findall(r"(tools/[\w./-]+\.py|flow/verification/[\w./-]+\.py)", text)
    missing = sorted({r for r in referenced if not (ROOT / r).is_file()})
    core_entry = (ROOT / "src/adi_model/__main__.py").is_file()
    ok = not missing and core_entry
    record("C6 entry points", ok,
           f"{len(set(referenced))} referenced scripts exist, python -m adi_model present"
           if ok else f"missing={missing} main={core_entry}")


# =============================================================================
# 7. v21 的 ADC2 码制
# =============================================================================
def _single_match(pattern: str, text: str, field: str) -> re.Match:
    """只接受一条可识别定义；缺少或出现多条都不能猜测有效值。"""
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"expected one definition of {field}, found {len(matches)}")
    return matches[0]


def _rtl_source(relative: str) -> str:
    """去掉 SV 注释后只检查可执行的声明和换算表达式。"""
    text = (ROOT / relative).read_text(encoding="utf-8")
    return re.sub(r"//[^\n]*|/\*.*?\*/", " ", text, flags=re.DOTALL)


def _adc2_model_default() -> tuple:
    """用 AST 读取 Python 的 ADC2 默认参数。

    返回位数、单位为 V 的两个窗口端点，以及端点策略。只接受类体上带类型
    的字面值，CI 在未安装模型依赖时也能判断定义是否缺失或出现歧义。
    """
    source = (CORE / "defaults.py").read_text(encoding="utf-8")
    classes = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "DefaultParameters"
    ]
    if len(classes) != 1:
        raise ValueError("cannot identify DefaultParameters in defaults.py")
    names = ("adc2_n_bits", "adc2_v_min", "adc2_v_max", "endpoint_policy")
    values = {}
    for node in classes[0].body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name in names:
                if name in values or node.value is None:
                    raise ValueError(f"DefaultParameters.{name} must have one explicit default")
                values[name] = ast.literal_eval(node.value)
    if values.keys() != set(names):
        raise ValueError(f"missing ADC2 defaults: {sorted(set(names) - values.keys())}")
    return tuple(values[name] for name in names)


def check_adc2_contract() -> None:
    """静态核对 ADC2 的位宽、半开电压窗及 Q24 码中心。

    合同电压单位为 V。Python 和 RTL 的值转换成同一表示后逐项比较，
    发现首处偏差即记录 FAIL。声明和表达式需满足 v21 的静态取值形式。
    """
    check = "C7 ADC2 contract"
    try:
        text = (ROOT / "docs/CONTRACT_CURRENT.md").read_text(encoding="utf-8")
        row = _single_match(r"^\|\s*ADC2\s*\|\s*([^\n|]+)\|", text, "ADC2 contract row")[1]
        bits = int(_single_match(r"`(\d+)\s+bit`", row, "ADC2 contract bit count")[1])
        voltage = r"([+-]?\d+(?:\.\d+)?)"
        window = _single_match(
            rf"`\[\s*{voltage}\s*,\s*{voltage}\s*\)\s*V`",
            row, "ADC2 half-open contract window",
        )
        lo, hi = Fraction(window[1]), Fraction(window[2])
        span = Fraction(_single_match(rf"跨度\s*`{voltage}\s*V`", row, "ADC2 contract span")[1])
        if (bits, lo, hi, span) != (13, Fraction(-3, 16), Fraction(9, 16), Fraction(3, 4)):
            raise ValueError(f"v21 requires 13 bits and [-0.1875,+0.5625) V, got {bits}, [{lo},{hi}), span={span}")

        # Fraction 保留从文档十进制端点到整数 Q24 的精确关系。
        # 窗口宽度决定台阶，窗口左端和半台阶共同决定零码对应的电压。
        q24_scale = 1 << 24
        step_q24 = span * q24_scale / (1 << bits)
        center_q24 = (lo * q24_scale, step_q24, step_q24 / 2)
        documented_center = _single_match(
            r"`Y\s*=\s*(-?\d+)\s*\+\s*(\d+)\s*·\s*raw\s*\+\s*(\d+)`",
            row, "ADC2 contract Q24 code center",
        )
        if tuple(map(int, documented_center.groups())) != center_q24:
            raise ValueError("ADC2 contract Q24 code center does not match the voltage window")

        defaults = _adc2_model_default()
        if defaults != (bits, lo, hi, "half_open"):
            raise ValueError(f"DefaultParameters ADC2 differs from the contract: {defaults!r}")

        top = _rtl_source("rtl/v21/adc_digital_ip_v21.sv")
        port = _single_match(
            r"\binput\s+logic\s+\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+adc2_raw_i\b",
            top, "v21 ADC2 input port",
        )
        if (int(port[1]), int(port[2])) != (bits - 1, 0):
            raise ValueError(f"v21 adc2_raw_i is [{port[1]}:{port[2]}], expected [{bits - 1}:0]")

        # 顶层数字输入只携带原始码。编码器中的换算斜率与码域一起定义电压窗，
        # 所以要核对这两处实现，才能发现位宽正确而模拟窗口已经漂移的情况。
        # 捕获已知定点字面量的宽度，防止数值看似相同但解析为更短宽度时被截断。
        macro = _rtl_source("rtl/v21/adc2_node_macro.sv")
        macro_bits = int(_single_match(
            r"\bparameter\s+int\s+unsigned\s+ADC2_N\s*=\s*(\d+)\b", macro, "ADC2_N",
        )[1])
        ymin = _single_match(r"\bYMIN_Q24\s*=\s*(-?)\s*64'sd(\d+)\s*;", macro, "YMIN_Q24")
        multiplier = int(_single_match(
            r"\bnum\s*=\s*\(\$signed\(y_wide\)\s*-\s*YMIN_Q24\)\s*\*\s*96'sd(\d+)\s*;",
            macro, "ADC2 encoder multiplier",
        )[1])
        divisor = int(_single_match(
            r"\bcode_calc\s*=\s*num\s*/\s*96'sd(\d+)\s*;",
            macro, "ADC2 encoder divisor",
        )[1])
        shift = int(_single_match(
            r"\bcode_floor\s*=\s*code_calc\s*>>>\s*(\d+)\s*;", macro, "ADC2 encoder Q shift",
        )[1])
        limits = _single_match(
            r"\bin_range\s*=\s*\(\s*code_floor\s*>=\s*96'sd(\d+)\s*\)\s*&&\s*"
            r"\(\s*code_floor\s*<=\s*96'sd(\d+)\s*\)\s*;", macro, "ADC2 raw code limits",
        )
        # 编码窗跨 2^bits 个量化台阶。窗口右端对应码 2^bits，因超出满码而被拒绝。
        # 用有符号常量和算术右移核对有效码区间，保留负输入时的向下取整语义。
        if (
            macro_bits != bits or shift != 24 or multiplier == 0 or divisor == 0
            or Fraction(int(ymin[1] + ymin[2]), q24_scale) != lo
            or Fraction((1 << bits) * divisor, multiplier) != span
            or (int(limits[1]), int(limits[2])) != (0, (1 << bits) - 1)
        ):
            raise ValueError("v21 ADC2 node encoder does not match the 13-bit half-open contract")

        recon = _rtl_source("rtl/v21/recon_v21.sv")
        center = _single_match(
            r"\by\s*=\s*(-?)\s*32'sd(\d+)\s*\+\s*32'sd(\d+)\s*\+\s*"
            r"\(\$signed\(\{\s*(\d+)'d0\s*,\s*adc2_raw\s*\}\)\s*\*\s*32'sd(\d+)\)\s*;",
            recon, "v21 reconstruction ADC2 Q24 code center",
        )
        # 重建取码中心，码 0 需要与量化窗左端相差半个台阶。补零后做有符号乘法，
        # 即使原码的最高位置 1，码值也仍为非负数。
        reconstructed_center = (int(center[1] + center[2]), int(center[5]), int(center[3]))
        if reconstructed_center != center_q24 or int(center[4]) != 32 - bits:
            raise ValueError("v21 reconstruction Q24 code center does not match the ADC2 contract")
    except (OSError, SyntaxError, ValueError, TypeError, OverflowError) as failure:
        record(check, False, str(failure))
        return
    record(check, True, "v21 RTL and Python defaults agree: 13 bits, [-0.1875,+0.5625) V")


def main() -> int:
    check_dependency_direction()
    check_caaz_isolation()
    check_doc_links()
    check_figure_assets()
    check_duplicate_formulas()
    check_entry_points()
    check_adc2_contract()
    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
        failures += 0 if ok else 1
    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
