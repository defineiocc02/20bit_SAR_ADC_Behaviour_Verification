"""来源身份、代码引用和已登记状态的静态检查。

来源哈希/页数登记、仓库内引用和显式 required_task_ids 分别检查。
STATIC_IDENTITY_AND_REFERENCE 通过只表示登记有效；文献语义和实现等价始终标为 NOT_ASSESSED，
本工具不执行模型、测试或 EDA。必需条目的NOT_RUN/BLOCKED 状态单独报告，不能由引用存在替代。

CLI 退出码：0 为静态输入及必需登记状态完整，1 为静态登记错误，2 为必需状态/原文未就绪或输出错误。
--summary 原子创建新文件，拒绝覆盖。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

SOURCES_FILE = "sources_manifest.json"
INVENTORY_FILE = "mechanism_inventory.json"
REFERENCES_FILE = "references/source_manifest.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEST_ID_RE = re.compile(r"^(?P<path>tests/[\w/]+\.py)::(?P<symbol>\w+)$")
RTL_CONTRACT_STATUSES = (
    "DIGITAL_IMPLEMENTED",
    "CONTROL_ONLY",
    "NOT_IMPLEMENTED",
    "NOT_APPLICABLE",
)


@dataclass
class Record:
    """一条门禁记录：check id、严重级别、人类可读结论。"""

    check: str
    level: str  # "PASS" | "FAIL" | "BLOCKED"
    detail: str
    scope: str = "STATIC_IDENTITY_AND_REFERENCE"


@dataclass
class GateResult:
    """分列静态引用与登记的必需状态，不推断原文等价。"""

    records: list[Record] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    registered_source_ids: set[str] = field(default_factory=set)

    def add(
        self, check: str, level: str, detail: str, *, scope: str = "STATIC_IDENTITY_AND_REFERENCE"
    ) -> None:
        """追加静态记录或登记状态；本调用不会执行被引用的检查。"""
        self.records.append(Record(check=check, level=level, detail=detail, scope=scope))

    def finalize(self) -> GateResult:
        """统计各级别条数，返回自身（链式收尾）。"""
        self.counts = {}
        for rec in self.records:
            self.counts[rec.level] = self.counts.get(rec.level, 0) + 1
        return self

    @property
    def static_refs_pass(self) -> bool:
        """静态登记未发现 FAIL；BLOCKED 完整性由 summary/退出码另外报告。"""
        return not any(
            rec.level == "FAIL" and rec.scope == "STATIC_IDENTITY_AND_REFERENCE"
            for rec in self.records
        )

    @property
    def required_states_ready(self) -> bool:
        """必需状态登记均为 PASS；并不表示本工具验证了状态所述行为。"""
        return all(
            rec.level == "PASS" for rec in self.records if rec.scope == "REGISTERED_REQUIRED_STATE"
        )


def _load_json(path: Path) -> dict[str, object]:
    """读取有限数值、无重复键的 JSON 对象，不接受未迁移根格式。"""

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        values: dict[str, object] = {}
        for name, value in pairs:
            if name in values:
                raise ValueError(f"duplicate JSON key: {name}")
            values[name] = value
        return values

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"non-finite JSON number: {value}")
        return number

    def reject_constant(value: str) -> NoReturn:
        raise ValueError(f"non-finite JSON constant: {value}")

    if path.stat().st_size > 300 * 1024**2:
        raise ValueError("JSON file exceeds the 300 MiB file limit")
    values = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_float=finite_float,
        parse_constant=reject_constant,
    )
    if not isinstance(values, dict):
        raise ValueError("manifest JSON must be an object")
    return values


def _resolve_sources_dir(manifest: dict[str, object], repo_root: Path) -> list[Path]:
    """原文定位目录候选：环境变量优先，其次清单登记的默认目录。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("ADC_SOURCES_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    rule: dict[str, object] = {}
    raw = manifest.get("location_rule")
    if isinstance(raw, dict):
        rule = raw
    else:
        meta = manifest.get("meta")
        if isinstance(meta, dict):
            candidate = meta.get("location_rule")
            if isinstance(candidate, dict):
                rule = candidate
    if rule:
        default_dir = rule.get("default_dir", "")
        if isinstance(default_dir, str) and default_dir:
            default = Path(default_dir).expanduser()
            candidates.append(default if default.is_absolute() else repo_root / default)
    return candidates


@dataclass(frozen=True)
class ReferenceAsset:
    """已核仓库PDF字节身份及登记页数；不表示本工具阅读了文献。"""

    source_id: str
    path: Path
    sha256: str
    pages: int
    size_bytes: int


def _check_reference_assets(
    repo_root: Path, manifest: dict[str, object], result: GateResult
) -> tuple[set[str], dict[str, ReferenceAsset]]:
    """核对选定随库原件，返回声明UID及可作为精确SHA fallback的资产。"""
    ids: set[str] = set()
    assets: dict[str, ReferenceAsset] = {}
    documents = manifest.get("documents")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        result.add("references.schema", "FAIL", "references清单必须是schema_version1")
    if not isinstance(documents, list) or not documents:
        result.add("references.documents", "FAIL", "references清单必须登记非空documents数组")
        return ids, assets
    seen_paths: set[str] = set()
    pages_sum = 0
    for item in documents:
        if not isinstance(item, dict):
            result.add("references.entry", "FAIL", "references条目必须是对象")
            continue
        start = len(result.records)
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            result.add("references.id", "FAIL", "source_id必须是非空字符串")
            continue
        if source_id in ids:
            result.add(f"references.{source_id}.id", "FAIL", "references来源UID重复")
        ids.add(source_id)
        name, digest = item.get("target_relative_path"), item.get("sha256")
        pages, size_bytes = item.get("pages"), item.get("bytes")
        if type(pages) is not int or pages <= 0:
            result.add(f"references.{source_id}.pages", "FAIL", "PDF登记页数必须是正整数")
        else:
            pages_sum += pages
        if type(size_bytes) is not int or size_bytes <= 0:
            result.add(f"references.{source_id}.bytes", "FAIL", "PDF登记大小必须是正整数字节")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            result.add(f"references.{source_id}.sha256", "FAIL", "PDF完整SHA256缺失或非法")
        path = None
        if isinstance(name, str):
            relative = Path(name)
            if (
                not relative.is_absolute()
                and relative.parts
                and relative.parts[0] == "references"
                and ".." not in relative.parts
                and relative.suffix.lower() == ".pdf"
            ):
                path = _repo_file(repo_root, name)
                if path is not None and not path.is_relative_to(
                    (repo_root / "references").resolve()
                ):
                    path = None
            if name in seen_paths:
                result.add(f"references.{source_id}.file", "FAIL", "references资产路径重复")
            seen_paths.add(name)
        if path is None:
            result.add(
                f"references.{source_id}.file",
                "FAIL",
                f"PDF路径不存在、越界或不在references内：{name}",
            )
        elif path.stat().st_size > 300 * 1024**2:
            result.add(f"references.{source_id}.file", "FAIL", "PDF超过300MiB，未读取")
        elif not any(record.level == "FAIL" for record in result.records[start:]):
            data = path.read_bytes()
            if len(data) != size_bytes:
                result.add(f"references.{source_id}.bytes", "FAIL", "仓库PDF大小与清单不匹配")
            if hashlib.sha256(data).hexdigest() != digest:
                result.add(
                    f"references.{source_id}.sha256", "FAIL", "仓库PDF完整SHA256与清单不匹配"
                )
            if (
                isinstance(digest, str)
                and type(pages) is int
                and type(size_bytes) is int
                and not any(record.level == "FAIL" for record in result.records[start:])
            ):
                assets[source_id] = ReferenceAsset(source_id, path, digest, pages, size_bytes)
                result.add(
                    f"references.{source_id}.identity",
                    "PASS",
                    "路径/大小/SHA一致；页数为已登记元数据，本工具未解析PDF页或审读技术",
                )
    if type(manifest.get("documents_count")) is not int or manifest["documents_count"] != len(
        documents
    ):
        result.add("references.count", "FAIL", "documents_count必须与登记条数一致")
    if (
        type(manifest.get("pages_as_file_inventory")) is not int
        or manifest["pages_as_file_inventory"] != pages_sum
    ):
        result.add("references.pages_total", "FAIL", "pages_as_file_inventory必须与登记页数和一致")
    return ids, assets


def _verify_source_entries(
    entries: list[tuple[object, bool]],
    *,
    result: GateResult,
    reference_assets: dict[str, ReferenceAsset],
    reference_by_sha: dict[str, ReferenceAsset],
    resolve_dirs,
) -> set[str]:
    """逐条校验来源条目（ID 合法性、重复、哈希、体积），返回登记的 ID 集合。

    从 `_check_sources` 抽出：原函数 104 行里这段 for 占 74 行。
    本函数只读清单、只写 result；错误以 result.add(...) 累积，不抛异常。

    Args:
        entries: (条目, 是否背景来源) 的列表，由调用方拼好。
        result: 累积检查结果的容器；本函数只往里 add。
        reference_assets: 参考资产表（按 ID），用于判断来源与资产是否同 UID。
        reference_by_sha: 参考资产的 SHA256 索引，用于交叉引用。
        resolve_dirs: 解析来源目录的可调用对象。

    Returns:
        已登记来源的 ID 集合（含本地文件被阻断的条目）。

    Side effects: 写 result；读取本地文件以计算哈希（仅当路径解析成功时）。
    """
    ids: set[str] = set()
    for src, is_background in entries:
        if not isinstance(src, dict):
            result.add("sources.entry", "FAIL", "sources 数组存在非对象条目")
            continue
        sid = src.get("id")
        if not isinstance(sid, str) or not sid.strip():
            result.add("sources.id", "FAIL", "来源 ID 必须是非空字符串")
            continue
        if sid in ids:
            result.add("sources.duplicate_id", "FAIL", f"来源 id 重复：{sid}")
        ids.add(sid)
        if is_background and src.get("hash_registered") is False:
            result.add(
                f"sources.{sid}.registration",
                "BLOCKED",
                "背景来源未登记哈希；内容校验 NOT_RUN，来源声明不等于原文已复核",
            )
            continue
        digest = src.get("sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            result.add(f"sources.{sid}.sha256", "FAIL", "SHA256 缺失或格式非法")
            continue
        fname = src.get("file")
        if not isinstance(fname, str) or not fname.strip() or Path(fname).name != fname:
            result.add(
                f"sources.{sid}.file", "FAIL", "来源必须登记单一文件名，不能包含绝对路径或目录"
            )
            continue
        pages = src.get("pages")
        if type(pages) is not int or pages <= 0:
            result.add(f"sources.{sid}.pages", "FAIL", "来源页数必须是正整数登记值")
        same_uid = reference_assets.get(sid)
        if same_uid is not None and (digest != same_uid.sha256 or pages != same_uid.pages):
            result.add(
                f"sources.{sid}.reference_identity",
                "FAIL",
                "同UID的根来源与随库资产SHA/页数登记冲突；不改变UID语义",
            )
        same_bytes = reference_by_sha.get(digest)
        if same_bytes is not None and pages != same_bytes.pages:
            result.add(f"sources.{sid}.reference_pages", "FAIL", "同SHA来源的页数登记冲突")
        found: Path | None = None
        for d in resolve_dirs:
            p = d / fname
            if p.is_file():
                found = p
                break
        if found is None and same_bytes is not None:
            result.add(
                f"sources.{sid}.sha256",
                "PASS",
                f"本机原件缺失，使用已核随库资产{same_bytes.source_id}的相同完整SHA；保留原来源UID与登记语义，未推断技术已实现",
            )
            continue
        if found is None:
            result.add(
                f"sources.{sid}.file",
                "BLOCKED",
                f"原文未在本地材料目录找到（{'; '.join(str(d) for d in resolve_dirs) or '未配置'}）；"
                "哈希校验 NOT_RUN，登记缺失",
            )
            continue
        if found.stat().st_size > 300 * 1024**2:
            result.add(f"sources.{sid}.file", "BLOCKED", "来源超过300 MiB，未读取或分析")
            continue
        actual = hashlib.sha256(found.read_bytes()).hexdigest()
        if actual == digest:
            result.add(f"sources.{sid}.sha256", "PASS", f"{found} 哈希一致")
        else:
            result.add(
                f"sources.{sid}.sha256",
                "FAIL",
                f"{found} 哈希不一致：清单 {digest[:12]}… 实际 {actual[:12]}…",
            )
    return ids



def _check_sources(
    repo_root: Path,
    manifest: dict[str, object],
    result: GateResult,
    reference_assets: dict[str, ReferenceAsset] | None = None,
) -> set[str]:
    """检查已登记来源文件，返回可用于静态引用的 ID。

    未登记哈希的背景条目保持 BLOCKED。返回的 ID 标识声明，包括本地文件被阻断的条目；
    不认证原文内容或文献引用的含义。
    """
    reference_assets = {} if reference_assets is None else reference_assets
    reference_by_sha = {asset.sha256: asset for asset in reference_assets.values()}
    sources = manifest.get("sources", [])
    if not isinstance(sources, list) or not sources:
        result.add("sources.present", "FAIL", "sources_manifest 缺少 sources 数组")
        return set()
    entries = [(source, False) for source in sources]
    background = manifest.get("background_sources", {})
    if not isinstance(background, dict):
        result.add("sources.background", "FAIL", "background_sources 必须是对象")
    else:
        items = background.get("items", [])
        if not isinstance(items, list):
            result.add("sources.background", "FAIL", "background_sources.items 必须是数组")
        else:
            entries.extend((source, True) for source in items)
    resolve_dirs = _resolve_sources_dir(manifest, repo_root)
    ids: set[str] = set()
    return _verify_source_entries(
        entries,
        result=result,
        reference_assets=reference_assets,
        reference_by_sha=reference_by_sha,
        resolve_dirs=resolve_dirs,
    )
    return ids


def _repo_file(repo_root: Path, relative_path: str) -> Path | None:
    """解析可移植的仓库相对文件路径，拒绝越出仓库。"""
    path = Path(relative_path)
    if path.is_absolute():
        return None
    root = repo_root.resolve()
    resolved = (root / path).resolve()
    return resolved if resolved.is_relative_to(root) and resolved.is_file() else None


def _top_level_declarations(path: Path) -> set[str]:
    """读取 Python 顶层类和函数的确切名称，不导入代码。"""
    if path.stat().st_size > 300 * 1024**2:
        raise ValueError("Python reference exceeds the 300 MiB file limit")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _check_rtl_contract(
    repo_root: Path, mechanism_id: str, contract: object, result: GateResult
) -> None:
    """检查可选 RTL 状态与文件锚点，不断言其行为已获验证。"""
    check = f"mechanisms.{mechanism_id}.rtl_contract"
    if not isinstance(contract, dict):
        result.add(check, "FAIL", "rtl_contract 必须是对象")
        return
    status = contract.get("status")
    if status not in RTL_CONTRACT_STATUSES:
        result.add(check, "FAIL", f"RTL 完成状态非法：{status}")
    boundary = contract.get("boundary")
    if not isinstance(boundary, str) or not boundary.strip():
        result.add(check, "FAIL", "RTL 功能边界必须是非空字符串")
    for field_name in ("code", "tests"):
        references = contract.get(field_name, [])
        if not isinstance(references, list):
            result.add(check, "FAIL", f"RTL {field_name} 必须是数组")
            continue
        if status in ("DIGITAL_IMPLEMENTED", "CONTROL_ONLY") and not references:
            result.add(check, "FAIL", f"RTL 状态 {status} 必须有 {field_name} 引用")
        for reference in references:
            if not isinstance(reference, str) or _repo_file(repo_root, reference) is None:
                result.add(
                    check,
                    "FAIL",
                    f"RTL {field_name} 路径不在仓库内或文件不存在：{reference}",
                )


def _check_code_and_tests(
    repo_root: Path,
    inventory: dict[str, object],
    result: GateResult,
    source_ids: set[str],
) -> None:
    mechanisms = inventory.get("mechanisms", [])
    if not isinstance(mechanisms, list) or not mechanisms:
        result.add("mechanisms.present", "FAIL", "mechanism_inventory 缺少 mechanisms 数组")
        return
    axes = inventory.get("axes", {})
    axes_ok: dict[str, list[str]] = {}
    if isinstance(axes, dict):
        for name, spec in axes.items():
            if isinstance(spec, dict) and isinstance(spec.get("values"), list):
                axes_ok[str(name)] = [str(v) for v in spec["values"]]
    config_classes = inventory.get("config_classes", {})
    cc_names = set(config_classes) if isinstance(config_classes, dict) else set()

    obligations = inventory.get("coverage_obligations")
    required_task_ids: list[str] = []
    if not isinstance(obligations, dict):
        result.add("coverage.required_task_ids", "FAIL", "缺少 coverage_obligations；清单尚未迁移")
        obligations = {}
    raw_tasks = obligations.get("required_task_ids")
    if (
        not isinstance(raw_tasks, list)
        or not raw_tasks
        or any(not isinstance(task, str) or not task.strip() for task in raw_tasks)
        or len(set(raw_tasks)) != len(raw_tasks)
    ):
        result.add(
            "coverage.required_task_ids",
            "FAIL",
            "required_task_ids 必须显式登记非空唯一字符串数组；不回填旧T01-T14",
        )
    else:
        required_task_ids = raw_tasks
    seen_ids: set[str] = set()
    covered_tasks: set[str] = set()
    test_declarations: dict[Path, set[str]] = {}
    for mech in mechanisms:
        if not isinstance(mech, dict):
            result.add("mechanisms.entry", "FAIL", "mechanisms 数组存在非对象条目")
            continue
        record_start = len(result.records)
        mid = mech.get("id")
        if not isinstance(mid, str) or not mid.strip():
            result.add("mechanisms.id", "FAIL", "机制 ID 必须是非空字符串")
            continue
        if mid in seen_ids:
            result.add(f"mechanisms.{mid}.id", "FAIL", "机制 ID 重复")
        seen_ids.add(mid)
        required = mech.get("required", True)
        if not isinstance(required, bool):
            result.add(f"mechanisms.{mid}.required", "FAIL", "required 必须是布尔值")

        degree = mech.get("implementation_degree")
        if degree not in axes_ok.get("implementation_degree", []):
            result.add(f"mechanisms.{mid}.degree", "FAIL", f"实现程度非法：{degree}")
        emb = mech.get("embodiment")
        if not isinstance(emb, str) or emb not in cc_names:
            result.add(f"mechanisms.{mid}.embodiment", "FAIL", f"实施例配置类非法：{emb}")
        ev = mech.get("evidence_source", [])
        if not isinstance(ev, list) or not ev:
            result.add(f"mechanisms.{mid}.evidence", "FAIL", "证据来源轴线缺失（不可为空）")
        else:
            bad = [v for v in ev if v not in axes_ok.get("evidence_source", [])]
            if bad:
                result.add(f"mechanisms.{mid}.evidence", "FAIL", f"证据来源枚举非法：{bad}")
        nature = mech.get("verification_nature", [])
        if not isinstance(nature, list) or not nature:
            result.add(
                f"mechanisms.{mid}.verification_nature", "FAIL", "验证性质轴线缺失（不可为空）"
            )
        else:
            bad_nature = [
                value for value in nature if value not in axes_ok.get("verification_nature", [])
            ]
            if bad_nature:
                result.add(
                    f"mechanisms.{mid}.verification_nature",
                    "FAIL",
                    f"验证性质枚举非法：{bad_nature}",
                )

        source_refs = mech.get("sources", [])
        if not isinstance(source_refs, list):
            result.add(f"mechanisms.{mid}.sources", "FAIL", "sources 必须是数组")
        elif not source_refs and emb != "PROJECT_EXTENSION":
            result.add(f"mechanisms.{mid}.sources", "FAIL", "文献机制必须登记来源引用")
        else:
            for source_ref in source_refs:
                if not isinstance(source_ref, dict):
                    result.add(f"mechanisms.{mid}.sources", "FAIL", "来源引用必须是对象")
                    continue
                source_id = source_ref.get("id")
                if not isinstance(source_id, str) or source_id not in source_ids:
                    result.add(f"mechanisms.{mid}.sources", "FAIL", f"来源 ID 未登记：{source_id}")
                location = source_ref.get("location")
                if not isinstance(location, str) or not location.strip():
                    result.add(f"mechanisms.{mid}.sources", "FAIL", "来源位置必须是非空字符串")

        task_refs = mech.get("task_refs", [])
        if not isinstance(task_refs, list) or (required is True and not task_refs):
            result.add(f"mechanisms.{mid}.task_refs", "FAIL", "必需机制缺少显式条目映射")
        else:
            for t in task_refs:
                if t not in required_task_ids:
                    result.add(f"mechanisms.{mid}.task_refs", "FAIL", f"未知必需条目 {t}")
                elif required is False:
                    result.add(
                        f"mechanisms.{mid}.required", "FAIL", f"必需条目 {t} 不能以optional机制登记"
                    )
                else:
                    covered_tasks.add(str(t))

        code_refs = mech.get("code", [])
        if not isinstance(code_refs, list):
            result.add(f"mechanisms.{mid}.code", "FAIL", "code 必须是数组")
            code_refs = []
        for rel in code_refs:
            if not isinstance(rel, str):
                result.add(f"mechanisms.{mid}.code", "FAIL", "code 数组存在非字符串")
                continue
            if _repo_file(repo_root, rel) is None:
                result.add(
                    f"mechanisms.{mid}.code", "FAIL", f"代码路径不在仓库内或文件不存在：{rel}"
                )

        test_refs = mech.get("tests", [])
        if not isinstance(test_refs, list):
            result.add(f"mechanisms.{mid}.tests", "FAIL", "tests 必须是数组")
            test_refs = []
        for tid in test_refs:
            if not isinstance(tid, str):
                result.add(f"mechanisms.{mid}.tests", "FAIL", "tests 数组存在非字符串")
                continue
            m = TEST_ID_RE.match(tid)
            if m is None:
                result.add(f"mechanisms.{mid}.tests", "FAIL", f"测试 ID 格式非法：{tid}")
                continue
            tpath = _repo_file(repo_root, m.group("path"))
            if tpath is None:
                result.add(
                    f"mechanisms.{mid}.tests", "FAIL", f"测试路径不在仓库内或文件不存在：{tid}"
                )
                continue
            try:
                if tpath not in test_declarations:
                    test_declarations[tpath] = _top_level_declarations(tpath)
            except (OSError, SyntaxError, UnicodeError, ValueError) as error:
                result.add(
                    f"mechanisms.{mid}.tests", "FAIL", f"测试文件无法静态解析：{tid}；{error}"
                )
                continue
            if m.group("symbol") not in test_declarations[tpath]:
                result.add(
                    f"mechanisms.{mid}.tests", "FAIL", f"测试类或函数未在该文件顶层声明：{tid}"
                )

        if "rtl_contract" in mech:
            _check_rtl_contract(repo_root, mid, mech["rtl_contract"], result)

        experiments = mech.get("experiments", [])
        if not isinstance(experiments, list):
            result.add(f"mechanisms.{mid}.experiments", "FAIL", "experiments 必须是数组")
            experiments = []
        if required is True and not experiments:
            result.add(
                f"mechanisms.{mid}.required_state",
                "BLOCKED",
                "必需机制未登记执行证据状态；本工具未执行行为检查",
                scope="REGISTERED_REQUIRED_STATE",
            )
        for exp in experiments:
            if not isinstance(exp, dict):
                result.add(f"mechanisms.{mid}.experiments", "FAIL", "实验状态条目必须是对象")
                continue
            status = exp.get("status")
            if status not in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
                result.add(f"mechanisms.{mid}.experiments", "FAIL", f"实验状态非法：{status}")
            elif required is True:
                result.add(
                    f"mechanisms.{mid}.required_state",
                    "BLOCKED" if status == "NOT_RUN" else status,
                    f"登记状态 {status}；本工具未运行该实验或核实语义",
                    scope="REGISTERED_REQUIRED_STATE",
                )
        if not any(
            record.level == "FAIL" and record.scope == "STATIC_IDENTITY_AND_REFERENCE"
            for record in result.records[record_start:]
        ):
            result.add(
                f"mechanisms.{mid}.static_refs",
                "PASS",
                "来源 ID/位置、代码和测试锚点及枚举合法；仅静态引用检查，未执行测试或核验文献语义和物理机制",
            )

    pending_items = obligations.get("pending_items", [])
    if not isinstance(pending_items, list):
        result.add("coverage.pending_items", "FAIL", "pending_items 必须是数组")
        pending_items = []
    for item in pending_items:
        if not isinstance(item, dict):
            result.add("coverage.pending_items", "FAIL", "待建项必须是对象")
            continue
        check = f"pending.{item.get('id', '?')}"
        task = item.get("task_ref")
        if task not in required_task_ids:
            result.add(check, "FAIL", f"未知必需条目 {task}")
        else:
            covered_tasks.add(task)
        status = item.get("status")
        if status not in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
            result.add(check, "FAIL", f"待建项状态 {status} 非法")
        else:
            result.add(
                check,
                "BLOCKED" if status == "NOT_RUN" else status,
                f"必需待建项登记状态 {status}；覆盖登记不代表实现完成",
                scope="REGISTERED_REQUIRED_STATE",
            )
    missing = [task for task in required_task_ids if task not in covered_tasks]
    if missing:
        result.add("coverage.required_task_ids", "FAIL", f"必需条目登记缺口：{', '.join(missing)}")
    elif required_task_ids:
        result.add("coverage.required_task_ids", "PASS", "显式必需条目均有登记；不代表已实现或验证")


def verify(repo_root: Path) -> GateResult:
    """核对根来源、随库原件及机制引用；不修改历史20/641阅读登记。"""
    result = GateResult()
    paths = {}
    for label, relative in (
        ("sources", SOURCES_FILE),
        ("inventory", INVENTORY_FILE),
        ("references", REFERENCES_FILE),
    ):
        path = _repo_file(repo_root, relative)
        paths[label] = path
        result.add(
            f"{label}.present",
            "PASS" if path is not None else "FAIL",
            str(path) if path is not None else f"清单缺失或越界：{relative}",
        )
    source_ids: set[str] = set()
    assets: dict[str, ReferenceAsset] = {}
    if paths["references"] is not None:
        try:
            declared_ids, assets = _check_reference_assets(
                repo_root, _load_json(paths["references"]), result
            )
            source_ids.update(declared_ids)
        except (OSError, UnicodeError, ValueError) as exc:
            result.add("references.read", "FAIL", f"随库来源清单或资产无法检查：{exc}")
    if paths["sources"] is not None:
        try:
            source_ids.update(
                _check_sources(repo_root, _load_json(paths["sources"]), result, assets)
            )
        except (OSError, UnicodeError, ValueError) as exc:
            result.add("sources.read", "FAIL", f"根来源清单或文件无法检查：{exc}")
    result.registered_source_ids = source_ids
    if paths["inventory"] is not None:
        try:
            _check_code_and_tests(repo_root, _load_json(paths["inventory"]), result, source_ids)
        except (OSError, UnicodeError, ValueError) as exc:
            result.add("inventory.read", "FAIL", f"机制清单无法检查：{exc}")
    return result.finalize()


def build_summary(result: GateResult, repo_root: Path) -> dict[str, object]:
    """生成严格类型的 validation_summary.json 内容。"""
    return {
        "artifact": "inventory_gate_summary",
        "generated_by": "adi_model.inventory_gate",
        "registered_source_ids": sorted(result.registered_source_ids),
        "reference_page_check": "REGISTERED_METADATA_ONLY_NOT_PDF_READING",
        "repo_root": str(repo_root),
        "evidence_scope": "STATIC_IDENTITY_AND_REFERENCE_CHECK_ONLY",
        "source_conformance": "SOURCE_CONFORMANCE_NOT_ASSESSED",
        "model_tests_run": False,
        "rtl_or_eda_run": False,
        "static_refs_pass": result.static_refs_pass,
        "required_states_ready": result.required_states_ready,
        "static_inputs_complete": not any(
            rec.scope == "STATIC_IDENTITY_AND_REFERENCE" and rec.level != "PASS"
            for rec in result.records
        ),
        "unresolved_required_states": [
            {"check": rec.check, "level": rec.level, "detail": rec.detail}
            for rec in result.records
            if rec.scope == "REGISTERED_REQUIRED_STATE" and rec.level != "PASS"
        ],
        "counts": {k: result.counts.get(k, 0) for k in ("PASS", "FAIL", "BLOCKED")},
        "records": [
            {"check": rec.check, "level": rec.level, "detail": rec.detail, "scope": rec.scope}
            for rec in result.records
        ],
    }


def _write_new_summary(path: Path, summary: dict[str, object]) -> None:
    """先完成同目录临时文件，再原子链接新目标；拒绝覆盖文件或符号链接。"""
    content = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".inventory-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """静态错误退出1，未就绪/输出错误退出2；不会执行行为或EDA检查。"""
    parser = argparse.ArgumentParser(description="来源身份、引用和必需登记状态静态检查")
    parser.add_argument("--repo-root", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--summary", default=None, help="排他创建静态检查JSON，不覆盖现有路径")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    result = verify(repo_root)
    if args.summary:
        try:
            _write_new_summary(Path(args.summary), build_summary(result, repo_root))
        except OSError as exc:
            print(f"summary output error: {exc}", file=sys.stderr)
            return 2
    for rec in result.records:
        print(f"[{rec.level:7s}] {rec.scope} {rec.check}: {rec.detail}")
    print(
        f"inventory_gate: static_refs_pass={result.static_refs_pass}, "
        f"required_states_ready={result.required_states_ready}; "
        "SOURCE_CONFORMANCE_NOT_ASSESSED; model/RTL/EDA NOT_RUN"
    )
    if not result.static_refs_pass:
        return 1
    if any(rec.level != "PASS" for rec in result.records):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
