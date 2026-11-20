# =============================================================================
# 文件名     : run_store.py
# 模块名     : 结果 SRAM 交付验收
# 版本       : v1.0
# 功能       : 在独立目录仿真完整深度，并从 Yosys 网表读取银行结构。
# 设计说明   : 仿真覆盖接口和位序；内存收集只说明通用可综合结构，工艺宏映射另验。
# 变更记录   : 增加全新构建目录、库路径别名与黑盒银行数检查。
# =============================================================================
"""从交付目录运行。路径参数接受实际工具，也可在已配置的 PATH 中查找。"""
import argparse
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


# =============================================================================
# 1. 工具、源文件和日志与本次运行绑定
# =============================================================================
def exe(name):
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"找不到工具 {name}")
    return Path(path).absolute()


def run(args,cwd,log,env):
    with log.open("w") as f:
        p=subprocess.run(args,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
    if p.returncode:
        raise RuntimeError(f"命令退出码 {p.returncode}，详见 {log}")


def main():
    if not __debug__:
        raise RuntimeError("检查结构断言请使用常规 Python 模式")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verilator",default=os.getenv("VERILATOR","verilator"))
    parser.add_argument("--yosys",default=os.getenv("YOSYS","yosys"))
    parser.add_argument("--work-parent",type=Path,default=Path(tempfile.gettempdir()))
    parser.add_argument("--receipt",type=Path,help="包含工具和源摘要的 JSON")
    args=parser.parse_args()
    args.work_parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix="store-check-",dir=args.work_parent))
    print("运行目录",work,flush=True)
    if not str(work).isascii() or " " in str(work):
        parser.error("请指定 ASCII 且无空格的临时构建目录")
    # Make 的第三方构建规则可能直接调用 python，别名只在本次运行的 PATH 生效。
    shortcuts=work/"bin";shortcuts.mkdir()
    (shortcuts/"python").symlink_to(sys.executable)
    env=dict(os.environ,PATH=str(shortcuts)+os.pathsep+os.environ["PATH"])
    sim=exe(args.verilator); compiler=exe(args.yosys)
    runtime=Path(env.get("VERILATOR_ROOT",sim.parent.parent))
    if (runtime/"bin/verilator_bin").is_file() and (runtime/"include/verilated.mk").is_file():
        alias=work/"vlt";alias.symlink_to(runtime.resolve(),target_is_directory=True)
        env.update(VERILATOR_ROOT=str(alias),VERILATOR_BIN=str(alias/"bin/verilator_bin"))
    for src in ["rtl/result_store.sv","verify/store_tb.sv","verify/store.ys"]:
        dst=work/src;dst.parent.mkdir(exist_ok=True);shutil.copyfile(ROOT/src,dst)
    run([sim,"--compiler","clang","--binary","--timing","-CFLAGS","-std=c++20",
         "--top-module","store_tb","--Mdir","obj","rtl/result_store.sv","verify/store_tb.sv"],
        work,work/"build.log",env)
    run([work/"obj/Vstore_tb"],work,work/"sim.log",env)
    simlog=(work/"sim.log").read_text()
    if "PASS result_store depth=16384 width=20 banks=4 checked_cycles=49156" not in simlog:
        raise ValueError("仿真未报告完整深度验收")

    # 2. 展开四个银行；保留 $mem_v2 时，stat 的普通 memory counter 可为零。
    run([compiler,"-s","verify/store.ys"],work,work/"yosys.log",env)
    modules=json.loads((work/"store_netlist.json").read_text())["modules"]
    instances=Counter(c["type"] for c in modules["result_store"]["cells"].values())
    memories=[c for c in modules["result_bank_4k20"]["cells"].values() if c["type"]=="$mem_v2"]
    assert instances["result_bank_4k20"]==4 and len(memories)==1
    geometry={n:int(memories[0]["parameters"][n],2) for n in ("ABITS","WIDTH","SIZE","RD_PORTS","WR_PORTS")}
    assert geometry=={"ABITS":12,"WIDTH":20,"SIZE":4096,"RD_PORTS":1,"WR_PORTS":1}
    # 文件只记录工具发布号和提交，保留各机器的独立安装路径。
    vlt_version = re.search(r"Verilator [0-9]+(?:\.[0-9]+)+", simlog)
    yosys_version = subprocess.check_output([compiler,"-V"],env=env,text=True).strip()
    receipt={"top":"result_store","bank_count":4,"generic_bank":geometry,"payload_bits":327680,
             "simulation":simlog.splitlines()[0],
             "simulator":vlt_version.group() if vlt_version else "see simulation.log",
             "synthesis_tool":yosys_version.split(",")[0].rstrip(")")+")" if "(git sha1 " in yosys_version else yosys_version,
             "synthesis_scope":"generic memory inference; PDK SRAM mapping and STA pending",
             "sources_sha256":{s:hashlib.sha256((work/s).read_bytes()).hexdigest()
                               for s in ("rtl/result_store.sv","verify/store_tb.sv","verify/store.ys")}}
    if args.receipt: args.receipt.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=="__main__":main()
