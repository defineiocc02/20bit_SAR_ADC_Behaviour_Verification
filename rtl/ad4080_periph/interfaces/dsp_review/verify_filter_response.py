# =============================================================================
# 文件名     : verify_filter_response.py
# 模块名     : verify_filter_response
# 版本       : v1.0
# 功能       : 通过真实 RTL 的整数输出测三种滤波器全倍率及钳位档频响。
#
# 设计说明   :
#   * 正弦逐点测采样码拟合响应。解析幅度和独立滚动窗口只提供对照，
#     既不代替实际输出，也不从手册值填充测试结果。
#   * 抽取率、复位优先级、停样排空和位真数值同一入口完成。
#   * 表 28 按字面 -3.000 dB 的输入 Fs 归一交点。读图误差、印刷
#     半末位和有限字长误差分别登记，均不作为器件保证公差。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""回放实际滤波 RTL，输出频响、启动时序和手册逐项对照。"""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ============================================================================
# 1. 扫描频点和动态激励
# ============================================================================
MODES = {"sinc1": 1, "sinc5": 2, "sinc5_comp": 3}
DELAY = {1: 1, 2: 4, 3: 6}  # 从选定抽取点接受到实际叶输出的核心间隔
AMPLITUDE_CODES = 262144
OUTPUT_POINTS = 96
PASSBAND_U = 0.1761303024  # 参考图 106 N=2，从 f/Fs 换算为 U=Nf/Fs
# 第一段从对应 f/Fs=.13 起，避开图 105 约 .125167 处的近竖直段。
STOP_U = ((0.26, 0.4), (0.4, 0.6), (0.6, 0.75), (0.75, 1.0))
ARRAYS = ("sinc1_decim.sv", "sinc5_decim.sv", "sinc5_comp.sv", "sinc5_comp_coeff_lut.svh")


@dataclass(frozen=True)
class Profile:
    """一档量测。n 是一级抽取，d 是对外总倍率，二者在 PF1 不同。"""
    name: str
    code: int
    n: int
    d: int
    printed_bw: float | None
    half_printed_unit: float | None


@dataclass(frozen=True)
class Trial:
    """一轮激励。kind 0 正弦，1 动态整数，2 清同步，3 异步复位。"""
    profile: Profile
    kind: int
    f: float
    phase: float
    warm: int
    count: int


def read_targets(path):
    """读取输入 Fs 单位的独立手册表格和读图数据。"""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    profiles, figures = [], defaultdict(list)
    for row in rows:
        name = row["filter"]
        code = int(row["sinc_dec_rate_bits"], 2)
        key = (name, code)
        # 选频点的印刷带宽与读图目标分开加载。图中墨线也可能和印刷表有出入。
        if row["source_kind"] == "printed_table":
            profiles.append(Profile(name, code, int(row["sinc_stage_decimation_n"]),
                                    int(row["total_decimation_d"]), float(row["value"]),
                                    float(row["half_print_unit_assumed"])))
        else:
            figures[key].append(row)
    if len(profiles) != 26 or len({(p.name, p.code) for p in profiles}) != 26:
        raise ValueError("Table 28 requires 26 distinct printed-rate targets")
    # 同样的动态输入再送编码 15，核对三条链的最大倍率钳位。
    for name in MODES:
        high = max((p for p in profiles if p.name == name), key=lambda p: p.code)
        profiles.append(Profile(name, 15, high.n, high.d, None, None))
    return profiles, figures


def graph_band(row, profile):
    """返回图中峰值所属的输入 Fs 归一频段。"""
    match = re.search(r"f/(fs|fOUT) band \[([^,]+),([^\]]+)\]|band f/(fs|fOUT) \[([^,]+),([^\]]+)\]",
                      row["note"])
    if not match:
        raise ValueError(f"figure band not identified for {row['source_id']}")
    unit, low, high = match.groups()[:3] if match.group(1) else match.groups()[3:]
    scale = profile.d if unit == "fOUT" else 1  # Fig.107 的代表阻带以输出速率归一
    return float(low) / scale, float(high) / scale


