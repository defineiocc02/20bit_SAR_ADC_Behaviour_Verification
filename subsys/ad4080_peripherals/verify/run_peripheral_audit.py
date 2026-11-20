#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_peripheral_audit.py
# 模块名     : 数字外围交付回归
# 版本       : v1.0
# 功能       : 用独立临时目录重跑外围单元、冻结 DSP、跨域装配和版图检查。
# 设计说明   : 每项同时检查退出码和该 DUT 的完成证据。通过逻辑回归只更新对应范围。
# 变更记录   : 为外围整体验收建立统一入口，禁止借用上次工作目录或空跑的综合工具。
# =============================================================================
"""从任意目录运行。需要 Python、Verilator、Clang C++20、Yosys、KLayout/gdstk，以及冻结 Git 对象。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REV = "7408edf8db9a41eebdd756378b69d2775f107dec"


def checked(command, cwd, env, log):
    """工具的绝对路径只出现在本地日志。发布回执保存源摘要和真实结束标记。"""
    # 编译器的标准错误与标准输出同存，便于区分设计拒绝与工具提前退出。
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=900)
    if result.returncode:
        raise RuntimeError(f"{log.name} 退出 {result.returncode}")
    return log.read_text(encoding="utf-8", errors="replace")


def matched(log, expected):
    """每组有自己的标记。任一缺失时，退出码 0 也不能作为通过的依据。"""
    evidence = []
    # 成功信号来自对应的仿真器、真实总线检查或结构证明；日志行数不作为成功条件。
    for needle in expected:
        line = next((line.strip() for line in log.splitlines() if needle in line), None)
        if line is None:
            raise ValueError(f"缺少实际完成证据：{needle}")
        evidence.append(line)
    return evidence


def executable(name):
    path = shutil.which(name)
    if not path:
        raise ValueError(f"没有找到可执行文件：{name}")
    return str(Path(path).absolute())


def sources():
    """只锁定参与编译、综合、判断或检查的输入，避免把运行中的输出算作输入。"""
    files = sorted(p for p in ROOT.rglob("*") if p.is_file()
                   and p.suffix in {".sv", ".py", ".sh", ".ys", ".f", ".vlt", ".c", ".cpp", ".h"}
                   and not any(part.startswith(".") for part in p.relative_to(ROOT).parts))
    # 记录几何、责任表和未闭合项，否则换了面积或发布边界仍可能沿用相同回执。
    files.extend(ROOT / rel for rel in (
        "floorplan/plan.json", "floorplan/block_binding.csv", "interfaces/function_ownership.csv",
        "io/package_pins.csv", "evidence/peripheral_open_items.csv",
        "calibration/requirements.txt", "calibration/example_dc.csv", "io/adi_receiver/adi_upstream.sha256",
    ))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}


# =============================================================================
# 1. 在新建的本轮目录中检查各叶子与正常调用入口
# =============================================================================
def save_receipt(path, report):
    """原子提交本次状态。失败或未执行不得挂在上次的通过标记下。"""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    Path(out.name).replace(path)


def run_audit(args):
    save_receipt(args.receipt, {"status": "RUNNING", "product_signoff": "NOT_VERIFIED"})
    # 只向 git 读取已锁定的对象，其他团队当前检出的分支可以不同。
    if args.repo is None:
        found = subprocess.run(["git","-C",str(ROOT),"rev-parse","--show-toplevel"],
                               text=True,capture_output=True)
        if found.returncode:
            raise ValueError("不在仓库内，请用 --repo 指定含冻结 DSP 的 Git 检出")
        repo = Path(found.stdout.strip())
    else:
        repo = args.repo.resolve()
    git = ["git", "--no-optional-locks", "-C", str(repo)]
    sha = subprocess.check_output([*git, "rev-parse", f"{REV}^{{commit}}"], text=True).strip()
    if sha != REV:
        raise ValueError("缺少交付使用的 DSP Git 对象")
    args.work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="peripheral-audit-", dir=args.work_parent)).resolve()
    print(f"运行目录 {work}", flush=True)
    # Verilator 和 WASI 综合都在独立 ASCII 目录构建，冻结检出只通过 git show/archive 读取。
    if not str(work).isascii() or " " in str(work):
        raise ValueError("请为构建指定无空格的 ASCII 工作目录")
    # 限选一项时按其实际依赖准备工具，方便板端只复核校准公式。
    requested = set(args.case or [])
    software_only = {"ownership", "layout", "area", "adi_crc_analysis", "adctoolbox_host"}
    need_logic = not requested or not requested <= software_only
    # 单做板级校准或纯软件审阅时，入口不要求安装 HDL 仿真器或工艺工具。
    vlt = executable(args.verilator) if need_logic else args.verilator
    yosys = executable(args.yosys) if need_logic else args.yosys
    geom_python = executable(args.geometry_python) if not requested or "layout" in requested else sys.executable
    cal_python = args.calibration_python
    if not requested or requested & {"adctoolbox_host", "adctoolbox_rtl", "product_calibration"}:
        cal_python = executable(cal_python)
    tools = work / "bin"
    tools.mkdir()
    (tools / "python").symlink_to(sys.executable)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ADC_FUNCTION_REPO=str(repo),
               VERILATOR=vlt, YOSYS=yosys, TRANSPORT_VERILATOR=vlt, TRANSPORT_YOSYS=yosys,
               PATH=str(tools) + os.pathsep + os.environ["PATH"])
    # 非 ASCII 工具安装路径通过本轮符号链接进入 Make；系统预装分离式目录沿用原设置。
    runtime = Path(env.get("VERILATOR_ROOT", Path(vlt).parent.parent))
    if (runtime / "include/verilated.mk").is_file() and (runtime / "bin/verilator_bin").is_file():
        alias = work / "vlt"
        alias.symlink_to(runtime.resolve(), target_is_directory=True)
        vlt = str(alias / "bin/verilator_bin")
        env.update(VERILATOR=vlt, TRANSPORT_VERILATOR=vlt, VERILATOR_BIN=vlt, VERILATOR_ROOT=str(alias))
    # 最慢的是冻结 DSP 的编译；要在各子项开始之前锁定整组输入，而非事后补记。
    env.update(DSP_UPSTREAM_REPO=str(repo), ADC_FUNCTION_REPO=str(repo),
               ADCTOOLBOX_PYTHON=cal_python, CAL_PYTHON=cal_python)
    # HDL 支持本地已冻结检出或官方按摘要导出的文件树。
    if args.adi_hdl_checkout:
        env["ADI_HDL_CHECKOUT"] = str(args.adi_hdl_checkout.resolve())
    if args.adi_hdl_source_dir:
        env["ADI_HDL_SOURCE_DIR"] = str(args.adi_hdl_source_dir.resolve())
    if args.fetch_adi:
        env["ADI_HDL_FETCH"] = "1"
    before = sources()
    steps = []

    # 已有子包负责自己的编译设置，总入口只复用它们的正式入口和定向完成判据。
    def script(name, command, expect, cwd=ROOT):
        def execute(stage):
            log = checked(command, cwd, env, stage / "run.log")
            return matched(log, expect)
        steps.append((name, execute))

    # 没有独立入口的叶子由本运行器建立。冻结 DSP 通过 Git 内容读取，不复制发布其源码。
    def bench(name, top, paths, markers, options=(), frozen=False):
        def execute(stage):
            for rel in paths:
                target = stage / Path(rel).name
                if frozen and rel.startswith("subsys/ad4080_dsp/"):
                    target.write_bytes(subprocess.check_output([*git, "show", f"{REV}:{rel}"]))
                else:
                    shutil.copyfile(ROOT / rel, target)
            # 各 compile/runtime 独立保存。无 $finish 输出/黄金比对时不会放行。
            command = [vlt, "--binary", "--timing", "--assert", "--compiler", "clang",
                       "-CFLAGS", "-std=c++20", "--output-split", "0", "-MAKEFLAGS", "VM_PARALLEL_BUILDS=0", "--top-module", top,
                       "--Mdir", "obj", *options, *(Path(p).name for p in paths)]
            checked(command, stage, env, stage / "build.log")
            log = checked([stage / "obj" / ("V" + top)], stage, env, stage / "run.log")
            return matched(log, markers)
        steps.append((name, execute))

    # 几何门和实例归属可以证明已有数据接口与槽位，不能据此判定目标工艺时序。
    script("ownership", [sys.executable, "-B", ROOT/"verify/check_ownership.py", "--repo", repo],
           ("adc_digital_ip_v21", "sar20_ad4080_top", "ad4080_fifo_lvds_record_link"))
    layout_args = [geom_python, "-B", ROOT/"verify/check_floorplan.py", "--output", work/"layout.json"]
    # Git 不携带他方的裸片原图。在本机或手工指定时核其摘要，否则仍独立读回 GDS/DEF。
    supplied_photo = (ROOT/"report/ipbrain_reference.png") if args.photo is None else args.photo
    if args.photo is not None or supplied_photo.is_file():
        layout_args.extend(["--photo",supplied_photo.resolve()])
    script("layout", layout_args, ("规划几何验收通过",))
    script("area", [sys.executable, "-B", "-m", "unittest", "discover", "-s", ROOT/"budget",
           "-p", "test_*.py"], ("Ran ", "OK"))
    # 先核实 16K 结果单口，再用两种底图检查同一接口的破坏性测试交接。
    script("store", [sys.executable, "-B", ROOT/"verify/run_store.py", "--work-parent", work,
           "--receipt", work/"store.json"], ("checked_cycles=49156", "327680"))
    bench("mbist", "result_store_mbist_tb", ["rtl/result_store.sv", "memory/result_store_mbist.sv",
           "memory/result_store_mbist_tb.sv"], ("pattern=00000 accesses=163840", "pattern=55555 accesses=163840",
           "PASS dropped read response", "PASS result_store_mbist banks=4 full_scan_accesses=327680"),
          ("-Wall", "-Wno-DECLFILENAME"))
    # OTP 对请求之前出现的两个应答给出不同诊断，检查时两类拒绝都须在场。
    bench("otp", "tb_otp_shadow_loader", ["otp/otp_shadow_loader.sv", "otp/tb_otp_shadow_loader.sv"],
          ("unrequested macro response closes the session with code 10",
           "unrequested checker pass closes the session with code 11", "OTP SHADOW LOADER REGRESSION PASS"), ("-Wall",))
    # AON 有持续时钟，数字与 I/O 域可以独立停钟；基线只在共同工作时检查会漏掉故障。
    power_files = ["power/ad4080_pwr_reset_sync.sv", "power/ad4080_pwr_supervisor.sv",
                   "power/tb_ad4080_pwr_supervisor.sv"]
    bench("power", "tb_ad4080_pwr_supervisor", power_files,
          ("6 ns REFIN", "PASS ad4080 power supervisor directed"), ("-Wall",))
    # 零和上界参数触发不同生成条件，普通运行时改输入不能代替这三次展开。
    for value, name in [(0, "zero"), (10_000_001, "fraction"), (0xffff_ffff, "max")]:
        bench("power_"+name, "tb_ad4080_pwr_supervisor", power_files, ("PASS PARAM",),
              ("-Wall", f"-GTEST_AON_MAX_CLK_HZ=32'h{value:08x}"))
    # 真实边沿借独立位序检查；下面的形式性质只针对无状态映射器。
    bench("edge_words", "ad4080_ddr_edge_words_tb",
          ["io/ad4080_ddr_edge_words.sv", "io/ad4080_ddr_edge_words_tb.sv"],
          ("PASS IO edge words: 1109",), ("-Wall",))
    def prove_edge(stage):
        for rel in ("io/ad4080_ddr_edge_words.sv", "io/ad4080_ddr_edge_words_properties.sv", "io/prove_edge_words.ys"):
            shutil.copyfile(ROOT/rel, stage/Path(rel).name)
        log = checked([yosys, "-Q", "-s", "prove_edge_words.ys"], stage, env, stage/"run.log")
        return matched(log, ("SAT proof finished - no model found: SUCCESS!", "End of script."))
    steps.append(("edge_words_formal", prove_edge))
    # 二种 SPI 尾配置的 payload 编码相同。缺失授权时不应等待外部 CRC 才让主机读 SPI 前缀。
    framer_files = ["io/ad4080_fifo_record_framer.sv", "io/ad4080_fifo_record_framer_tb.sv"]
    for tail in (0, 1):
        bench("record_frame_"+str(tail), "ad4080_fifo_record_framer_tb", framer_files,
              (f"PASS FIFO record SPI_TAIL={tail}",), ("-Wall", f"-GBIND_SPI_TAIL=1'b{tail}"))
    # 冻结 DSP 在原仓的 logical FIFO 与新实现的四银行分别验证，避免两个存储体相加。
    dsp_path="subsys/ad4080_dsp/"
    # 冻结的普通队列台架自带固定 200 µs 看门狗，按原生 32 字运行，保留其全部断言。
    # 另一个随深度设置超时的整深度台架检查原队列和记录控制器面对同沿冲突时的区别。
    bench("frozen_result_fifo_tb", "result_fifo_tb",
          [dsp_path+"rtl/result_fifo.sv", dsp_path+"sim/tb/result_fifo_tb.sv"],
          ("RESULT_FIFO_CONTRACT_COMPLETE depth=32",), ("-Wno-fatal",), frozen=True)
    bench("frozen_fifo_capture_contract_tb", "fifo_capture_contract_tb",
          [dsp_path+"rtl/result_fifo.sv", dsp_path+"rtl/fifo_capture.sv", dsp_path+"sim/tb/fifo_capture_contract_tb.sv"],
          ("FIFO_CAPTURE_CONTRACT_COMPLETE depth=16384",), ("-Wno-fatal","-GDEPTH=16384"), frozen=True)
    bench("queue_port_full_depth", "pinned_fifo_contract_tb",
          [dsp_path+"rtl/result_fifo.sv", dsp_path+"rtl/fifo_capture.sv", "verify/pinned_fifo_contract_tb.sv"],
          ("PINNED_FIFO_1RW_PASS depth=16384 queue_both=1 captured_writes=16384 captured_reads=16384",),
          ("-Wno-fatal",), frozen=True)
    script("dsp_data", ["bash", ROOT/"interfaces/dsp_review/run.sh", repo],
           ("PASS config SPI -> correction", "PASS result SPI SDOB:", "aa00075e0009e8fff30e",
            "AD4080_DSP_RATE_SCAN_COMPLETE legal=27 clamp=3 pause_switch=1", "AD4080_DSP_REVIEW_COMPLETE"))
    # 管理请求验证既有原始 SPI 路径，也验证独立 CSR 薄桥；仅测默认上电值不能替代这两项。
    script("dsp_control", [sys.executable, "-B", ROOT/"interfaces/control_review/run.py", "--repo", repo,
           "--scratch", work/"control", "--verilator", vlt], ("CONTROL_REVIEW_COMPLETE", "OBS watermark_host low_mismatch=blocked high_mismatch=blocked",
           "OBS bridge_gpio addresses=0x1a,0x1b blocked=24", "POWER_BRIDGE_COMPLETE"))
    script("transport", ["sh", ROOT/"transport/verify.sh"],
           ("PASS single 40/400 MHz", "PASS dual 40/200 MHz", "PASS startup discard visible",
            "AD4080 RESULT TRANSPORT REGRESSION PASS",
            "Yosys generic synthesis and check PASS"))
    # BIST 的保持、破坏性授权、重建是不同阶段。服务壳需经故障回归才开放结果口。
    script("services", ["bash", ROOT/"integration/run_verify.sh"],
           ("BIST hold before grant is harmless", "incomplete-capture reset/mode/BIST blocked",
            "PERIPHERAL_SERVICES PASS", "Generic synthesis structure PASS"))
    # 工程 CRC 完整帧由另一侧按 36 个真实双沿采样复原。它不等价于验证器件 CRC 参数。
    script("record_link", ["bash", ROOT/"integration/run_linked.sh"],
           ("RECORD_LVDS_GOLDEN PASS", "aa95e2b8000181a4d4, 36 pairs",
            "PERIPHERAL_READBACK_LINK PASS", "Readback generic structure PASS"))

    # 厂商程序放在独立构建目录，只从固定 Git 对象或各自有摘要的官方源读取。
    # no-OS 驱动是未改的 C 函数；新桥接只提供 SPI、延时、GPIO 等板端服务。
    def noos_trial(stage):
        archive = args.adi_noos_archive
        # 未显式允许联网时，使用调用方准备的离线包，支持隔离构建机。
        if archive is None and args.fetch_adi:
            archive = stage/"no-os.tar.gz"
            url = "https://codeload.github.com/analogdevicesinc/no-OS/tar.gz/0e6ed1dea135ef541ad6323fc9fcad116ffce90b"
            request = Request(url,headers={"User-Agent":"ad4080-peripherals-source-check"})
            with urlopen(request, timeout=90) as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out)
        if archive is None:
            raise ValueError("需要 --adi-noos-archive，或显式 --fetch-adi 下载固定官方资料")
        command = [sys.executable,"-B",ROOT/"interfaces/control_review/run_noos.py","--repo",repo,
                   "--adi-archive",Path(archive).resolve(),"--scratch",stage/"build","--verilator",vlt]
        return matched(checked(command, ROOT, env, stage/"run.log"),("NOOS_HOST_COMPLETE", "NOOS_SPI_RTL_COMPLETE"))
    steps.append(("adi_noos",noos_trial))
    script("adi_receiver", ["sh",ROOT/"io/adi_receiver/run_verify.sh"],
           ("PASS ADI receiver interop: 4 runs",))
    # 这组仅检验候选 CRC 的区分能力；厂商参数须由独立采集的尾字段确认。
    script("adi_crc_analysis", [sys.executable,"-B","-m","unittest","discover","-s",ROOT/"io/adi_crc",
           "-p","test_*.py"], ("Ran 12 tests","OK"))
    # 绝对校准、外围单存储和产品实测参数各有自己的证明边界，不能用工程 CRC 自证商用参数。
    script("adctoolbox_host", [cal_python,"-B","-m","unittest","discover","-s",ROOT/"calibration",
           "-p","test_*.py"], ("Ran ","OK"))
    script("adctoolbox_rtl", [cal_python,"-B",ROOT/"calibration/verify_rtl.py","--repo",repo,
           "--verilator",vlt,"--out",work/"system-calibration.json"], ("Verified 8234 RTL outputs against sys_corr",))
    # 两个入口都展开同一 DSP。一个断言实际跨环读址，另一个使用主机实算寄存器。
    script("product_binding", ["bash",ROOT/"integration/product/run_product.sh"],
           ("AD4080_PRODUCT_BINDING PASS","PRODUCT_STRUCTURE PASS"))
    script("product_calibration", ["bash",ROOT/"integration/product/run_product.sh","--calibration"],
           ("AD4080_PRODUCT_CALIBRATION PASS","PRODUCT_STRUCTURE PASS"))

    # =========================================================================
    # 2. 汇总各独立结果，保留开项及失败结果
    # =========================================================================
    requested = set(args.case or [])
    names = {name for name, _ in steps}
    if requested - names:
        raise ValueError(f"未登记的检查项 {sorted(requested-names)}")
    if args.jobs < 1:
        raise ValueError("并行数至少为 1")
    def execute_one(name, execute):
        stage = work/name
        stage.mkdir()
        try:
            return {"status":"PASS", "observed":execute(stage)}
        except (RuntimeError,ValueError,OSError,subprocess.SubprocessError) as error:
            return {"status":"FAIL", "reason":str(error)}

    # 将用时较长的冻结 DSP 和大布局单元提前入队，其余独立小测试可同时推进。
    # 每项使用专属工作目录；结果按清单顺序保存，避免因调度顺序造成归档差异。
    # 指定某一项时，只记录该项现场证据；发布者可从 full_suite 判断是否完整运行。
    selected = [(name, fn) for name,fn in steps if not requested or name in requested]
    order = {"dsp_data":0, "adi_noos":1, "product_binding":2, "product_calibration":3}
    finished = {}
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(selected))) as pool:
        futures = {pool.submit(execute_one, name, fn): name
                   for name,fn in sorted(selected,key=lambda item:order.get(item[0],4))}
        for future in as_completed(futures):
            name = futures[future]
            finished[name] = future.result()
            print(f"{finished[name]['status']:4} {name}",flush=True)
    outcomes = {name:finished[name] for name,_ in selected}
    if sources() != before:
        # 防止并发改源码时将早期编译与后来输出打包成同一次全量回归。
        raise ValueError("运行期间交付源变化，请在稳定检出下重新运行")
    with (ROOT/"evidence/peripheral_open_items.csv").open(encoding="utf-8",newline="") as f:
        open_items = list(csv.DictReader(f))
    # 没有工艺库或实物位流的项目单列在开放账本里。一次数字仿真成功不能自动解除它们。
    expected_open = {"FRAME-CRC", "SPI-CLOCK", "DSP-MAP", "CNV-READY", "PRODUCT-JOIN", "MACROS",
                     "OTP-FACTORY", "POWER-PHY", "PHYSICAL", "GPIO-STATE"}
    if {row["id"] for row in open_items} != expected_open or len(open_items) != len(expected_open):
        raise ValueError("产品待办集合已变。请对照新增证据更新发布判据。")
    if any(not all(row.values()) for row in open_items):
        raise ValueError("未闭合项缺少来源、负责人或关闭证据")
    # 36 行是转换组、外围和实物宏的交界清单。物理或工厂项可明确没有 RTL 测例；
    # 已归类为包内 RTL 的功能则必须能指向本入口真正存在的检查。
    with (ROOT/"interfaces/function_ownership.csv").open(encoding="utf-8", newline="") as f:
        inventory = list(csv.DictReader(f))
    ids = [r["function_id"] for r in inventory]
    if len(inventory) != 36 or len(set(ids)) != len(ids):
        raise ValueError("功能清单缺项或重复，请逐条复核新增或撤销的设计边界")
    for row in inventory:
        refs = set(row["regression_cases"].split())
        external = set(row["open_ids"].split())
        if not row["audit_scope"] or refs - names or external - expected_open:
            raise ValueError(f'功能 {row["function_id"]} 的核查范围或引用不可解析')
        if row["owner_class"] == "DELIVERY_RTL" and not refs:
            raise ValueError(f'已交付 RTL {row["function_id"]} 没有可运行的检查')
    # 对外回执不携带宿主目录。独立子项的未裁剪日志仍保存在此次临时运行树中。
    for outcome in outcomes.values():
        if outcome["status"] == "PASS":
            outcome["observed"] = [line.replace(str(work), "<run>").replace(str(repo), "<frozen-repo>")
                                   .replace(str(ROOT), "<delivery>") for line in outcome["observed"]]
    failed = [n for n,v in outcomes.items() if v["status"]=="FAIL"]
    report = {"status":"FAIL" if failed else "PASS", "scope":"all registered open-source checks" if not requested else "selected checks only",
              "frozen_dsp":REV, "checks":outcomes, "full_suite":set(outcomes)==names,
              "failed":failed, "scope_rows":len(inventory), "photo_bytes_compared":supplied_photo.is_file(), "open_items":open_items, "product_signoff":"NOT_VERIFIED",
              "source_sha256":before}
    # 外部复核还可以在工作目录读原始日志与逐项产物，封装的小回执记录它们的判据。
    result = work/"result.json"
    result.write_text(json.dumps(report, ensure_ascii=False,indent=2)+"\n")
    save_receipt(args.receipt, report)
    print(f"PASS={len(outcomes)-len(failed)} FAIL={len(failed)} OPEN={len(open_items)}")
    print("产品待办", ", ".join(row["id"] for row in open_items))
    if failed:
        return 1
    # 发布逻辑回归与验收器件是两种退出条件，自动化可明确选择。
    return 2 if args.require_device_closure and (open_items or set(outcomes) != names) else 0


if __name__=="__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo",type=Path,default=os.environ.get("ADC_FUNCTION_REPO"),
                     help="含冻结 DSP 的 Git 检出；默认采用当前工作树")
    cli.add_argument("--calibration-python",default=os.environ.get("ADCTOOLBOX_PYTHON", sys.executable),
                     help="已安装固定 ADCToolbox 的 Python；只运行相应校准检查时才使用")
    cli.add_argument("--adi-noos-archive",type=Path, default=os.environ.get("ADI_NOOS_ARCHIVE"),
                     help="对应公开提交的 no-OS .tar.gz")
    cli.add_argument("--adi-hdl-checkout",type=Path,default=os.environ.get("ADI_HDL_CHECKOUT"))
    cli.add_argument("--adi-hdl-source-dir",type=Path,default=os.environ.get("ADI_HDL_SOURCE_DIR"))
    cli.add_argument("--fetch-adi",action="store_true",help="允许在线获取固定 ADI no-OS 与 HDL 源码")
    cli.add_argument("--photo",type=Path,help="自备已登记裸片图，另验图像字节与尺寸")
    cli.add_argument("--verilator",default=os.getenv("VERILATOR","verilator"))
    cli.add_argument("--yosys",default=os.getenv("YOSYS","yosys"))
    cli.add_argument("--geometry-python",default=sys.executable)
    cli.add_argument("--work-parent",type=Path,default=Path(tempfile.gettempdir()))
    cli.add_argument("--jobs",type=int,default=2,help="可同时运行的独立检查数，默认 2")
    cli.add_argument("--receipt",type=Path)
    cli.add_argument("--case",action="append",help="仅复核选定项；省略表示完整运行")
    cli.add_argument("--require-device-closure", action="store_true",
                     help="产品签收另计；仍有等待实物/目标工艺的项目时退出 2")
    # 工具或依赖缺失在该次运行中视为未能完成，不能继承旧的成功回执。
    options = cli.parse_args()
    try:
        code = run_audit(options)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as err:
        save_receipt(options.receipt, {"status":"FAIL", "stage":"runner", "error":str(err),
                                      "product_signoff":"NOT_VERIFIED"})
        print(err, file=sys.stderr)
        code = 1
    raise SystemExit(code)
