#!/usr/bin/env python3
"""直接从**生产 RTL** 抽取 rnd96 实现，检查它与 §9.3 的 RND(n,96) 等价。

为什么不能另写一份 Python 公式
------------------------------
早前的 tools/prove_rnd96.py 自己重写了公式。审查方在隔离副本里
把生产 RTL 的常数改错后，该脚本仍然打印 PROVEN —— 说明它检查的是
"我写的公式"，不是"生产实现"。这类检查对漂移完全免疫，毫无价值。

本脚本的做法：
  1. 从 rtl/sar_core/recon_v21.sv **原地读取** rnd96_const 的函数体
     与 MAGIC 常数（不复制、不重写）
  2. 把该函数体包成独立可编译单元，用 VCS 仿真
  3. 逐点比对 RND(n,96)，并记录源码哈希
  4. 若源码里缺少预期结构（常数名/移位量/舍入比较），直接报错退出

这样：
  * 改常数 -> 脚本读到的常数变了 -> 比对失败
  * 改移位量 -> 抽取的移位量变了 -> 比对失败
  * 改舍入条件 -> 抽取的比较变了 -> 比对失败
  * 删函数   -> 抽取失败 -> 脚本报错

输出分三段，互不混淆：
  [A] 数学公式证明（与 RTL 无关的恒等式）
  [B] 生产 RTL 仿真检查（本脚本主体）
  [C] 工具形式化（未完成，另行报告）
"""
import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RTL = ROOT / "rtl" / "sar_core" / "recon_v21.sv"

# 期望在生产源码里出现的结构（用于检测"函数被悄悄换掉"）
EXPECT_FUNC = "function automatic logic signed [31:0] rnd96_const"
EXPECT_SHIFT3 = ">> 34"
EXPECT_SHIFT5 = ">> 5"
EXPECT_DIV = "* 32'd96"
EXPECT_TIE = ">= 32'd96"


def fail(msg):
    print(f"  [B] FAIL: {msg}")
    sys.exit(1)


def extract():
    """从生产源码抽取 MAGIC 常数与 rnd96_const 函数体。"""
    if not RTL.exists():
        fail(f"生产源码不存在: {RTL}")
    src = RTL.read_text()

    m = re.search(r"localparam\s+logic\s*\[[^\]]+\]\s+MAGIC_3_34\s*=\s*([^;]+);", src)
    if not m:
        fail("生产源码里找不到 MAGIC_3_34 声明")
    magic_expr = m.group(1).strip()

    i = src.find(EXPECT_FUNC)
    if i < 0:
        fail("生产源码里找不到 rnd96_const 函数")
    j = src.find("endfunction", i)
    if j < 0:
        fail("rnd96_const 没有 endfunction")
    body = src[i : j + len("endfunction")]

    # 结构自检：防止函数被替换成别的实现
    for pat, name in [
        (EXPECT_SHIFT3, ">> 34"),
        (EXPECT_SHIFT5, ">> 5"),
        (EXPECT_DIV, "* 32'd96"),
        (EXPECT_TIE, ">= 32'd96"),
    ]:
        if pat not in body:
            fail(f"rnd96_const 里缺少预期结构 {name}")

    return magic_expr, body, hashlib.sha256(src.encode()).hexdigest()


def build_harness(magic_expr, body):
    """把抽取到的函数体包成可仿真模块。函数体原样嵌入，不做改写。"""
    return f"""`timescale 1ns/1ps
// 自动生成：不要手改。由 tools/check_rtl_rnd96.py 从生产源码抽取。
module rtl_rnd96_harness (
    input  logic signed [31:0] n,
    output logic signed [31:0] got
);
    localparam logic [34:0] MAGIC_3_34 = {magic_expr};

{body}

    assign got = rnd96_const(n);
endmodule
"""


