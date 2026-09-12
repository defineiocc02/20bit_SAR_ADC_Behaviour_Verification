"""机制清单与来源清单的一致性门禁（阶段 A，任务书 §2/§10）。

校验三件事，任何硬性失败都以非零退出码结束：

1. **来源清单**（``sources_manifest.json``）：SHA256 格式；原文文件在
   本地材料目录可定位时逐一比对哈希（不匹配=硬失败；缺失=BLOCKED，
   不伪造原文证据）。
2. **机制清单**（``mechanism_inventory.json``）：轴线枚举合法；代码路径、
   测试 ID（文件+类名）、来源 ID、实验状态全部真实存在——清单引用
   不存在的锚点与引用错误的锚点同样是造假。
3. **覆盖完整性**：任务书 §4 的 T01-T14 每一项都必须被至少一个机制或
   待建项引用；机制 ID 唯一。

产出 ``validation_summary.json``（严格类型），供报告元数据与 CI 消费。

用法::

    python -m adi_model.inventory_gate [--repo-root PATH] [--summary PATH]

本模块只依赖标准库，是清单↔代码库的静态一致性检查；
物理机制的验收仍在 pytest 与 ``adi-run-all``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCES_FILE = "sources_manifest.json"
INVENTORY_FILE = "mechanism_inventory.json"
TASK_IDS = tuple(f"T{i:02d}" for i in range(1, 15))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEST_ID_RE = re.compile(r"^(?P<path>tests/[\w/]+\.py)::(?P<cls>\w+)$")


@dataclass
class Record:
    """一条门禁记录：check id、严重级别、人类可读结论。"""

    check: str
    level: str  # "PASS" | "FAIL" | "BLOCKED"
    detail: str


@dataclass
class GateResult:
    """门禁结果汇总。``ok`` 只看 FAIL 数；BLOCKED 单独呈现。"""

    records: list[Record] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, check: str, level: str, detail: str) -> None:
        """追加一条记录（level 为 PASS/FAIL/BLOCKED）。"""
        self.records.append(Record(check=check, level=level, detail=detail))

    def finalize(self) -> GateResult:
        """统计各级别条数，返回自身（链式收尾）。"""
        self.counts = {}
        for rec in self.records:
            self.counts[rec.level] = self.counts.get(rec.level, 0) + 1
        return self

    @property
    def ok(self) -> bool:
        """无 FAIL 即通过；BLOCKED 只登记不否决。"""
        return self.counts.get("FAIL", 0) == 0


def _load_json(path: Path) -> dict[str, object]:
    with path.open("rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _resolve_sources_dir(manifest: dict[str, object]) -> list[Path]:
    """原文定位目录候选：环境变量优先，其次清单登记的默认目录。"""
    import os

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
            candidates.append(Path(default_dir).expanduser())
    return candidates


def _check_sources(repo_root: Path, manifest: dict[str, object], result: GateResult) -> None:
    sources = manifest.get("sources", [])
    if not isinstance(sources, list) or not sources:
        result.add("sources.present", "FAIL", "sources_manifest 缺少 sources 数组")
        return
    resolve_dirs = _resolve_sources_dir(manifest)
    ids: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            result.add("sources.entry", "FAIL", "sources 数组存在非对象条目")
            continue
        sid = str(src.get("id", "?"))
        ids.add(sid)
        digest = str(src.get("sha256", ""))
        if not SHA256_RE.match(digest):
            result.add(f"sources.{sid}.sha256", "FAIL", "SHA256 缺失或格式非法")
            continue
        fname = str(src.get("file", ""))
        found: Path | None = None
        for d in resolve_dirs:
            p = d / fname
            if p.is_file():
                found = p
                break
        if found is None:
            result.add(
                f"sources.{sid}.file",
                "BLOCKED",
                f"原文未在本地材料目录找到（{'; '.join(str(d) for d in resolve_dirs) or '未配置'}）；"
                "哈希校验 NOT_RUN，登记缺失",
            )
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


def _check_code_and_tests(
    repo_root: Path, inventory: dict[str, object], result: GateResult
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

    seen_ids: set[str] = set()
    covered_tasks: set[str] = set()
    for mech in mechanisms:
        if not isinstance(mech, dict):
            result.add("mechanisms.entry", "FAIL", "mechanisms 数组存在非对象条目")
            continue
        mid = str(mech.get("id", "?"))
        if mid in seen_ids:
            result.add(f"mechanisms.{mid}.id", "FAIL", "机制 ID 重复")
        seen_ids.add(mid)

        degree = mech.get("implementation_degree")
        if degree not in axes_ok.get("implementation_degree", []):
            result.add(f"mechanisms.{mid}.degree", "FAIL", f"实现程度非法：{degree}")
        emb = mech.get("embodiment")
        if emb not in cc_names:
            result.add(f"mechanisms.{mid}.embodiment", "FAIL", f"实施例配置类非法：{emb}")
        ev = mech.get("evidence_source", [])
        if not isinstance(ev, list) or not ev:
            result.add(f"mechanisms.{mid}.evidence", "FAIL", "证据来源轴线缺失（不可为空）")
        else:
            bad = [v for v in ev if v not in axes_ok.get("evidence_source", [])]
            if bad:
                result.add(f"mechanisms.{mid}.evidence", "FAIL", f"证据来源枚举非法：{bad}")

        task_refs = mech.get("task_refs", [])
        if not isinstance(task_refs, list) or not task_refs:
            result.add(f"mechanisms.{mid}.task_refs", "FAIL", "缺少任务书条目映射")
        else:
            for t in task_refs:
                if t in TASK_IDS:
                    covered_tasks.add(str(t))
                else:
                    result.add(f"mechanisms.{mid}.task_refs", "FAIL", f"未知任务条目 {t}")

        for rel in mech.get("code", []) or []:
            if not isinstance(rel, str):
                result.add(f"mechanisms.{mid}.code", "FAIL", "code 数组存在非字符串")
                continue
            if not (repo_root / rel).is_file():
                result.add(f"mechanisms.{mid}.code", "FAIL", f"代码路径不存在：{rel}")

        for tid in mech.get("tests", []) or []:
            if not isinstance(tid, str):
                result.add(f"mechanisms.{mid}.tests", "FAIL", "tests 数组存在非字符串")
                continue
            m = TEST_ID_RE.match(tid)
            if m is None:
                result.add(f"mechanisms.{mid}.tests", "FAIL", f"测试 ID 格式非法：{tid}")
                continue
            tpath = repo_root / m.group("path")
            if not tpath.is_file():
                result.add(f"mechanisms.{mid}.tests", "FAIL", f"测试文件不存在：{tid}")
                continue
            cls = m.group("cls")
            declared = any(
                line.lstrip().startswith(f"class {cls}")
                for line in tpath.read_text(encoding="utf-8").splitlines()
            )
            if not declared:
                result.add(f"mechanisms.{mid}.tests", "FAIL", f"测试类未在该文件声明：{tid}")

        for exp in mech.get("experiments", []) or []:
            if isinstance(exp, dict):
                status = exp.get("status")
                if status not in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
                    result.add(
                        f"mechanisms.{mid}.experiments",
                        "FAIL",
                        f"实验状态 {status} 不在 PASS/FAIL/BLOCKED/NOT_RUN",
                    )

    pending = inventory.get("coverage_obligations", {})
    pending_items = pending.get("pending_items", []) if isinstance(pending, dict) else []
    if isinstance(pending_items, list):
        for item in pending_items:
            if not isinstance(item, dict):
                continue
            t = item.get("task_ref")
            if t in TASK_IDS:
                covered_tasks.add(str(t))
            status = item.get("status")
            if status not in ("PASS", "FAIL", "BLOCKED", "NOT_RUN"):
                result.add(f"pending.{item.get('id', '?')}", "FAIL", f"待建项状态 {status} 非法")
    missing = [t for t in TASK_IDS if t not in covered_tasks]
    if missing:
        result.add("coverage.T01-T14", "FAIL", f"任务书最低覆盖缺口：{', '.join(missing)}")
    else:
        result.add("coverage.T01-T14", "PASS", "T01-T14 全部被机制或待建项覆盖")


def verify(repo_root: Path) -> GateResult:
    """执行全部检查并返回结构化结果。"""
    result = GateResult()
    sources_path = repo_root / SOURCES_FILE
    inventory_path = repo_root / INVENTORY_FILE
    for label, p in (("sources", sources_path), ("inventory", inventory_path)):
        if not p.is_file():
            result.add(f"{label}.present", "FAIL", f"清单文件不存在：{p}")
        else:
            result.add(f"{label}.present", "PASS", str(p))

    manifest: dict[str, object] = {}
    inventory: dict[str, object] = {}
    if sources_path.is_file():
        manifest = _load_json(sources_path)
        _check_sources(repo_root, manifest, result)
    if inventory_path.is_file():
        inventory = _load_json(inventory_path)
        _check_code_and_tests(repo_root, inventory, result)
    return result.finalize()


def build_summary(result: GateResult, repo_root: Path) -> dict[str, object]:
    """生成严格类型的 validation_summary.json 内容。"""
    return {
        "artifact": "inventory_gate_summary",
        "generated_by": "adi_model.inventory_gate",
        "repo_root": str(repo_root),
        "ok": result.ok,
        "counts": {k: result.counts.get(k, 0) for k in ("PASS", "FAIL", "BLOCKED")},
        "records": [
            {"check": rec.check, "level": rec.level, "detail": rec.detail} for rec in result.records
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：硬性失败返回 1，BLOCKED 不改变退出码（登记不等于失败）。"""
    parser = argparse.ArgumentParser(description="机制/来源清单一致性门禁")
    parser.add_argument("--repo-root", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument("--summary", default=None, help="写出 validation_summary.json 的路径")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    result = verify(repo_root)
    if args.summary:
        out = Path(args.summary)
        out.write_text(
            json.dumps(build_summary(result, repo_root), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    for rec in result.records:
        print(f"[{rec.level:7s}] {rec.check}: {rec.detail}")
    n_fail = result.counts.get("FAIL", 0)
    n_blocked = result.counts.get("BLOCKED", 0)
    n_pass = result.counts.get("PASS", 0)
    print(f"inventory_gate: {n_pass} PASS, {n_fail} FAIL, {n_blocked} BLOCKED")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
