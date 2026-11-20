# =============================================================================
# 文件名     : check_ownership.py
# 模块名     : 转换内核、外部 DSP 和外围服务的静态交接核查
# 版本       : v1.0
# 功能       : 从冻结的 Git 对象及交付清单读取模块实例，输出可复核的归属。
# 设计说明   : 只计已知模块的语法实例。参数和 generate 分支以源层次呈现；
#              不推断门数、面积或硅上实现，也不运行或写回功能组工程。
# 变更记录   : 加入转换/外围混接、误计第二个物理 FIFO 的审阅依据。
# =============================================================================
"""从任意工作目录运行；--repo 指定含冻结提交的功能工程。仅使用 Python 标准库。"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# 各入口共用同一已发布 DSP 源提交，防止台架与产品绑定读到不同的代码。
REV = (ROOT / "evidence/dsp-revision.txt").read_text(encoding="ascii").strip()
PROD = "adc_digital_ip_v21"
DSP = "sar20_ad4080_top"
SERVICES = "ad4080_peripheral_services"
READBACK = "ad4080_result_readback_link"
FRAMED = "ad4080_fifo_lvds_record_link"
PATTERN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_$][\w$]*|[()#;]', re.S)


def frozen(repo, *args):
    """读取指定提交中的实物，工作树和索引都不参与取证。"""
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(repo), *args], text=True
    )


def listed(text):
    # 所有生产条目是相对于工作树的路径。测试和模拟宏另有清单。
    return [p for line in text.splitlines() if (p := line.split("//", 1)[0].strip())]


# =============================================================================
# 1. 找声明，再识别对所有已建档模块的实例化
# =============================================================================
def declarations(paths):
    blocks = {}
    for path, source in paths.items():
        tokens = [w for w in PATTERN.findall(source) if not w.startswith(("//", "/*", '"'))]
        start = None
        for i, token in enumerate(tokens):
            if token == "module":
                start = i
            elif token == "endmodule" and start is not None:
                name = tokens[start + 1]
                if name in blocks:
                    raise ValueError(f"发现同名模块 {name}: {path}")
                blocks[name] = (path, tokens[start + 2 : i])
                start = None
    return blocks


def instances(tokens, known):
    result = []
    for i, word in enumerate(tokens):
        if word not in known:
            continue
        j = i + 1
        # 参数可含多层括号和常量表达式，不能用遇到第一个右括号来找实例名。
        if tokens[j : j + 2] == ["#", "("]:
            j += 2
            depth = 1
            while j < len(tokens) and depth:
                depth += (tokens[j] == "(") - (tokens[j] == ")")
                j += 1
            if depth:
                raise ValueError(f"模块参数未闭合 {word}")
        # 这一批源码以单实例语句书写，generate 中的银行有一个语法位置。
        if j + 1 < len(tokens) and re.fullmatch(r'[A-Za-z_$][\w$]*', tokens[j]) and tokens[j + 1] == "(":
            result.append({"module": word, "instance": tokens[j]})
    return result


def closure(top, graph):
    seen, todo = set(), [top]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(row["module"] for row in graph[name])
    return seen


# =============================================================================
# 2. 交叉引用要与生产清单和各自的真实入口相符
# =============================================================================
def audit(repo):
    prod_paths = listed(frozen(repo, "show", REV + ":flow/build/filelists/files_adc_v21.f"))
    dsp_paths = frozen(repo, "ls-tree", "-r", "--name-only", REV, "--", "subsys/ad4080_dsp/rtl").splitlines()
    src = {p: frozen(repo, "show", f"{REV}:{p}") for p in prod_paths + dsp_paths if p.endswith(".sv")}
    local_paths = []
    for name in ("integration/files.f", "integration/readback_files.f"):
        local_paths.extend(listed((ROOT / name).read_text()))
    # 取并集，避免共同引用的字映射器被误算成两份库存 RTL。
    for path in set(local_paths):
        src["delivery/" + path] = (ROOT / path).read_text()
    blocks = declarations(src)
    graph = {m: instances(body, blocks) for m, (_, body) in blocks.items()}
    scopes = {m: closure(m, graph) for m in (PROD, DSP, SERVICES, READBACK, FRAMED)}
    prod = {m for m, (p, _) in blocks.items() if p in prod_paths}
    dsp = {m for m, (p, _) in blocks.items() if p in dsp_paths}

    # 这里核对未绑定的原始实例关系。产品接入后的 SRAM 数量另读综合 JSON。
    if scopes[PROD] != prod or len(prod) != 11:
        raise ValueError("转换顶层可达范围与唯一 11 源生产清单不一致")
    if not scopes[DSP] <= dsp or (scopes[SERVICES] | scopes[FRAMED] | scopes[READBACK]) & (prod | dsp):
        raise ValueError("发现跨团队实例，需复核所有者及面积计入位置")
    if not {"sched18", "dither_pre", "dem_mask_v3", "wsum_v21", "recon_v21"} <= prod:
        raise ValueError("转换侧的调度、扰动、DEM 或权重重构缺失")
    if not {"sys_error_corr", "sinc1_decim", "sinc5_decim", "sinc5_comp", "event_detect",
            "fifo_capture", "result_fifo", "spi_slave", "ad4080_regs"} <= scopes[DSP]:
        raise ValueError("产品后处理侧的已知实例缺失")
    if "result_store" not in scopes[SERVICES] or "result_fifo" not in scopes[DSP]:
        raise ValueError("需要分别定位旧 DSP 的结果存储和交付包的物理银行实现")
    # 整记录复用现有载荷读回链，同时加入独立组帧；两种入口都不实例化另一份结果 RAM。
    if not scopes[READBACK] < scopes[FRAMED] or "ad4080_fifo_record_framer" not in scopes[FRAMED]:
        raise ValueError("整记录路径缺少已验证的单路载荷或组帧器")
    if {"result_fifo", "result_store"} & scopes[FRAMED]:
        raise ValueError("记录适配层重复包含结果存储")
    # 所有消费者都从源码求出，不以中文文档对版本、模块数或完成度的说法为输入。
    s5_calls = {m: [s["instance"] for s in calls if s["module"] == "sinc5_decim"]
                for m, calls in graph.items() if any(s["module"] == "sinc5_decim" for s in calls)}
    # PF1 与普通 SINC5 共用同一积分/梳状链；不同输出选择不能产生第二套大寄存器阵列。
    if s5_calls != {"sinc5_comp": ["u_sinc5"]}:
        raise ValueError("SINC5/PF1 共享关系或实例数量发生变化，请复核功能与面积")
    return {"revision": REV, "method": "declared module instance sites in listed source sets",
            "production_files": len(prod_paths), "dsp_declared_modules": len(dsp),
            "roots": {k: {"direct": graph[k], "reachable": sorted(v)} for k, v in scopes.items()},
            "sinc5_instance_sites": s5_calls, "dsp_leaves_outside_its_wrapper": sorted(dsp-scopes[DSP]),
            "scope": "static source closure only; no synthesis or timing claim"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=os.environ.get("ADC_FUNCTION_REPO"), help="冻结源码所在 Git 检出，默认本工作树")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    repo = args.repo
    if repo is None:
        origin = subprocess.run(["git","-C",str(ROOT),"rev-parse","--show-toplevel"],capture_output=True,text=True)
        if origin.returncode:
            ap.error("独立解压时需通过 --repo 指定含冻结提交的检出")
        repo = Path(origin.stdout.strip())
    results = audit(repo)
    out = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(out)
    print("PASS",[(m,len(v["reachable"])) for m,v in results["roots"].items()])