def alias_edge(f, d):
    """确定输出 Nyquist 或 DC 的退化点，使用两次正交注入恢复幅度。"""
    doubled = 2 * d * f
    return abs(doubled - round(doubled)) < 1e-11


def read_curve_holdout(path):
    """加载图 106 未参加逆向拟合的 46 个中心点及读图包络。"""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["split"] == "holdout"]
    if len(rows) != 46:
        raise ValueError("expected 46 interleaved Fig. 106 holdout points")
    return rows


def make_trials(profiles, figures, curve_holdout):
    """按固定种子生成全部测试，频点以输入 Fs 为单位。"""
    random = np.random.default_rng(20261120)
    trials, labels = [], {}
    for p in profiles:
        key = (p.name, p.code)
        # 动态向量覆盖完整 FIR 历史。两个取消用例在组末覆盖在途输出。
        trials.extend(Trial(p, kind, 0.0, 0.0, 0, 48 if kind == 1 else 1) for kind in (1, 2, 3))
        if p.printed_bw is None:
            continue
        points = {0.0: {"dc"}}

        def add(values, role):
            for value in values:
                f = float(value)
                if 0 < f <= 0.5:
                    points.setdefault(f, set()).add(role)

        # 密采样跨过 -3.000，附近的窄括区用于估计实际交点。
        add(p.printed_bw * np.linspace(0.84, 1.16, 49), "bandwidth")
        # 该组仅与独立解析模型比较；另有不参与拟合的真实图线样点。
        add(random.uniform(0.02, 0.60, 13) / p.n, "holdout")
        add([0.5 / p.n, 0.65 / p.n, 0.85 / p.n, 1.0 / p.n], "representative")
        if p.name == "sinc5_comp":
            add(np.linspace(0, PASSBAND_U, 57) / p.n, "passband")
            add(np.linspace(PASSBAND_U, 0.187, 21) / p.n, "passband_edge")
            for low, high in STOP_U:
                add(np.linspace(low, high, 21) / p.n, "stopband")
            if p.code == 0:
                add((row["f_over_fs"] for row in curve_holdout), "fig106_holdout")
        for row in figures[key]:
            if row["x_over_fs"]:
                add([float(row["x_over_fs"])], "figure_anchor")
            if row["metric"] == "max_gain_db_in_band":
                add(np.linspace(*graph_band(row, p), 25), "figure_peak_band")

        # 48 个 PF1 输出足以覆盖 35 抽头和 CIC 历史，随后只使用稳定输出拟合。
        warm = {"sinc1": 4, "sinc5": 8, "sinc5_comp": 48}[p.name]
        for f, roles in sorted(points.items()):
            labels[key, f] = "+".join(sorted(roles))
            phases = (0.0, math.pi/2) if alias_edge(f,p.d) else (0.37,)
            trials.extend(Trial(p, 0, f, phase, warm, OUTPUT_POINTS) for phase in phases)
    return trials, labels


# ============================================================================
# 2. 实际 Verilator 码流和位真对照
# ============================================================================
def run_checked(arguments, directory):
    """执行在独立目录中的工具，任何编译和运行失败都使门禁非零退出。"""
    result = subprocess.run(arguments,cwd=directory,capture_output=True,text=True,timeout=900,check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} exit={result.returncode}\n"
                           f"{result.stdout}{result.stderr}")
    return result.stdout


