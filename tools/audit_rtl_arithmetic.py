#!/usr/bin/env python3
"""以显式数值夹具审计独立整数算术，不导入 ADC 模型或旧参考模型。

仅检查码中心取偶舍入、乘加算术域、有符号向下取整及输出标志；位宽来自必填 JSON。
夹具与结果均标为 COMPONENT_SYNTHETIC_DIGITAL，不作为模拟器件参数的证明。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # 在导入同目录模块前关闭字节码写入，避免在工作树生成缓存。

from run_open_rtl import (  # noqa: E402
    REPO,
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    verilator_compiler_args,
    write_json,
)

SOURCES = (
    "rtl/core/cal_residue_mac.sv",
    "rtl/core/adc2_dec.sv",
    "rtl/core/div_floor.sv",
    "rtl/core/cal_output_stage.sv",
)
FIELDS = (
    "voltage_bits",
    "voltage_fraction_bits",
    "weight_fraction_bits",
    "accumulator_bits",
    "output_bits",
    "adc2_bits",
    "sum_bits",
    "divisor_bits",
    "divider_stages",
    "random_cases",
    "seed",
)


def load_format(path: Path) -> dict:
    """只接受完整数值声明；范围限制属于本审计的支持域，不推断物理器件。"""
    x = json.loads(path.read_text())
    if (
        not isinstance(x, dict)
        or x.get("schema_version") != 1
        or x.get("scope") != "COMPONENT_SYNTHETIC_DIGITAL"
        or set(x) != {"schema_version", "scope", "description", *FIELDS}
        or not isinstance(x["description"], str)
        or not x["description"].strip()
        or any(type(x[k]) is not int for k in FIELDS)
    ):
        raise ValueError("format-json requires all explicit synthetic numerical fields")
    vb, vf, wf, acc, out, adc, sb, wd, st, n, seed = (x[k] for k in FIELDS)
    wa = acc - vf - 1
    if not (
        4 <= vb <= 64
        and 1 <= vf < vb
        and 1 <= wf <= sb + 3
        and 4 <= out <= 24
        and 2 <= adc <= 16
        and 4 <= sb <= 64
        and 2 <= wa <= 96
        and 1 <= wd <= 96
        and 1 <= st <= wa
        and sb <= acc + 1
        and 0 <= acc - vf - 2 < sb
        and sb + vb + 4 > acc
        and 1 <= n <= 4096
        and 0 <= seed < 2**32
    ):
        raise ValueError("format exceeds bounded audit or actual MAC/decoder width contracts")
    return x


def write_math_header(path: Path, fmt: dict) -> None:
    """仅写数学子模块使用的位宽，不写切片、扰动、模拟电压或采样率参数。"""
    names = {
        "V_BITS": "voltage_bits",
        "V_FRAC": "voltage_fraction_bits",
        "W_FRAC": "weight_fraction_bits",
        "ACC_BITS": "accumulator_bits",
        "OUT_BITS": "output_bits",
        "ADC2_BITS": "adc2_bits",
    }
    path.write_text(
        "// COMPONENT_SYNTHETIC_DIGITAL; numerical units, not device parameters.\n"
        + "`ifndef COMPONENT_MATH_PARAMS_VH\n`define COMPONENT_MATH_PARAMS_VH\n"
        + "".join(f"localparam int {k} = {fmt[v]};\n" for k, v in names.items())
        + "`endif\n"
    )


def encode(values) -> str:
    """按声明位宽编码二进制补码，不经浮点数表示大整数。"""
    return " ".join(format(v % (1 << w), "x") for v, w in values)


def round_even_ratio(n: int, d: int) -> int:
    """用整数商余数实现最近偶数舍入，同样处理负数。"""
    q, rem = divmod(n, d)
    return q + int(2 * rem > d or (2 * rem == d and q % 2))


def flag_vectors(bits: int) -> list:
    """用标量逐沿状态机生成参考，分别覆盖配置取消及同沿清除与错误的优先级。"""
    stim = [
        [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        [1, 1, 0, 1, 37, 7, 0, 0, 0, 0, 0],
        [1, 1, 0, 0, 91, 8, 0, 0, 0, 0, 0],
        [1, 1, 0, 1, 73, 9, 0, 1, 1, 0, 0],
        [1, 1, 1, 1, 99, 10, 1, 0, 0, 1, 0],
        [1, 1, 1, 0, 0, 11, 0, 0, 0, 0, 0],
        [1, 1, 0, 1, 111, 12, 1, 0, 0, 0, 1],
        [1, 0, 0, 1, 121, 13, 0, 1, 1, 1, 1],
        [1, 1, 0, 1, (1 << bits) - 1, 14, 0, 1, 0, 0, 0],
        [1, 1, 1, 1, 0, 15, 1, 0, 0, 0, 1],
        [1, 1, 0, 1, 201, 16, 0, 0, 0, 0, 0],
        [0, 1, 1, 1, 211, 17, 1, 1, 1, 1, 1],
    ]
    word = low = high = acc = gain = adc = sid = flags = 0
    result = []
    for row in stim:
        rst, ready, clear, done, candidate, identity, cl, ch, ov, ge, ao = row
        valid = 0
        if not rst:
            word = low = high = acc = gain = adc = sid = flags = 0
        else:
            if clear:
                acc = gain = adc = 0
            if ready and done:
                valid, sid = 1, identity
                flags = ((ch << 4 | cl << 3) if not (ov or ge) else 0) | ao << 2 | ge << 1 | ov
                adc |= ao
                acc |= ov
                gain |= ge
                if not (ov or ge):
                    word, low, high = candidate, cl, ch
            if not ready:
                word = low = high = sid = flags = valid = 0
        result.append((row, [word, valid, low, high, acc, gain, adc, sid, flags]))
    return result


def generate_vectors(root: Path, f: dict) -> dict:
    """生成少量随机与边界向量，要求覆盖正常／溢出算术、宽分母及负数向下取整。"""
    vb, vf, wf, acc, ob, ab, sb = (f[k] for k in FIELDS[:7])
    wa, wd = acc - vf - 1, f["divisor_bits"]
    rng = random.Random(f["seed"])
    vmin, vmax, lim = -(1 << (vb - 1)), (1 << (vb - 1)) - 1, 1 << (acc - 1)
    edge = 1 << max(0, acc - 1 - ob - wf)
    cases = [
        (max(vmin, min(vmax, sign * edge + d)), 0, 0, 1, 0, 0)
        for sign in (-1, 1)
        for d in (-3, -2, -1, 0, 1, 2, 3)
    ]
    for k in range(f["random_cases"]):
        if k % 3 == 0:
            fine, offset, injection = (rng.randint(vmin, vmax) for _ in range(3))
            gain, total = (rng.getrandbits(sb) for _ in range(2))
            rails = rng.randrange(-(1 << (sb + 1)), 1 << (sb + 1))
        else:
            v = min(511, vmax)
            fine, offset, injection = (rng.randint(-v, v) for _ in range(3))
            gain, total = (rng.getrandbits(min(sb, 14)) for _ in range(2))
            rails = rng.randint(-8191, 8191)
        cases.append((fine, offset, injection, gain, total, rails))
    rows, accepted = [], 0
    for fine, offset, injection, gain, total, rails in cases:
        op1, op2, op3 = (fine - offset) << wf, rails << vf, total * injection
        numerator = op1 - op2 - op3
        shifted = (numerator + (gain << vf)) << ob
        denominator = gain << (vf + 1)
        ov = any(not -lim <= x < lim for x in (op1, op2, op3, numerator, shifted, denominator))
        accepted += not ov
        rows.append(
            encode(
                [
                    (fine, vb),
                    (offset, vb),
                    (injection, vb),
                    (gain, sb),
                    (total, sb),
                    (rails, sb + 2),
                    (int(ov), 1),
                    (shifted // (1 << (vf + 1)), wa),
                ]
            )
        )
    if not 0 < accepted < len(rows):
        raise ValueError("MAC vectors must cover accepted and overflow arithmetic")
    (root / "mac.hex").write_text("\n".join(rows) + "\n")
    adc = [(0, 0, k * (1 << ab)) for k in (-3, -1, 1, 3)]
    adc += [
        (rng.getrandbits(ab), rng.randint(vmin, vmax), rng.randint(vmin, vmax))
        for _ in range(f["random_cases"])
    ]
    (root / "adc.hex").write_text(
        "".join(
            encode(
                [
                    (c, ab),
                    (lo, vb),
                    (hi, vb),
                    (lo + round_even_ratio((2 * c + 1) * (hi - lo), 1 << (ab + 1)), vb),
                ]
            )
            + "\n"
            for c, lo, hi in adc
        )
    )
    amin, amax = -(1 << (wa - 1)), (1 << (wa - 1)) - 1
    ds = sorted({0, 1, 2, 3, (1 << wd) - 1, min(1 << wa, (1 << wd) - 1)})
    div = [(n, d) for n in (amin, amin + 1, -1, 0, 1, amax) for d in ds]
    for k in range(f["random_cases"]):
        div.append(
            (rng.randint(amin, amax), rng.getrandbits(wd if k % 2 == 0 else rng.randrange(wd + 1)))
        )
    (root / "div.hex").write_text(
        "".join(
            encode([(n, wa), (d, wd), (n // d if d else 0, wa), (int(d == 0), 1)]) + "\n"
            for n, d in div
        )
    )
    flags = flag_vectors(ob)
    (root / "flags.hex").write_text(
        "".join(
            encode(
                [(v, ob if k == 4 else 32 if k == 5 else 1) for k, v in enumerate(row)]
                + [
                    (v, ob if k == 0 else 32 if k == 7 else 5 if k == 8 else 1)
                    for k, v in enumerate(expected)
                ]
            )
            + "\n"
            for row, expected in flags
        )
    )
    return {
        "mac": len(cases),
        "mac_nonoverflow": accepted,
        "adc": len(adc),
        "div": len(div),
        "flag_sequences": len(flags),
    }


TB = r"""
// COMPONENT_SYNTHETIC_DIGITAL: explicit raw integer format, no ADC device parameters.
`timescale 1ns/1ps
`include "rtl_params.vh"
module arithmetic_audit_tb;
 localparam int SUM=@SUM@,WA=@WA@,WD=@WD@,STAGES=@STAGES@;
 localparam int LAT=(WA+STAGES-1)/STAGES;
 logic clk=0,sample_clk=0;
 always #2 clk=~clk;
 logic signed[V_BITS-1:0] f,o,i,mi,ma,ef,sf,so,si,smi,sma;
 logic[SUM-1:0] g,t,sg,st;
 logic signed[SUM+1:0] rails,sr;
 logic[ADC2_BITS-1:0] c,sc;
 wire[V_BITS-1:0] af;
 wire[WA-1:0] a1;
 logic[WA-1:0] ea1;
 wire mo,ao;
 logic eo;
 always @(posedge sample_clk)begin
  sf<=f;so<=o;si<=i;sg<=g;st<=t;sr<=rails;sc<=c;smi<=mi;sma<=ma;
 end
 task automatic sample_inputs();#1;sample_clk=1;#1;sample_clk=0;#1;endtask
 cal_residue_mac #(.SUM_BITS(SUM)) mac(.fine_r(sf),.off_r(so),.inj_r(si),
  .gain_s(sg),.total_s(st),.rails_s(sr),.a1(a1),.any_ovf(mo));
 adc2_dec adc(.adc2_code(sc),.adc2_min_q(smi),.adc2_max_q(sma),.fine_q(af),.ovf(ao));
 logic rst=0,start=0;
 logic signed[WA-1:0] da,eq;
 logic[WD-1:0] dd;
 wire signed[WA-1:0] dq;
 wire de,busy,done;
 logic ee;
 div_floor #(.P_W_A(WA),.P_W_D(WD),.P_STAGES(STAGES)) dv(
  .clk(clk),.rst_n(rst),.start(start),.a(da),.d(dd),.q(dq),.err(de),.busy(busy),.done(done));
 logic fr=0,ready=0,clear=0,commit=0,cl=0,ch=0,ov=0,ge=0,bad=0;
 logic[OUT_BITS-1:0] candidate=0,ew;
 logic[31:0] sid=0,eid;
 wire[OUT_BITS-1:0] word;
 wire valid,rl,rh,ra,rg,ro;
 wire[31:0] rid;
 wire[4:0] flags;
 logic ev,el,eh,eacc,egain,eadc;
 logic[4:0] eflags;
 logic pr,prd,pc,pe,pcl,pch,po,pg,pa;
 logic[OUT_BITS-1:0] pw;
 logic[31:0] pid;
 cal_output_stage output_stage(.clk(clk),.rst_n(fr),.cfg_ready(ready),.clr_ovf(clear),
  .complete(commit),.candidate(candidate),.candidate_sample_id(sid),
  .candidate_clip_low(cl),.candidate_clip_high(ch),.candidate_acc_ovf(ov),
  .candidate_gain_err(ge),.candidate_adc2_ovf(bad),.dout(word),.dout_valid(valid),
  .clip_low(rl),.clip_high(rh),.acc_ovf(ra),.gain_err(rg),.adc2_ovf(ro),
  .result_sample_id(rid),.result_flags(flags));
 int mn=0,an=0,dn=0,fn=0,ignored=0;
 int em,ea,ed,efs;
 bit ready_counts=0,comb_finished=0,seq_finished=0;
 string vdir;
 initial begin
  if(!$value$plusargs("VDIR=%s",vdir) ||
    !$value$plusargs("expected_mac=%d",em) || !$value$plusargs("expected_adc=%d",ea) ||
    !$value$plusargs("expected_div=%d",ed) || !$value$plusargs("expected_flags=%d",efs))
    $fatal(1,"explicit vectors and counts required");
  if(em<=0 || ea<=0 || ed<=0 || efs<=0)$fatal(1,"empty arithmetic fixture");
  ready_counts=1;
 end
 initial begin:comb_check
  int fd,n;string line;wait(ready_counts);
  fd=$fopen({vdir,"/mac.hex"},"r");if(fd==0)$fatal(1,"MAC file missing");
  while($fgets(line,fd)!=0)begin
   n=$sscanf(line,"%h %h %h %h %h %h %h %h",f,o,i,g,t,rails,eo,ea1);
   if(n!=8)$fatal(1,"MAC parse");sample_inputs();mn++;
   if(mo!==eo || (!mo && a1!==ea1))$fatal(1,"MAC mismatch row=%0d",mn);
  end
  $fclose(fd);if(mn!=em)$fatal(1,"MAC count mismatch");
  $display("MAC_INDEPENDENT_PASS cases=%0d",mn);
  fd=$fopen({vdir,"/adc.hex"},"r");if(fd==0)$fatal(1,"ADC file missing");
  while($fgets(line,fd)!=0)begin
   n=$sscanf(line,"%h %h %h %h",c,mi,ma,ef);
   if(n!=4)$fatal(1,"ADC parse");sample_inputs();an++;
   if(ao || af!==ef)$fatal(1,"ADC mismatch row=%0d",an);
  end
  $fclose(fd);if(an!=ea)$fatal(1,"ADC count mismatch");
  $display("ADC_INDEPENDENT_PASS cases=%0d",an);comb_finished=1;
 end
 initial begin:seq_check
  int fd,n;string line;wait(ready_counts);
  @(posedge clk);#0.1;@(negedge clk);rst=1;
  fd=$fopen({vdir,"/div.hex"},"r");if(fd==0)$fatal(1,"DIV file missing");
  while($fgets(line,fd)!=0)begin
   n=$sscanf(line,"%h %h %h %h",da,dd,eq,ee);if(n!=4)$fatal(1,"DIV parse");
   @(negedge clk);start=1;@(posedge clk);#0.1;
   if(!busy || done)$fatal(1,"DIV acceptance");
   @(negedge clk);start=0;
   for(int step=1;step<=LAT;step++)begin
    @(posedge clk);#0.1;
    if(step<LAT && (!busy || done))$fatal(1,"DIV early result");
    if(step==LAT && (busy || !done || dq!==eq || de!==ee))
      $fatal(1,"DIV mismatch row=%0d",dn+1);
    @(negedge clk);start=(step==1 && step<LAT);
    if(start)begin da='1;dd='1;ignored++;end
   end
   @(posedge clk);#0.1;
   if(done || dq!==eq || de!==ee)$fatal(1,"DIV pulse or hold");
   dn++;
  end
  $fclose(fd);if(dn!=ed)$fatal(1,"DIV count mismatch");
  $display("DIV_INDEPENDENT_PASS cases=%0d latency=%0d busy_requests=%0d",dn,LAT,ignored);
  fd=$fopen({vdir,"/flags.hex"},"r");if(fd==0)$fatal(1,"flag file missing");
  while($fgets(line,fd)!=0)begin
   n=$sscanf(line,"%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h",
      pr,prd,pc,pe,pw,pid,pcl,pch,po,pg,pa,ew,ev,el,eh,eacc,egain,eadc,eid,eflags);
   if(n!=20)$fatal(1,"flag parse");
   @(negedge clk);fr=pr;ready=prd;clear=pc;commit=pe;candidate=pw;sid=pid;
   cl=pcl;ch=pch;ov=po;ge=pg;bad=pa;
   @(posedge clk);#0.1;
   if({word,valid,rl,rh,ra,rg,ro,rid,flags}!=={ew,ev,el,eh,eacc,egain,eadc,eid,eflags})
    $fatal(1,"OUTPUT flag mismatch row=%0d",fn+1);
   fn++;
  end
  $fclose(fd);if(fn!=efs)$fatal(1,"flag count mismatch");
  $display("OUTPUT_FLAG_SEQUENCE_PASS scenarios=%0d",fn);seq_finished=1;
 end
 initial begin
  wait(comb_finished && seq_finished);
  $display("ARITHMETIC_AUDIT_COMPLETE mac=%0d adc=%0d div=%0d flag_sequences=%0d scope=COMPONENT_SYNTHETIC_DIGITAL",mn,an,dn,fn);
  $finish;
 end
 initial begin
  wait(ready_counts);
  repeat((ed*(LAT+5)+em+ea+efs+100)*4)@(posedge clk);
  $fatal(1,"arithmetic watchdog");
 end
