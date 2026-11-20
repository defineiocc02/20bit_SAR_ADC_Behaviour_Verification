#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_delivery.py
# 模块名     : 外围交付完整性校验
# 版本       : 随交付提交
# 功能       : 在 Git 检出或独立解压包中核对同一份 SHA-256 清单。
# 设计说明   : 三个本地审阅附件含自备原图，公开检出可缺省，有文件必校验。
# 变更记录   : 首次支持源码交付与包含自备材料的完整桌面包。
# =============================================================================
"""在任意工作目录执行；--all-assets 要求三个本地附件也齐备。"""
import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evidence/manifest.sha256"
LOCAL_ASSETS = {"report/ipbrain_reference.png", "report/handoff.pdf", "figures/floorplan.pdf"}


# =============================================================================
# 1. 检查每个已登记文件，明确返回缺失附件的数量
# =============================================================================
def check(all_assets):
    seen = set()
    skipped = []
    for number, line in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ValueError(f"第 {number} 行格式错误")
        digest, name = match.groups()
        path = ROOT / name
        # 所有条目由交付目录下的规范相对路径标识，重复或越界均属清单错误。
        if name in seen or name != Path(name).as_posix() or Path(name).is_absolute() or path.resolve() != path or ROOT not in path.parents:
            raise ValueError(f"重复或非规范路径 {name}")
        seen.add(name)
        if not path.is_file() and name in LOCAL_ASSETS and not all_assets:
            skipped.append(name)
            continue
        # 公开模式也校验已经存在的附件，防止旧 PDF 被误认成本轮审阅稿。
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"文件缺失或内容不同 {name}")
    if not (LOCAL_ASSETS | {"README.md", "calibration/system_cal.py", "verify/check_delivery.py"}) <= seen:
        raise ValueError("清单缺少交付入口或已登记的本地附件")
    print(f"PASS checked={len(seen) - len(skipped)} optional_absent={len(skipped)}")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--all-assets", action="store_true", help="要求完整桌面包中的每个附件")
    try:
        check(cli.parse_args().all_assets)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