def run_testbench(repo, work, verilator, trials):
    """编译现有 RTL 并驱动全部频点，返回逐字采集的轨迹文件。"""
    with (work/"trials.txt").open("w",encoding="utf-8") as stream:
        for index, t in enumerate(trials):
            p=t.profile
            stream.write(f"{index} {MODES[p.name]} {p.code} {p.d} {t.warm} {t.count} "
                         f"{t.kind} {t.f:.17g} {t.phase:.17g}\n")
    for name in ARRAYS:
        shutil.copyfile(repo/"rtl/ad4080_dsp"/name,work/name)
    # 保留发布版叶和文件名。Verilator 构建及原始码轨迹均不会进入生产源码目录。
    bench=Path(__file__).with_name("tb_filter_response.sv")
    shutil.copyfile(bench,work/bench.name)
    run_checked([verilator,"--binary","--timing","--assert","-Wall","--compiler","clang",
                 "--output-split","0","--output-split-cfuncs","0","-CFLAGS","-std=c++20",
                 "-j","2","-I.","--top-module",bench.stem,"--Mdir","obj",
                 *ARRAYS[:-1],bench.name],work)
    log = run_checked([f"./obj/V{bench.stem}"],work)
    # 单独检查执行完成，防止取消用例没有输出时被漏跑却仍满足行计数。
    if f"FILTER_RESPONSE_TRACE_COMPLETE trials={len(trials)}\n" not in log:
        raise RuntimeError("incomplete filter data capture")
    return work/"observed.txt"


def rolling_sum(x, n):
    """独立有限窗口累加，前史为零，所有中间量使用无限精度整数。"""
    running = 0
    result = []
    for index, v in enumerate(x):
        running += v
        # 只减掉确实已经进入过的窗口项，启动阶段自然是前史零填充。
        if index >= n:
            running -= x[index-n]
        result.append(running)
    return result


def dynamic_input(n):
    """返回已知的有符号 20 位动态样本，含两侧轨端。"""
    if n % 79 == 0:
        return -524288
    if n % 79 == 1:
        return 524287
    return (n*30119 + (n%37)*7919) % 880000 - 440000