endmodule
"""


def write_testbench(path: Path, fmt: dict) -> None:
    """测试台仅实例化数学子模块；数字夹具参数与器件配置无关。"""
    text = TB
    values = {
        "SUM": fmt["sum_bits"],
        "WA": fmt["accumulator_bits"] - fmt["voltage_fraction_bits"] - 1,
        "WD": fmt["divisor_bits"],
        "STAGES": fmt["divider_stages"],
    }
    for key, value in values.items():
        text = text.replace("@" + key + "@", str(value))
    path.write_text(text)


def run_case(
    out: Path,
    work: Path,
    fmt: dict,
    command: list,
    version: str,
    counts: dict,
    timeout: int,
    mutation=None,
) -> dict:
    """使用全新编译目录；负对照须在真实仿真中触发 DIV mismatch，编译错误不算检出。"""
    out.mkdir()
    stage = work / out.name
    stage.mkdir()
    include = stage / "include"
    include.mkdir()
    write_math_header(include / "rtl_params.vh", fmt)
    sources = []
    for name in SOURCES:
        target = stage / Path(name).name
        target.write_bytes((work / "source_snapshot" / Path(name).name).read_bytes())
        sources.append(target)
    if mutation:
        target = stage / "div_floor.sv"
        old, new = mutation
        text = target.read_text()
        if text.count(old) != 1:
            raise ValueError("mutation needs exactly one divider target")
        target.write_text(text.replace(old, new))
    tb = stage / "arithmetic_audit_tb.sv"
    write_testbench(tb, fmt)
    sources.append(tb)
    env = os.environ.copy()
    alias_runtime(work, env, version)
    binary = stage / "obj/Varithmetic_audit_tb"
    checked_run(
        [
            *command,
            "--binary",
            "--timing",
            "--assert",
            "--top-module",
            "arithmetic_audit_tb",
            "-j",
            "1",
            "-Wno-fatal",
            "-Werror-PINMISSING",
            "-Werror-SELRANGE",
            *verilator_compiler_args(),
            f"-I{include}",
            "--Mdir",
            str(binary.parent),
            *map(str, sources),
        ],
        cwd=stage,
        env=env,
        log=out / "compile.log",
        timeout=timeout,
    )
    if not binary.is_file():
        raise RuntimeError("compiler produced no new binary")
    args = [
        str(binary),
        f"+VDIR={work/'vectors'}",
        f"+expected_mac={counts['mac']}",
        f"+expected_adc={counts['adc']}",
        f"+expected_div={counts['div']}",
        f"+expected_flags={counts['flag_sequences']}",
    ]
    if mutation:
        log = out / "run.log"
        with log.open("w") as stream:
            cp = subprocess.run(
                args,
                cwd=stage,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        output = log.read_text()
        write_json(
            out / "run.log.command.json",
            {"argv": args, "raw_returncode": cp.returncode, "log_sha256": digest(log)},
        )
        if (
            cp.returncode == 0
            or "DIV mismatch" not in output
            or "ARITHMETIC_AUDIT_COMPLETE" in output
        ):
            raise RuntimeError("mutation did not produce the expected real divider failure")
        record = {
            "status": "EXPECTED_FAIL_DETECTED",
            "raw_returncode": cp.returncode,
            "failure": next(x for x in output.splitlines() if "DIV mismatch" in x),
        }
    else:
        output = checked_run(args, cwd=stage, env=env, log=out / "run.log", timeout=timeout)
        found = completion(
            output, "ARITHMETIC_AUDIT_COMPLETE", ("mac", "adc", "div", "flag_sequences")
        )
        if found != {key: counts[key] for key in found}:
            raise ValueError("actual checks differ from explicit vector counts")
        for marker, key in (
            ("MAC_INDEPENDENT_PASS", "mac"),
            ("ADC_INDEPENDENT_PASS", "adc"),
            ("DIV_INDEPENDENT_PASS", "div"),
            ("OUTPUT_FLAG_SEQUENCE_PASS", "flag_sequences"),
        ):
            field = "scenarios" if key == "flag_sequences" else "cases"
            if completion(output, marker, (field,))[field] != counts[key]:
                raise ValueError("component subset count differs")
        record = {"status": "PASS", "counts": found}
    record.update(
        {
            "run_log_sha256": digest(out / "run.log"),
            "compiled_source_sha256": {p.name: digest(p) for p in sources},
            "math_header_sha256": digest(include / "rtl_params.vh"),
        }
    )
    write_json(out / "result.json", record)
    return record


def main(argv=None) -> int:
    """要求显式格式及输出路径；失败记录不标成功，也不复用旧编译结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--format-json", type=Path, required=True)
    parser.add_argument("--negative-controls", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    out = None
    manifest = {"schema_version": 1, "status": "RUNNING", "scope": "COMPONENT_SYNTHETIC_DIGITAL"}
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        fmt = load_format(args.format_json)
        out = new_output(args.out)
        command = command_for("verilator")
        version = checked_run(
            [*command, "-V"],
            cwd=out,
            env=os.environ.copy(),
            log=out / "verilator-version.log",
            timeout=min(args.timeout, 30),
        )
        with tempfile.TemporaryDirectory(prefix="sar_math_only_") as temporary:
            work = Path(temporary)
            snapshot = work / "source_snapshot"
            snapshot.mkdir()
            source_hashes = {}
            for name in SOURCES:
                target = snapshot / Path(name).name
                target.write_bytes((REPO / name).read_bytes())
                source_hashes[name] = digest(target)
            saved = out / "source_snapshot"
            saved.mkdir()
            for source in snapshot.iterdir():
                (saved / source.name).write_bytes(source.read_bytes())
            vectors = work / "vectors"
            vectors.mkdir()
            counts = generate_vectors(vectors, fmt)
            (out / "vectors").mkdir()
            for vector in vectors.iterdir():
                (out / "vectors" / vector.name).write_bytes(vector.read_bytes())
            (out / "format.json").write_bytes(args.format_json.read_bytes())
            manifest.update(
                {
                    "format": fmt,
                    "format_json_sha256": digest(args.format_json),
                    "counts": counts,
                    "tool_command": command,
                    "verilator_version": version,
                    "sources_sha256": source_hashes,
                    "vectors_sha256": {p.name: digest(p) for p in vectors.iterdir()},
                    "coverage_boundary": "Raw mathematical units only; no physical ADC/device binding.",
                }
            )
            write_json(out / "manifest.json", manifest)
            manifest["positive"] = run_case(
                out / "positive", work, fmt, command, version, counts, args.timeout
            )
            if args.negative_controls:
                manifest["negative_controls"] = {}
                changes = {
                    "wide_denominator_guard": (
                        "if (!dv_large && !trial_difference[W_R]) begin",
                        "if (!trial_difference[W_R]) begin",
                    ),
                    "negative_floor": ("(r_next != 0)", "1'b0"),
                }
                for name, change in changes.items():
                    manifest["negative_controls"][name] = run_case(
                        out / name,
                        work,
                        fmt,
                        command,
                        version,
                        counts,
                        args.timeout,
                        mutation=change,
                    )
            if any(digest(REPO / name) != expected for name, expected in source_hashes.items()):
                raise RuntimeError(
                    "arithmetic source changed during execution; frozen snapshot retained"
                )
            manifest["status"] = "PASS"
            write_json(out / "manifest.json", manifest)
            print(
                "ARITHMETIC_COMPONENT_COMPLETE "
                + " ".join(
                    f"{key}={counts[key]}" for key in ("mac", "adc", "div", "flag_sequences")
                )
                + " scope=COMPONENT_SYNTHETIC_DIGITAL"
            )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        if out is not None:
            manifest.update({"status": "FAIL", "error": str(error)})
            write_json(out / "manifest.json", manifest)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