def gen_points(n_bits=31, n_random=50000):
    """生成检查点：等价类端点 + 边界 + 随机。"""
    import random

    lim = 1 << n_bits
    qmax = (lim - 1) // 3
    pts = {0, 1, 2, 3, 47, 48, 49, 95, 96, 97, 191, 192, 193}
    # 等价类端点（floor(a*m/2^K) 只依赖 a//3 与 a%3）
    for q in (0, 1, 2, qmax - 2, qmax - 1, qmax):
        if 0 <= q <= qmax:
            for r in (0, 1, 2):
                a = q * 3 + r
                if a < lim:
                    pts.add(a)
                    pts.add(-a)
    # 均匀分布
    step = max(1, qmax // 4096)
    for q in range(0, qmax + 1, step):
        for r in (0, 1, 2):
            a = q * 3 + r
            if a < lim:
                pts.add(a)
                pts.add(-a)
    # 随机
    rnd = random.Random(20261116)
    for _ in range(n_random):
        pts.add(rnd.randrange(-(lim - 1), lim))
    return sorted(pts)


def ref_rnd(n, d):
    """§9.3 的 RND：符号对称、恰半离零。"""
    s = -1 if n < 0 else 1
    q, r = divmod(abs(n), d)
    return s * (q + (1 if 2 * r >= d else 0))


def run_vcs(work, harness):
    """编译并运行 harness，返回 {n: got} 映射。

    踩过的坑（都真实发生过）：
      1. `$fdisplay(1, ...)` 里的 1 不是已打开的 fd -> 输出丢失
      2. 相对路径 "pts.txt" 因 simv 工作目录不同而静默 fopen 失败
         -> fscanf 返回 0、仿真正常结束、结果为空
      3. fscanf 用 longint 后 `a[31:0]` 在旧版 VCS 上取值异常
    所以这里统一用**绝对路径 + 显式 fd + int 读入**。
    """
    (work / "rtl_rnd96_harness.sv").write_text(harness)
    outf = str(work / "res.txt")
    inf = str(work / "pts.txt")
    tb = work / "tb_rnd96.sv"
    tb.write_text(
        """`timescale 1ns/1ps
module tb_rnd96;
    logic signed [31:0] n;
    logic signed [31:0] got;
    rtl_rnd96_harness u (.n(n), .got(got));
    integer fd, outfd, r, a;
    initial begin
        outfd = $fopen("%OUTF%", "w");
        if (outfd == 0) begin $display("FATAL: cannot open out"); $finish; end
        fd = $fopen("%INF%", "r");
        if (fd == 0) begin $display("FATAL: cannot open in"); $finish; end
        $display("INFO: pts opened");
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d\\n", a);
            if (r == 1) begin
                n = a[31:0];
                #1;
                $fdisplay(outfd, "%0d %0d", a, got);
            end
        end
        $fclose(fd);
        $fclose(outfd);
        $display("INFO: done");
        $finish;
    end
endmodule
""".replace("%OUTF%", outf).replace("%INF%", inf)
    )
    env = dict(os.environ)
    env["SNPSLMD_LICENSE_FILE"] = "27080@localhost"
    env["VCS_HOME"] = "/opt/synopsys/vcs/W-2024.09-SP1"
    env["PATH"] = "/opt/synopsys/vcs/W-2024.09-SP1/bin:" + env.get("PATH", "")
    c = subprocess.run(
        ["vcs", "-full64", "-sverilog", "-timescale=1ns/1ps",
         "tb_rnd96.sv", "rtl_rnd96_harness.sv", "-o", "simv"],
        cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, env=env,
    )
    if c.returncode != 0:
        fail("VCS 编译失败\n" + c.stdout[-1500:] + c.stderr[-800:])
    r = subprocess.run(["./simv"], cwd=str(work), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=env)
    for ln in r.stdout.splitlines():
        if "INFO:" in ln or "FATAL" in ln:
            print("    " + ln.strip())
    out = work / "res.txt"
    if not out.exists():
        fail("仿真没有产生 res.txt\n" + r.stdout[-800:])
    res = {}
    for line in out.read_text().splitlines():
        q = line.split()
        if len(q) == 2:
            res[int(q[0])] = int(q[1])
    return res


def main():
    parser = argparse.ArgumentParser(description="核对现行 RTL 的定点除法")
    parser.add_argument("--work-dir", type=pathlib.Path, default=pathlib.Path("/tmp"),
                        help="已有的任务目录。脚本在其中另建专用子目录。")
    args = parser.parse_args()
    # 独占创建后保留 TB、harness 和结果，便于定位失败并行程安全。
    parent = args.work_dir.resolve()
    tmp = pathlib.Path("/tmp").resolve()
    home = pathlib.Path.home().resolve()
    retained = (home / "eda_runs").resolve()
    if parent == home or not (parent.is_relative_to(tmp) or parent.is_relative_to(retained)):
        parser.error("--work-dir must be under /tmp or $HOME/eda_runs")
    work = pathlib.Path(tempfile.mkdtemp(prefix="v21-rnd96.", dir=parent))
    print(f"  检查目录: {work}")
    print("=" * 70)
    print("[A] 数学公式证明（与 RTL 无关）")
    print("=" * 70)
    m = 0x155555556
    print(f"  m = ceil(2^34/3) = 0x{m:X} = {m}")
    print(f"  m 需要 {m.bit_length()} 位无符号 -> [33:0](34 位) 与 [34:0](35 位) 都装得下")
    print(f"  3m - 2^34 = {3*m - 2**34}")
    qmax = ((1 << 31) - 1) // 3
    upper = qmax * (3 * m - 2**34) + 2 * m
    print(f"  上界 2q+rm <= {upper} < 2^34 = {2**34} : {upper < 2**34}")
    print("  => floor(a*m/2^34) == floor(a/3) 对 |a| < 2^31 成立")
    print("  （这是不等式给出的全域结论；下面的点集只是补充核对）")

    print()
    print("=" * 70)
    print("[B] 生产 RTL 仿真检查（原地抽取，不重写公式）")
    print("=" * 70)
    magic_expr, body, sha = extract()
    print(f"  源码: {RTL.relative_to(ROOT)}")
    print(f"  sha256: {sha}")
    print(f"  抽取到 MAGIC_3_34 = {magic_expr}")
    print(f"  抽取到函数体 {len(body.splitlines())} 行")

    pts = gen_points()
    print(f"  检查点 {len(pts)} 个（等价类端点 + 边界 + 随机）")

    if True:
        (work / "pts.txt").write_text("\n".join(str(p) for p in pts) + "\n")
        got = run_vcs(work, build_harness(magic_expr, body))
        print(f"  仿真返回 {len(got)} 点")

        bad = 0
        for n in pts:
            if n not in got:
                fail(f"仿真未返回 n={n}")
            if got[n] != ref_rnd(n, 96):
                bad += 1
                if bad <= 5:
                    print(f"    MISMATCH n={n} rtl={got[n]} ref={ref_rnd(n, 96)}")
        print(f"  失配 {bad}")

    print()
    print("=" * 70)
    if bad == 0:
        print("RESULT: RTL_CHECK_PASS  (生产 RTL 的 rnd96_const 与 RND(n,96) 逐点一致)")
        print("        范围前提：|n| < 2^31。域外行为不在本检查范围内。")
        return 0
    print("RESULT: RTL_CHECK_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