def int_reference(trial, taps, frac):
    """按有限窗口和 DSP 定点规则独立推导动态试验的每一个码。"""
    p = trial.profile
    x = [dynamic_input(i) for i in range(p.d*trial.count)]
    order = 1 if p.name == "sinc1" else 5
    for _ in range(order):
        x = rolling_sum(x, p.n)
    scale = p.n**order
    y = [(v + scale//2)//scale for v in x[p.n-1::p.n]]  # 此处先独立量化 CIC，再喂 PF1
    if p.name != "sinc5_comp":
        return y
    # 真实 PF1 从第二个 SINC5 值开始，每隔一值输出。乘前历史零补。
    q = [0]*(len(taps)-1)+y
    floor_half = 1 << (frac-1)
    out = []
    for index in range(1,len(y),2):
        offset = index+len(taps)-1
        v = (sum(c*q[offset-k] for k,c in enumerate(taps))+floor_half) >> frac
        out.append(max(-524288,min(524287,v)))  # 符号判定在全精度整和后做一次
    return out


def check_trace(path, trials, profiles_taps, frac):
    """完整检查输出时序与位真动态样本。返回用于频响拟合的实际输出码组。"""
    samples = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            index, ordinal, clock, code = map(int,line.split())
            t = trials[index]
            if ordinal != t.warm+len(samples[index])+1:
                raise RuntimeError(f"missing/duplicate result in trial {index}")
            leaf_sample = ordinal*t.profile.d
            # 气泡在每个十一笔边界之前插入。首笔之前的气泡已被相对时钟扣除。
            bubbles = (leaf_sample-1)//11 if t.kind == 1 else 0
            if clock != leaf_sample+bubbles+DELAY[MODES[t.profile.name]]:
                raise RuntimeError(f"RTL output phase mismatch in trial {index} ordinal {ordinal}")
            samples[index].append(code)
    tones, dynamic = defaultdict(list), {}
    for index,t in enumerate(trials):
        codes = samples[index]
        expected_count = 0 if t.kind >= 2 else t.count  # 停样排空与清除在途为相反结果
        if len(codes) != expected_count:
            raise RuntimeError(f"trial {index} has {len(codes)} outputs, expected {expected_count}")
        p = t.profile
        key=(p.name,p.code)
        if t.kind == 0:
            tones[key,t.f].append(codes)
        elif t.kind == 1:
            expected = int_reference(t, profiles_taps[min(7,p.code)], frac)
            if codes != expected:
                first=next(k for k,(a,b) in enumerate(zip(codes,expected,strict=True)) if a!=b)
                raise RuntimeError(f"dynamic mismatch {key} word {first}: RTL {codes[first]}, model {expected[first]}")
            dynamic[key] = len(codes)
    return tones,dynamic


# ============================================================================
# 3. 从实测输出估计增益和有限字长界
# ============================================================================
def estimate_tone(captures, f_out):
    """返回幅值、拟合 RMS（码）及幅值误差相对于点态误差的放大系数。"""
    count=len(captures[0])
    i=np.arange(count)
    if alias_edge(f_out,1):
        # 折叠到 DC/Nyquist 时用正交实验代替病态正弦拟合。
        basis=(-1.0)**i if int(round(2*f_out)) % 2 else np.ones(count)
        vectors = [np.asarray(item,dtype=float) for item in captures]
        coeffs = [float(np.mean(v*basis)) for v in vectors]
        rms = max(float(np.sqrt(np.mean((v-c*basis)**2))) for v,c in zip(vectors,coeffs,strict=True))
        return math.hypot(*coeffs),rms,math.sqrt(2)
    angle=2*np.pi*np.remainder(f_out,1.0)*i
    design=np.column_stack((np.sin(angle),np.cos(angle),np.ones(count)))
    linear_fit=np.linalg.pinv(design)
    values=np.asarray(captures[0],dtype=float)
    coeffs=linear_fit@values
    rms=float(np.sqrt(np.mean((values-design@coeffs)**2)))
    # 点态 L∞ 码误差经两个线性拟合系数后的上界，再用幅度的反三角不等式。
    gain_bound=math.hypot(*np.abs(linear_fit[:2]).sum(axis=1))
    return math.hypot(*coeffs[:2]),rms,gain_bound


def continuous_magnitude(p, f, taps, frac):
    """比较全精度基带传递和现有码表，刻画实际输出量化的限度。"""
    # 归一矩形窗在 DC 极限为 1。np.sinc 避免直接用 sin/sin 产生 0/0。
    magnitude=abs(float(np.sinc(p.n*f)/np.sinc(f)))**(1 if p.name=="sinc1" else 5)
    if p.name == "sinc5_comp":
        # FIR 系数按一级 CIC 的较慢速率解释，输出二倍抽取只改变分析轴。
        angles = np.exp(-2j*np.pi*f*p.n*np.arange(len(taps)))
        magnitude *= abs(np.dot(taps,angles)) / (1 << frac)
    return magnitude


def to_db(gain):
    """零增益没有有限 dB 值，用 None 表示。"""
    return 20*math.log10(gain) if gain>0 else None


def measured_rows(profiles, labels, tones, profiles_taps, frac):
    """从实际输出计算每频点响应，并检查解析幅度受整数噪声界约束。"""
    data=defaultdict(list)
    for p in profiles:
        key=(p.name,p.code)
        if p.printed_bw is None:
            continue
        taps=profiles_taps[min(p.code,7)]
        dc,_,_=estimate_tone(tones[key,0.0],0.0)
        dc_model=continuous_magnitude(p,0.0,taps,frac)
        if dc <= 0 or dc_model <= 0:
            raise RuntimeError(f"nonpositive measured DC gain for {key}")
        # 输入量化和 SINC 舍入各半码；PF1 加权放大前级 1 码，再有半码最终舍入。
        per_sample=1.0 if p.name != "sinc5_comp" else sum(map(abs,taps))/(1<<frac)+0.5
        for (_,f), role in sorted((item for item in labels.items() if item[0][0]==key),key=lambda item:item[0][1]):
            amp,rms,fit_bound=estimate_tone(tones[key,f],f*p.d)
            model=continuous_magnitude(p,f,taps,frac)
            amp_error=abs(amp-AMPLITUDE_CODES*model)
            allowed=fit_bound*per_sample
            if amp_error > allowed+1e-5:
                raise RuntimeError(f"tone exceeds integer error bound {key} f={f}: {amp_error}>{allowed}")
            # dB 用同档实际 DC 标定。原始幅度误差用码值，便于看近零的阻带。
            data[key].append({
                "filter":p.name,"code":p.code,"sinc_decimation":p.n,"total_decimation":p.d,
                "f_over_fs_in":f,"f_over_fs_out":f*p.d,"magnitude_db":to_db(amp/dc),
                "model_magnitude_db":to_db(model/dc_model),"tone_residual_rms_lsb20":rms,
                "amplitude_error_lsb20":amp_error,"amplitude_error_bound_lsb20":allowed,"role":role,
            })
    return data


# ============================================================================
# 4. 带宽、图表差异和公开结果
# ============================================================================
def crossing(rows, level):
    """从两侧实际采集点对 dB 阈值线性内插，返回交点和频率括区。"""
    for left,right in zip(rows,rows[1:]):
        a,b=left["magnitude_db"],right["magnitude_db"]
        if a is None or b is None:  # 真零无 dB，不作为有限斜率插值端点
            continue
        if a > level >= b:
            lo,hi=left["f_over_fs_in"],right["f_over_fs_in"]
            return (lo+(hi-lo)*(level-a)/(b-a),lo,hi)
    raise RuntimeError(f"no bounded measured {level:.3f} dB crossing")


# 峰值只从已观测频点统计，保留原图容差和表格印刷精度的不同语义。
# 每次换档含真实复位，旧抽取相位不能替下一档提供有效码。
def peak_in_band(rows, low, high):
    """报告频点网格实测峰值及其位置。"""
    valid=[r for r in rows if low-1e-14 <= r["f_over_fs_in"] <= high+1e-14 and r["magnitude_db"] is not None]
    peak=max(valid,key=lambda r:r["magnitude_db"])
    return peak["magnitude_db"], peak["f_over_fs_in"]


def summarize(profiles, data, dynamic):
    """按独立标准汇总实测带宽、通带波动和代表阻带。"""
    summary=[]
    for p in profiles:
        key=(p.name,p.code)
        if p.printed_bw is None:
            continue
        rows=data[key]
        f3,lo,hi=crossing(rows,-3.0)
        # 半个印刷末位只描述读表比较。与目标精度、宏公差分开。
        target=p.printed_bw
        half=p.half_printed_unit
        pass_rows=[r for r in rows if r["f_over_fs_in"]*p.n <= PASSBAND_U+1e-14]
        pass_db=[r["magnitude_db"] for r in pass_rows]
        peak,_=peak_in_band(rows,0.285/p.n,0.375/p.n) if p.name=="sinc5_comp" else (None,None)
        hold=[r for r in rows if "holdout" in r["role"].split("+")]  # 不混入 Fig.106 读图测试
        summary.append({
            "filter":p.name,"code":p.code,"sinc_decimation":p.n,"total_decimation":p.d,
            "measured_bw_3db_over_input_fs":f3,"bw_bracket_low":lo,"bw_bracket_high":hi,
            "table_bw_3db_over_input_fs":target,"rtl_minus_table_f_over_fs":f3-target,
            "half_print_unit":half,"within_printed_half_unit":abs(f3-target)<=half,
            "pf1_passband_low_db":min(pass_db) if p.name=="sinc5_comp" else None,
            "pf1_passband_high_db":max(pass_db) if p.name=="sinc5_comp" else None,
            "pf1_sampled_within_0p1_db":min(pass_db)>=-0.1 and max(pass_db)<=0.1 if p.name=="sinc5_comp" else None,
            "pf1_representative_stop_peak_db":peak,
            "max_tone_residual_rms_lsb20":max(r["tone_residual_rms_lsb20"] for r in rows),
            "holdout_amplitude_error_rms_lsb20":math.sqrt(sum(r["amplitude_error_lsb20"]**2 for r in hold)/len(hold)),
            "max_holdout_amplitude_error_lsb20":max(r["amplitude_error_lsb20"] for r in hold),
            "bit_exact_dynamic_words":dynamic[key],"tone_frequencies":len(rows),
        })
    return summary


def compare_figures(profiles, figures, data):
    """把图中读数、可读墨线包络和实际数值并排列出。"""
    result=[]
    for p in profiles:
        key=(p.name,p.code)
        if key not in figures:
            continue
        rows=data[key]
        for manual in figures[key]:
            metric=manual["metric"]
            at_f=None
            if metric=="bw_3db_over_input_fs":
                measured,_,_=crossing(rows,-3.0)
            elif metric=="first_minus0p1_over_fs":
                measured,_,_=crossing(rows,-0.1)
            elif metric=="gain_db_at_f":
                at_f=float(manual["x_over_fs"])
                measured=next(r["magnitude_db"] for r in rows if r["f_over_fs_in"]==at_f)
            elif metric=="max_gain_db_in_band":
                measured,at_f=peak_in_band(rows,*graph_band(manual,p))
            else:
                raise ValueError(f"unhandled figure metric {metric}")
            ink=re.search(r"graphic ink envelope \[([^,]+),([^\]]+)\]",manual["note"])
            low,high=(float(v) for v in ink.groups())
            ref=float(manual["value"])
            delta=measured-ref
            result.append({
                "filter":p.name,"code":p.code,"sinc_decimation":p.n,"total_decimation":p.d,
                "source":manual["source_id"],"metric":metric,"unit":manual["unit"],
                "figure_centerline":ref,"ink_low":low,"ink_high":high,
                "rtl_measured":measured,"rtl_minus_ink_center":delta,
                "rtl_in_ink_interval":low<=measured<=high,"rtl_peak_f_over_fs_in":at_f,
                "table_minus_ink_center":p.printed_bw-ref if metric=="bw_3db_over_input_fs" else None,
                "table_bw_in_ink_interval":low<=p.printed_bw<=high if metric=="bw_3db_over_input_fs" else None,
                # 例如 Fig.104 N=16 的两种区间无交，并非半个末位能解释。
                "printed_range_overlaps_ink":p.printed_bw-p.half_printed_unit<=high and
                    p.printed_bw+p.half_printed_unit>=low if metric=="bw_3db_over_input_fs" else None,
            })
    return result


def compare_curve_holdout(curve, response):
    """记录 Fig. 106 留出点对实际 RTL 的逐点 dB 偏差。"""
    measured={row["f_over_fs_in"]:row["magnitude_db"] for row in response}
    result = []
    for row in curve:
        f = float(row["f_over_fs"])
        center = float(row["center_db"])
        value = measured[f]
        low = float(row["graphic_lower_db"])
        high = float(row["graphic_upper_db"])
        result.append({
            "f_over_fs_in": f, "center_db": center, "measured_db": value,
            "rtl_minus_center_db": value-center, "graphic_lower_db": low,
            "graphic_upper_db": high, "rtl_in_ink_interval": low <= value <= high,
        })
    return result


def write_csv(path, rows):
    """输出统一表头，None 保留为空白单元格。"""
    with path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main():
    """用当前仓库的实际 RTL 再生三份 CSV 及 JSON 证据。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[4])
    parser.add_argument("--output",type=Path,required=True,help="保存实测 CSV/JSON 的目录")
    parser.add_argument("--manual-targets",type=Path,default=Path(__file__).with_name("manual_filter_targets.csv"))
    parser.add_argument("--verilator",default=os.environ.get("VERILATOR","verilator"))
    args=parser.parse_args()
    repo=args.repo.resolve()
    source=repo/"rtl/ad4080_dsp/design/sinc5_comp_coefficients.json"
    holdout_source=repo/"rtl/ad4080_dsp/design/sinc5_comp_fig106_anchors.csv"
    # 固定一次编译输入。并行编辑期间有任何源差异，由同版重新跑回执。
    source_files={name:repo/"rtl/ad4080_dsp"/name for name in ARRAYS}
    source_files.update(coefficients=source,manual_targets=args.manual_targets,
                        figure_holdout=holdout_source,
                        testbench=Path(__file__).with_name("tb_filter_response.sv"),
                        driver=Path(__file__))
    digests={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in source_files.items()}
    coeff=json.loads(source.read_text())
    frac=coeff["frac_bits"]
    taps={item["rate"]:item["coefficients_q"] for item in coeff["modes"]}
    if sorted(taps)!=list(range(8)):
        raise ValueError("missing PF1 rate coefficients")
    profiles,figures=read_targets(args.manual_targets)
    curve_holdout=read_curve_holdout(holdout_source)
    trials,labels=make_trials(profiles,figures,curve_holdout)
    with tempfile.TemporaryDirectory(prefix="ad4080-filter-response-") as work:
        path=run_testbench(repo,Path(work),args.verilator,trials)
        tones,dynamic=check_trace(path,trials,taps,frac)
    measured=measured_rows(profiles,labels,tones,taps,frac)
    summary=summarize(profiles,measured,dynamic)
    graphic=compare_figures(profiles,figures,measured)
    holdout=compare_curve_holdout(curve_holdout,measured["sinc5_comp",0])
    if any(hashlib.sha256(path.read_bytes()).hexdigest()!=digests[name] for name,path in source_files.items()):
        raise RuntimeError("filter sweep source changed during execution")
    args.output.mkdir(parents=True,exist_ok=True)
    write_csv(args.output/"filter_response.csv",[item for rows in measured.values() for item in rows])
    write_csv(args.output/"filter_summary.csv",summary)
    write_csv(args.output/"figure_comparison.csv",graphic)
    write_csv(args.output/"fig106_holdout.csv",holdout)
    printed_pass=sum(row["within_printed_half_unit"] for row in summary)
    ripple_pass=sum(row["pf1_sampled_within_0p1_db"] is True for row in summary)
    report={
        "source_sha256":digests,
        "measurement":"tone fitted to actual Verilator codes after settling; degenerate output bins use phase quadrature",
        "frequency_reference":"f/Fs of input. f/Fs_out equals f/Fs times total decimation.",
        "bandwidth_definition":"exact -3.000 dB; half of printed unit is a comparison convention",
        "passband_stage_u":PASSBAND_U,
        "scope":"synthetic signed20 numerical filters",
        "trials":len(trials),"checked_dynamic_words":sum(dynamic.values()),
        "checked_cancel_trials":2*len(profiles),"checked_clamped_profiles":3,
        "printed_pass":printed_pass,"pf1_sampled_passband_pass":ripple_pass,
        "fig106_holdout":{"count":len(holdout),
                          "max_abs_error_db":max(abs(row["rtl_minus_center_db"]) for row in holdout),
                          "rms_error_db":math.sqrt(math.fsum(row["rtl_minus_center_db"]**2 for row in holdout)/len(holdout))},
        "summary":summary,"figure_comparison":graphic,
    }
    (args.output/"filter_validation.json").write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print(f"AD4080_FILTER_RESPONSE_COMPLETE rates={len(summary)} clamps=3 dynamic_words={sum(dynamic.values())} "
          f"tones={sum(len(v) for v in measured.values())} cancel_trials={2*len(profiles)}")
    # 手册图墨比较保持为资料差异。对明示的工程带宽和采样通带则给出门禁。
    if printed_pass!=26 or ripple_pass!=8:
        print(f"FILTER_RESPONSE_ENGINEERING_OPEN table={printed_pass}/26 pf1_passband={ripple_pass}/8")
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
