# =============================================================================
# 文件名     : inventory_gate.py
# 版本       : v1.0
# 功能       : 核对来源身份、代码引用和清单中的必要状态。
# 设计说明   :
#   * 来源标识和 SHA-256 具有不同职责。名称用于关联机制，同字节才可替代原件。
#   * 引用是否可定位、必需状态是否就绪是两个维度，分别记录，不相互代替。
#   * 只读原件并静态解析代码。离线缺资料使用 BLOCKED，不能伪装为已复核。
#   * 只在指明输出路径时创建摘要。已存在的证据禁止覆盖，保证重复运行有独立身份。
# 变更记录   : 接入验证目录中的相对清单位置，并解析已归档的历史路径。
# =============================================================================
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

# 清单单独归于验证流程。旧仓库快照按整对清单读取，避免新旧字段混用。
MANIFEST_DIR = Path("flow/verification/manifests")
SOURCES_FILE = str(MANIFEST_DIR / "sources_manifest.json")
INVENTORY_FILE = str(MANIFEST_DIR / "mechanism_inventory.json")
REFERENCES_FILE = "references/source_manifest.json"
# 冻结清单的原始字符串仍是证据的一部分。只对已知移位的首层目录重定位。
MOVED_REFERENCE_ROOTS = {
    "tests": Path("flow/tests"),
    "sim": Path("flow/sim"),
    "extensions": Path("subsys/extensions"),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEST_ID_RE = re.compile(r"^(?P<path>(?:(?:flow/)?tests|subsys/extensions/caaz/tests)/[\w/]+\.py)::(?P<symbol>\w+)$")
RTL_CONTRACT_STATUSES = (
    "DIGITAL_IMPLEMENTED",
    "CONTROL_ONLY",
    "NOT_IMPLEMENTED",
    "NOT_APPLICABLE",
)


# =============================================================================
# 1. 结果和范围
# =============================================================================
# 门禁每遇到一项事实追加一行。静态身份、原始资料可用性和必要状态可以独立追溯。
# FAIL 表示有反证或记录格式错误，BLOCKED 表示证据缺项。二者不能合并为相同原因。
# 未执行的阶段依自身范围排除。GateResult 的统计只计登记行，不代替实际运行。
# 记录顺序保留输入次序，损坏字段的报错可沿相同机制 ID 找回原条目。
# 需要额外校验状态的调用方使用 required_states_ready，而不是只读 PASS 总数。
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


# =============================================================================
# 2. 输入读入
# =============================================================================
# 先限制输入体积再接触解析器，避免损坏或错误选择的文件消耗无限内存。
# 用对象钩子检查重复键，防止后一个同名字段覆盖前一个来源或状态字段。
# 清单的实数必须有界。Python 默认接受的 NaN 和无穷表示不能写进严格 JSON。
# 读取成功还需验证根类型。后续检查器依赖字段映射，不接受数组的隐式重解释。
# 解码或结构错误由调用方归入具体的 sources、inventory 或 references 记录。
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


# =============================================================================
# 3. 本地资料查找
# =============================================================================
# 首先查显式环境覆盖，再查清单的默认目录，支持在不同主机按同身份复验。
# 旧清单把 location_rule 放在 meta 内，有顶层规则时以顶层为准，避免默认覆盖显式选择。
# 相对默认位置以仓库根为基准。规则仅提供候选目录，不更改来源的登记名称。
# 目录是否存在由逐文件查找判断，此处不创建目录、下载或复制原件。
# 外部资料目录只用于原件定位。代码和测试引用仍由仓库内相对路径检查。
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


# =============================================================================
# 4. 随库资料的身份
# =============================================================================
# ReferenceAsset 只装已经核查通过的资料。声明过但身份不符的条目不得进入替代索引。
# 原始资料的大小使用字节，页数为登记值。门禁不以读到 PDF 开头代替页数或技术核查。
# 按 source_id 查来源身份。按 SHA 比较同内容别名，这两张表承担不同约束。
# 把已证明的资产类型单独保存，防止后续算法把来源清单里的未核声明当真值。
@dataclass(frozen=True)
class ReferenceAsset:
    """已核仓库PDF字节身份及登记页数；不表示本工具阅读了文献。"""

    source_id: str
    path: Path
    sha256: str
    pages: int
    size_bytes: int


# =============================================================================
# 5. 随库原件检查
# =============================================================================
# 先限制入口版本及条目类型，拒绝把任意路径清单当作参考 PDF 的专用格式。
# 编码规范限制在 references 下且为 PDF。真实路径再做解析，防止符号链接绕过目录界限。
# 空 ID 与重复 ID 单独报错。重复路径也报错，不能用一个文件填满两个资料槽位。
# 记录起点隔离当前条目。其他条目失败不应阻止核对本条目，但本条目有错就不缓存。
# 去重前核对长度和完整 SHA。不能只用同名文件、部分哈希或宣称页数判等。
# documents_count 和总页数是清点口径，不是实际 PDF 的逐页技术阅读结果。
# 文件超限时跳过读取并留下失败记录，避免构建或 CI 在误放大文件时卡住。
# 类型判断区分 bool 与 int，JSON true 不能充当页数或字节大小。
# 遇到损坏或不可读文件由外层保留路径关联结果，不写入任何原始清单。
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


# =============================================================================
# 6. 主来源和背景来源的追踪
# =============================================================================
# 登记来源 ID 先于查找文件。原件未就绪时，机制仍可引用其明确的登记身份。
# 背景条目允许明确声明哈希未登记。它输出 BLOCKED，不加入已经核实的原件集合。
# 随库原件与主来源相同 ID 时先查登记冲突。即使找到外部文件，也不能隐藏这个冲突。
# 找不到本地文件时可使用已经逐字节核过的同 SHA 资产。替代对象必须一致完整。
# 同 SHA 却登记不同页数时保留冲突。字节相同并不自动修正手写元数据。
# 来源文件字段只接受文件名；从目录查找时禁止条目自己指定目录或绝对路径。
# 优先检查实际找到的原件。若它的哈希错误，不能用同 SHA 替代物把错误盖掉。
# 资料体积超限时不尝试哈希，明确记为当前环境未执行该校验。
# resolve_dirs 只负责候选位置，来源验证和状态记录在此完成，不改变候选目录。
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



# =============================================================================
# 7. 汇总登记与建立索引
# =============================================================================
# 把主体和背景记录保留各自类别。背景条目的登记豁免不作用于主要来源。
# 所有资料成员按来源 ID 汇入机制核查。清单缺失和本地资料缺失的级别不相同。
# 随库登记单独形成哈希索引。后续允许比较字节身份，不把附带文件名当推断依据。
# 登记的文献规模由原清单记录。路径迁移不重新声明哪些原文已经阅读。
# 此步骤只返回登记身份。来源哈希结果留在 result，后续引用检查不重写其状态。
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


# =============================================================================
# 8. 仓库相对路径及旧证据
# =============================================================================
# 本函数服务代码、测试、RTL 及随库资产的共同路径边界，供调用方先验类型检查。
# 现存合法原路径优先。外部快照可以保留旧树，不会被跳到当前仓库文件。
# 兼容仅覆盖已知目录迁移。未知路径继续失败，避免全仓按 basename 猜测证据。
# 无论直接路径或兼容路径，都以解析后的真实位置判断仓库边界，禁止逃出仓库。
# 兼容时拒绝带父级跳转的输入，以免通过新的目录深度改变原引用的意思。
def _repo_file(repo_root: Path, relative_path: str) -> Path | None:
    """解析可移植的仓库相对文件路径，拒绝越出仓库。"""
    path = Path(relative_path)
    if path.is_absolute():
        return None
    root = repo_root.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        return None
    if resolved.is_file():
        return resolved
    # 旧身份仅在原路径确实缺失时解析到迁移目的地，不覆盖调用方提供的文件。
    if not path.parts or ".." in path.parts:
        return None
    destination = MOVED_REFERENCE_ROOTS.get(path.parts[0])
    if destination is None:
        return None
    relocated = (root / destination.joinpath(*path.parts[1:])).resolve()
    return relocated if relocated.is_relative_to(root) and relocated.is_file() else None


# =============================================================================
# 9. 代码锚点和实现状态
# =============================================================================
# 仅用 AST 提取声明，清单的检查不会 import 测试，因而不会执行仿真或生成向量。
# 仅承认文件顶层函数和类。内部函数或注释中出现相同名字不能证明公开测试入口。
# 模块静态可定位与 RTL 完成状态分开判断。登记的控制级别须在显式的状态集合内。
# 目前只验证引用和声明有无，不根据符号名、端口长度或注释自动推导功能等价。
# AST 解析失败由验证条目关联到原路径，方便修复语法而不是错误重写清单。
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


# =============================================================================
# 10. 机制与验收引用
# =============================================================================
# 先识别必需任务集合及机制 ID，再检查每条记录，避免按一张固定的任务号表猜覆盖。
# 只接受显式条件下的任务映射。一个 optional 机制不能填补必需项的验收。
# 引用检查只确认文件及顶层符号能定位。测试存在不等价于此次执行过该测试。
# 同一测试文件可被多个机制引用，缓存声明集合以避免多次解析造成代价。
# 状态判断使用清单的登记枚举，过时的自由文本不能被默认为成功。
# 必需项缺自己的执行结果时保留未就绪。一个其他机制的 PASS 不填补本项缺口。
# 遇到格式错误继续检查其余独立机制，便于一次给出完整整理清单。
# 已定位的来源和任务按 ID 比较。机制名称相似不能证明两者身份一致。
# 静态引用、实验登记和必需任务覆盖分别发布，报告阅读者可以定位失败层。
# 根目录清单移动不改该机制文件的哈希。旧路径由统一解析器处理，不逐条改史料。
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


# =============================================================================
# 11. 汇合单次门禁结果
# =============================================================================
# 优先决定整套登记目录，避免部分文件从旧目录读、另一半从新目录读。
# 三个输入失效分别记录。当一个清单无法读时，其余清单仍能给独立的诊断结果。
# 先身份核查，再解析机制的来源引用。顺序明确才能区分来源缺口与实现缺口。
# 本函数只读证据并返回结果，不调整通过记录，也不自动覆盖冻结的证据文件。
def verify(repo_root: Path) -> GateResult:
    """核对根来源、随库原件及机制引用；不修改历史20/641阅读登记。"""
    result = GateResult()
    paths = {}
    # 只有整个新目录缺席时才接受旧快照布局。新目录存在但少文件应当报错。
    manifest_dir = MANIFEST_DIR if (repo_root / MANIFEST_DIR).exists() else Path("manifest")
    for label, relative in (
        ("sources", str(manifest_dir / Path(SOURCES_FILE).name)),
        ("inventory", str(manifest_dir / Path(INVENTORY_FILE).name)),
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


# =============================================================================
# 12. 摘要与 CLI
# =============================================================================
# 摘要同时保留逐项记录和总量。下游可过滤未就绪的必需记录，而不需解析终端文案。
# 输出字段显示验证范围以及未执行的功能／EDA 环节，避免把静态验证升级成行为结果。
# 写入摘要使用同目录临时文件和排他建目标。已存在的摘要属于别的运行，必须报错。
# 写入异常时临时文件清理由该函数负责。调用方不应据残留的路径推定验证通过。
# 退出码分列静态错误和输入未就绪。自动化消费者可为两类情况分别提供反馈。
# 只打印既有登记和检查结论。CLI 不会为了获得 PASS 调用网络或更改本地原件。
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
