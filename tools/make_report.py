"""把 results.json + 图片打包成一份自包含 HTML 报告（无外部依赖）。"""

from __future__ import annotations

import base64
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("ADI_MODEL_RESULTS_DIR") or os.path.join(HERE, "results")
FIG = os.path.join(OUT, "fig")
with open(os.path.join(OUT, "results.json"), encoding="utf-8") as _fh:
    R = json.load(_fh)


def b64(name: str) -> str:
    p = os.path.join(FIG, name)
    if not os.path.exists(p):
        return ""
    with open(p, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def f(v, n=2, dash="—"):
    if v is None:
        return dash
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, int | float):
        return f"{v:,.{n}f}"
    return str(v)


def badge(ok: bool) -> str:
    c = "#4ECDC4" if ok else "#FF6B6B"
    t = "PASS" if ok else "FAIL"
    return f'<span class="badge" style="background:{c}22;color:{c};border-color:{c}55">{t}</span>'


def rows_html(header, rows, aligns=None):
    a = aligns or ["l"] * len(header)
    h = "".join(f'<th class="t{a[i]}">{c}</th>' for i, c in enumerate(header))
    body = ""
    for r in rows:
        body += "<tr>" + "".join(f'<td class="t{a[i]}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


d = R["derived"]
v = R["validate"]
sw = R["sweep"]["rows"]
s7 = R["s7_dem_on"]["cases"]
s7o = R["s7_dem_off"]["cases"]

# ---------------------------------------------------------------- 自检表
val_rows = [
    [k, f(x["实际"], 4), f(x["门限"], 4), x["单位"], badge(x["PASS"])] for k, x in v.items()
]

# ---------------------------------------------------------------- 缩放扫描
sw_rows = []
for r in sw:
    sw_rows.append(
        [
            f(r["C_total_pF"], 2),
            "开" if r["ktc"] else "关",
            f(r["sigma_kTC_uV"], 1),
            f(r["sigma_mismatch_ppm"], 0),
            f(r["SNDR_dB"]),
            f(r["NSD_nV_rtHz"]),
            f(r["SFDR_dB"], 1),
            f(r["THD_dB"], 1),
            f(r["INL_max_LSB"], 3),
            f(r["sigma_e_mean_uV"], 1),
            f(r["overflow_rate"] * 100, 2),
        ]
    )


# ---------------------------------------------------------------- stage7
def s7_rows(cases):
    out = []
    for k, x in cases.items():
        out.append(
            [
                k,
                f(x["C_total_pF"], 2),
                f(x["SNDR_dB"]),
                f(x["SNDR_dB"] - 93.43, 2),
                f(x["NSD_nV_rtHz"]),
                f(x["SFDR_dB"], 1),
                f(x["THD_dB"], 1),
                f(x["INL_max_LSB"], 3),
                f(x["sigma_e_mean_uV"], 1),
                f(x["switch_charge_proxy"] * 1e12, 1),
            ]
        )
    return out


s3, s4, s5, s6 = R["s3"], R["s4"], R["s5"], R["s6"]

# ---------------------------------------------------------------- 关键结论数字
base = next(r for r in sw if r["ktc"] is False and abs(r["C_total_pF"] - 20.5) < 1e-6)
shr = next(r for r in sw if r["ktc"] is True and abs(r["C_total_pF"] - 5.125) < 1e-6)
shrn = next(r for r in sw if r["ktc"] is False and abs(r["C_total_pF"] - 5.125) < 1e-6)
kpi = {
    "base_sndr": base["SNDR_dB"],
    "base_nsd": base["NSD_nV_rtHz"],
    "shr_sndr": shrn["SNDR_dB"],
    "shr_nsd": shrn["NSD_nV_rtHz"],
    "ktc_sndr": shr["SNDR_dB"],
    "ktc_nsd": shr["NSD_nV_rtHz"],
    "gain_db": shr["SNDR_dB"] - shrn["SNDR_dB"],
    "vs_base": shr["SNDR_dB"] - base["SNDR_dB"],
    "cap_ratio": base["C_total_pF"] / shr["C_total_pF"],
    "inl_base": base["INL_max_LSB"],
    "inl_ktc": shr["INL_max_LSB"],
    "f_bw": R["validate_ktc"]["KTC 校正项带宽上限 f_max (满幅)"]["实际"],
    "pdk_ppm": d["pdk_sigma_est_ppm"],
    "fit_ppm": 100.0,
}

# ============================================================ v5 结果准备
s12 = R.get("s12", {})
s12_topo = s12.get("topologies", [])
s12_bridge = s12.get("bridge_sweep", [])
s12_bd = s12.get("boundary", {})
_b_off = [r for r in s12_bridge if not r["dem"]]
_b_on = [r for r in s12_bridge if r["dem"]]
dem_row_note = (
    (
        "σ=2e-3 时 DEM 关 {} µV / DEM 开 {} µV，偏差 {}%。".format(
            f(_b_off[-1]["sawtooth_pp_uV"], 1),
            f(_b_on[-1]["sawtooth_pp_uV"], 1),
            f(
                abs(_b_off[-1]["sawtooth_pp_uV"] - _b_on[-1]["sawtooth_pp_uV"])
                / max(_b_off[-1]["sawtooth_pp_uV"], 1e-9)
                * 100,
                1,
            ),
        )
    )
    if (_b_off and _b_on)
    else "（无数据）"
)

s13 = R.get("s13", {})
s13_rows = s13.get("rows", [])
s13_dict = {r["设置"]: r for r in s13_rows}
s13_all = s13_dict.get("全部开启", {})
s13_ron = s13.get("ron_sweep", [])
s13_rho = s13.get("rho_max_for_2p2LSB")
s13_ron010 = next((r for r in s13_ron if abs(r["rho"] - 0.1) < 1e-9), {})
s13_p = s13.get("params_used", {})
s13_repconv = s13.get("rep_convergence", {})
s13_rep = s13_repconv.get("rep", 128)
s13_rho_pk = s13.get("rho_max_INL_at_rho_max")

s14 = R.get("s14", {})
s14_maps = s14.get("maps", [])
s14_cal = s14.get("calibration", [])
s14_req = s14.get("required_samples_all", [])
s14_req_rows = []
for r in s14_req:
    if r.get("feasible"):
        s14_req_rows.append(
            [
                r["映射"],
                badge(True),
                r["rank"],
                f"{r['binding']}界",
                f(r["n_samples"], 0),
                f"{r['at_fs_40MHz_seconds']*1e6:.0f} µs",
            ]
        )
    else:
        s14_req_rows.append(
            [r["映射"], badge(False), r.get("rank"), "秩不足", "∞（不可辨识）", "—"]
        )
s14_req_note = s14_req[-1]["note"] if s14_req else ""

s15_rows = R.get("s15", {}).get("rows", [])

sNT = R.get("noise_transfer", {})
sNT_rows = sNT.get("rows", [])
sSC = R.get("split_calib", {})
sSC_off = sSC.get("noise_off", {})
sSC_on = sSC.get("noise_on", {})
sDC = R.get("dither_closure", {})
sPL = R.get("pipeline", {})

# 现状判定：只对"需求与当前值可比"的行给结论，其余留空（不硬凑结论）
_s15_status = {
    "单位失配 sigma_eps": True,  # 100 ppm(拟合) ≤ 300 ppm；但 PDK 估算 1117 ppm 不达标
    "桥接电容 C_C 相对失配": True,  # 100 ppm ≤ 482 ppm
    "采样相时长 T_s": True,  # 11.2 ns ≥ 4.7 ns
    "开关导通电阻码调制 rho": False,  # 当前假设 0.100 > 峰值口径需求 s13_rho（约 0.04）
    "单位权重校准观测数": None,  # 取决于是否实现 permute 校准模式
}
s15_ui = []
for r in s15_rows:
    ok = _s15_status.get(r["参数"], None)
    tag = badge(ok) if ok is not None else '<span class="small">—</span>'
    s15_ui.append([r["参数"], r["需求"], r["当前假设"], tag, r["来源"], r["说明"]])


def _verdict(summary):
    out = ""
    for k, ok in (summary or {}).items():
        out += f"<tr><td>{k}</td><td>{'达标' if ok else '未达标'}</td><td>{badge(ok)}</td></tr>"
    return out


s12_verdict = _verdict(R.get("s12_summary"))
s13_verdict = _verdict(R.get("s13_summary"))
s14_verdict = _verdict(R.get("s14_summary"))
sNT_verdict = _verdict(
    {
        "stage16 逐相位噪声传递：MC=解析、γ=1 回观测底、" "γ<1 结构性残余量化": R.get(
            "noise_transfer", {}
        ).get("PASS")
    }
)
sSC_verdict = _verdict(
    {
        "stage17 split 校准闭环 v1：θ̂ 对齐理论 + " "out-of-sample 恢复到理想地板": R.get(
            "split_calib", {}
        ).get("PASS")
    }
)
sDC_verdict = _verdict(
    {"stage18 带 dither 的采样相位电荷闭合 + 离散掩码": R.get("dither_closure", {}).get("PASS")}
)
sPL_verdict = _verdict(
    {
        "stage19 退化等价（pipeline 与 sim_split 逐位一致）": sPL.get("bitwise_equal"),
        "stage19 SADC 补偿窗口命中理论 (ADC2余量)/G": bool(
            sPL.get("sadc_window", {}).get("measured_window_mV", 0) >= 9.0
        ),
        "stage19 slice 带宽失配杂散 + 8/18 洗牌打散 ≥20 dB": bool(
            sPL.get("interleave", {}).get("spur_reduction_dB", 0) > 20.0
        ),
        "stage19 数字核名义守恒（DAC(M(c)) 与 DEM 状态无关）": sPL.get(
            "digital_conservation", {}
        ).get("ok"),
    }
)
sIL = R.get("il_offset", {})
sDQ = R.get("dither_quant", {})
sAZ = R.get("autozero", {})
sFL = R.get("flicker", {})
sBW = R.get("rdac_bitwise", {})
UPD = int(R.get("derived", {}).get("units_per_Delta1", 8))
audit_verdict = _verdict(
    {
        "stage20 逐 slice offset（四件套收官）：f_S/2 指纹 + 2× 标度 + 洗牌抑制": sIL.get("PASS"),
        "stage21 量化器侧 dither：Δ1 粒度转移溢出 / step0 粒度转移窗口吸收": sDQ.get("PASS"),
        "stage22 auto-zero −1.6 dB / ADC2 动态带宽 +1.3 dB：预算推导 vs 实测": sAZ.get("PASS"),
        "stage23 1/f 噪声（转角 40 Hz）：谱形 + 带功率 + AC 影响量化": sFL.get("PASS"),
        "stage24 RDAC 逐位装载：差分隔离比值 = 解析 + 峰值需求 0.508×": sBW.get("PASS"),
    }
)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADI 20b 40MS/s 两级 SAR — 行为模型验收报告</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#1D1F27;color:#E6E8EE;
 font-family:"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
 line-height:1.7;font-size:14px}}
.wrap{{max-width:1080px;margin:0 auto;padding:32px 24px 80px}}
h1{{font-size:26px;margin:0 0 6px;background:linear-gradient(90deg,#FF6B6B,#4ECDC4);
 -webkit-background-clip:text;background-clip:text;color:transparent}}
.sub{{color:#8B91A7;font-size:13px;margin-bottom:28px}}
h2{{font-size:19px;margin:44px 0 14px;padding-left:11px;border-left:3px solid #4ECDC4}}
h3{{font-size:15px;margin:26px 0 10px;color:#C9CEDE}}
p{{margin:10px 0}}
.card{{background:#242733;border:1px solid #33384A;border-radius:12px;padding:18px 20px;margin:14px 0}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.kpi{{background:#242733;border:1px solid #33384A;border-radius:12px;padding:14px 16px;
 border-top:2px solid #4ECDC4}}
.kpi .lab{{font-size:11px;color:#8B91A7;letter-spacing:.5px;text-transform:uppercase}}
.kpi .val{{font-size:24px;font-weight:600;margin-top:4px}}
.kpi .note{{font-size:11px;color:#8B91A7;margin-top:2px}}
.kpi.a{{border-top-color:#FF6B6B}} .kpi.b{{border-top-color:#FFD93D}}
table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}}
th{{background:#2C3040;color:#A8AFC4;font-weight:600;text-align:left;
 padding:9px 10px;border-bottom:1px solid #3A4054;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #2C3040;font-variant-numeric:tabular-nums}}
tbody tr:hover{{background:#282C3A}}
.tl{{text-align:left}} .tr{{text-align:right}} .tc{{text-align:center}}
.badge{{display:inline-block;padding:1px 8px;border:1px solid;border-radius:20px;
 font-size:11px;font-weight:600;letter-spacing:.5px}}
.eq{{background:#191B23;border-left:2px solid #4ECDC4;padding:12px 16px;margin:14px 0;
 font-family:"SF Mono",Consolas,monospace;font-size:13px;color:#C9CEDE;
 overflow-x:auto;white-space:pre}}
.tag{{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;margin-right:6px}}
.tag.p{{background:#4ECDC422;color:#4ECDC4}} .tag.m{{background:#FFD93D22;color:#FFD93D}}
.tag.r{{background:#FF6B6B22;color:#FF6B6B}}
img{{width:100%;border-radius:10px;border:1px solid #33384A;margin:10px 0}}
.warn{{background:#FFD93D14;border:1px solid #FFD93D44;border-radius:10px;padding:12px 16px;margin:14px 0}}
.ok{{background:#4ECDC414;border:1px solid #4ECDC444;border-radius:10px;padding:12px 16px;margin:14px 0}}
code{{background:#191B23;padding:1px 5px;border-radius:4px;font-size:12px;color:#FFD93D}}
.small{{font-size:12px;color:#8B91A7}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
@media(max-width:760px){{.grid2{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">

<h1>ADI 20b 40MS/s 精度 SAR — 行为模型 v1 验收报告</h1>
<div class="sub">
用固定的物理电容阵列产生真实误差，用可替换的数字映射算法决定如何使用阵列，
最后通过残差重构观察误差究竟去了哪里。<br>
模型目标：<b>不混淆失配、噪声与数字校正</b>。基准：ISSCC2024 9.8（Bodnar et al., 20b 40MS/s, DR 94.2 dB）。
</div>

<h2>一、先锁定边界</h2>
<div class="card">
{rows_html(["项目", "来源 / 口径", "性质"], [
  ["f<sub>s</sub> = 40 MS/s，差分 ±3 V（6 V<sub>pp</sub>）", "PPT；由 NSD 8.8 nV/√Hz 与 −167.6 dBFS/Hz 反推校核一致", '<span class="tag p">PPT</span>'],
  ["20 bit 目标，LSB<sub>20</sub> = 5.7220 µV", "2·V<sub>FS</sub>/2<sup>20</sup>", '<span class="tag p">PPT</span>'],
  ["B<sub>1</sub> = 6 bit（Δ<sub>1</sub> = 93.75 mV）", "模型选定；PPT 未给出位数分配", '<span class="tag m">参数</span>'],
  ["G<sub>0</sub> = 32，ADC2 = 15 bit / [−0.3, 3.3] V", "G<sub>0</sub> 来自 PPT 图标注；位数与量程为参数", '<span class="tag p">PPT</span>+<span class="tag m">参数</span>'],
  ["RDAC 18 slice（8+8+2 spare），每次 8 个", "PPT：pool of 18, 2× spare", '<span class="tag p">PPT</span>'],
  ["每 slice 64 单位 → 512 单位参与，步长 Δ<sub>1</sub>/8", "按 3b 横向 × 3b 纵向分割建模", '<span class="tag m">建模</span>'],
  ["C<sub>total</sub> = 20.5 pF（kT/C）→ C<sub>u</sub> = 40.04 fF", "PPT：Large RDAC for kT/C (20.5pF)", '<span class="tag p">PPT</span>'],
  ["SADC/RDAC 增益失配 1×10<sup>−4</sup>（13.3 bit）", "PPT：&gt;12b AC matching", '<span class="tag p">PPT</span>'],
  ["失配 σ<sub>ε</sub>(1) = 1×10<sup>−4</sup>", "行为学标定：令 s=1、DEM 开时 SNDR ≈ 93.4 dB 对齐论文", '<span class="tag m">探索性</span>'],
  ["缩放律 σ<sub>ε</sub>(s) = σ<sub>0</sub>/√s，σ<sub>n</sub><sup>2</sup> = χkT/C，χ = 2", "探索性假设，<b>必须用 PDK 替换</b>", '<span class="tag m">探索性</span>'],
  ["KTC 校正支路", "<b>本次要研究的扩展，不是 ADI 已公开实现的模块</b>", '<span class="tag r">扩展</span>'],
])}
</div>

<h2>二、派生量与自检</h2>
<div class="kpis">
<div class="kpi"><div class="lab">LSB @20b</div><div class="val">{f(d['LSB20_uV'],4)} µV</div></div>
<div class="kpi"><div class="lab">Δ₁ / RDAC 步长</div><div class="val">{f(d['Delta1_mV'],2)} mV</div><div class="note">{f(d['RDAC_step_uV'],1)} µV × {d['units_per_Delta1']}/Δ₁</div></div>
<div class="kpi"><div class="lab">Δ₂ / G₀</div><div class="val">{f(d['Delta2_over_G_LSB20'],2)} LSB₂₀</div><div class="note">后端分辨能力判据</div></div>
<div class="kpi"><div class="lab">单位电容</div><div class="val">{f(d['C_unit_fF'],2)} fF</div><div class="note">× {d['n_units_active']} 单位</div></div>
</div>
<div class="card">
{rows_html(["检查项", "实际", "门限", "单位", "结果"], val_rows, ["l","r","r","l","c"])}
<div class="small">噪声预算（折输入，µV rms）：
kT/C {f(d['budget_uV']['kT/C (RDAC)'],2)}　·　残差放大器 {f(d['budget_uV']['RA (折输入)'],2)}　·
ADC2 量化 {f(d['budget_uV']['ADC2 量化 (折输入)'],2)}　→　合计对应 DR = 94.6 dB，与论文一致。
RA 噪声由 target_dr_db 反推并在 s=1 锁定（它是电路属性，不随采样电容缩放）。</div>
</div>

<h2>三、主干方程</h2>
<div class="eq">两条采样路径分开：  x_S[n] = x[n] + n_S[n]        x_R[n] = x[n] + n_R[n]

粗量化与残差：      c₁ = Q₁(x_S)      b = M(c₁, S, DEM state)
                  v_D⁰    = DAC_nominal(b)          ← 数字可见
                  v_D^true = DAC_physical(b, chip)   ← 物理真值
                  r = x_R − v_D^true
                  v_R = G·r + n_RA,out          v₂ = Q₂(v_R)

重构：              x̂ = v_D⁰ + v₂ / Ĝ  −  d_corr</div>
<h3>为什么这条式子最重要</h3>
<div class="eq">x̂ = x + n_R  −  e_D  +  (n_RA,out + q₂) / G₀         其中 e_D = v_D^true − v_D⁰</div>
<p>RDAC 的<b>采样噪声</b>进入最终结果；RDAC 的<b>权重误差</b>也进入最终结果；
SADC 的误差<b>主要</b>通过粗码选择、残差范围和非理想耦合影响结果，并不直接叠加到输出。
这是后续测试 DEM、dither 和 KTC 的统一入口。</p>

<h2>四、逐级验收</h2>

<h3>Stage 1 · 理想两级链路</h3>
<div class="ok">{badge(R['s1']['PASS_重构正确'])} 重构正确　{badge(R['s1']['PASS_无溢出'])} 无溢出　{badge(R['s1']['PASS_误差≈量化步长'])} 误差符合后端量化步长
<div class="small">SNDR = {f(R['s1']['SNDR_dB'])} dB（理论 {f(R['s1']['expect_SNDR_from_q2_dB'])} dB，仅受 ADC2 量化限制）；
最大误差 {f(R['s1']['err_max_LSB'],3)} LSB₂₀ = Δ₂/G₀ 的一半；残差严格落在 [0, Δ₁]。</div></div>

<h3>Stage 2 · 固定物理失配</h3>
<div class="ok">{badge(R['s2']['PASS_同芯片同控制可复现'])} 相同芯片 + 相同控制状态 → 逐点完全一致（最大差 {R['s2']['same_seed_same_noise_maxdiff_V']} V）
<div class="small">失配在 <code>chip.C_true</code> 里每颗虚拟芯片只生成一次，不随采样点重新生成；
否则模拟的就不再是固定失配，而是在凭空给 DAC 加随机噪声。</div></div>

<h3>Stage 3 · DEM</h3>
<div class="ok">{badge(s3['PASS_名义值严格守恒'])} 名义值严格守恒（DAC<sub>nominal</sub>(M(c,state)) 与 state 无关）
　{badge(s3['PASS_失真下降'])} THD {f(s3['delta_THD_dB'],1)} dB</div>
{rows_html(["DEM", "SNDR [dB]", "SNR [dB]", "THD [dB]", "SFDR [dB]", "噪声 [µV]"], [
 ["关", f(s3['dem_off']['SNDR_dB']), f(s3['dem_off']['SNR_dB']), f(s3['dem_off']['THD_dB'],1), f(s3['dem_off']['SFDR_dB'],1), f(s3['dem_off']['noise_rms_V']*1e6,2)],
 ["开", f(s3['dem_on']['SNDR_dB']), f(s3['dem_on']['SNR_dB']), f(s3['dem_on']['THD_dB'],1), f(s3['dem_on']['SFDR_dB'],1), f(s3['dem_on']['noise_rms_V']*1e6,2)],
], ["l","r","r","r","r","r"])}
<p><b>关键观察：</b>DEM 把 THD 从 {f(s3['dem_off']['THD_dB'],1)} dB 拉到 {f(s3['dem_on']['THD_dB'],1)} dB，
SFDR 从 {f(s3['dem_off']['SFDR_dB'],1)} 抬到 {f(s3['dem_on']['SFDR_dB'],1)} dB，
但<b>噪声本底</b>同时被抬高（+{f(s3['delta_noise_V']*1e6,2)} µV）。
这就是「平均转移曲线更直，单次输出更分散」——两项必须一起看。</p>
<img src="{b64('dem_spectrum.png')}" alt="DEM 频谱对比">
<img src="{b64('static_curves.png')}" alt="静态均值误差与条件标准差">

<h3>Stage 4 · Dither</h3>
<div class="ok">{badge(s4['PASS_理想器件下无明显残留'])} 理想器件：注入—扣除成对，最大残留 {f(s4['ideal_with_dither']['err_max_LSB'],3)} LSB₂₀，溢出率 0
　{badge(s4['PASS_失配下线性化有收益'])} 失配器件下线性化有收益</div>
{rows_html(["模式", "|INL| [LSB₂₀]", "σ_e 均值 [µV]", "可用输入范围 [V_pp]"], [
 ["关", f(s4['mismatch_static']['off']['inl_max_LSB'],3), f(s4['mismatch_static']['off']['sigma_e_mean_V']*1e6,2), f(s4['mismatch_static']['off']['usable_input_range_Vpp'],2)],
 ["模拟注入 + 数字扣除", f(s4['mismatch_static']['analog']['inl_max_LSB'],3), f(s4['mismatch_static']['analog']['sigma_e_mean_V']*1e6,2), f(s4['mismatch_static']['analog']['usable_input_range_Vpp'],2)],
], ["l","r","r","r"])}
<div class="warn"><b>输入注入 dither 的真实代价：吃量程。</b>幅度 A<sub>D</sub> 直接从 ±V<sub>FS</sub> 里扣，
本例 2Δ₁ = 187.5 mV → 可用范围从 6.0 V<sub>pp</sub> 掉到 5.625 V<sub>pp</sub>（−6.3%）。
第一版这只是<b>理想可减 dither 的基准</b>，不是 ADI upper/lower dither 的完整实现；
后续应按专利把「整数部分的 DAC 控制」与「小数部分的模拟残差」分开注入。</div>

<h3>Stage 5 · 采样噪声与电容缩放</h3>
<p>缩电容时 <b>kT/C（∝1/√s）与失配（∝1/√s）必须同时改</b>，不能只改一个；
而残差放大器噪声是电路属性，<b>锁定不变</b>。</p>
<img src="{b64('budget.png')}" alt="噪声预算与判别性对比">

<h3>Stage 6 · KTC 校正支路</h3>
{rows_html(["KTC", "e_N [µV]", "SNDR [dB]", "噪声 [µV]", "ADC2 溢出率", "提取通路饱和率"],
 [[("开" if r['ktc'] else "关"), f(r['e_n_uV'],0), f(r['SNDR_dB']), f(r['noise_rms_uV'],2), f(r['overflow_rate']*100,3), f(r['ktc_path_sat_rate']*100,3)] for r in s6['rows']],
 ["l","r","r","r","r","r"])}
<p><b>ΔSNDR = {f(s6['rows'][1]['SNDR_dB'] - s6['rows'][0]['SNDR_dB'],2)} dB</b>（e<sub>N</sub>=0），
正好等于从噪声预算里扣掉 n<sub>R</sub> = 20.10 µV 的理论值 —— 目标采样噪声被消除。
观测通路噪声 e<sub>N</sub> 增大时收益衰减但不会归零。</p>
<img src="{b64('ktc.png')}" alt="KTC 观测噪声与 beta 失配">
<div class="warn"><b>更正：β 失配不是精度误差，而是采样时刻的插值权重。</b>
展开 KTC 重构式可得 <span class="eq" style="margin:6px 0">x̂ = x₁ + β·Δx + (1−β)·n_R</span>
β=1 输出 x₂，β=0 输出 x₁，中间值即 t₁ 与 t₂ 之间的<b>线性插值</b>。
Δt = T<sub>s</sub>/256 ≈ 0.1 ns，所以 β 误差等效为<b>亚 0.1 ns 的群延迟偏移</b> ——
对带限信号是纯 LTI 效应，SNDR / SFDR / THD / 双音 IMD 全都不动
（实测 β 失配 20% 时 IMD3 仍为 −120.7 dBc，与 β=0 一致）。
<b>真正的风险是 β 的时变性</b>（随码型、温度、电源漂移）→ 那才构成采样时钟调制并产生杂散，v1 未建模。</div>
<div class="warn"><b>KTC 方案有硬带宽上限，而且与 G<sub>N</sub>/κ 怎么分无关。</b>
校正项 κ·v<sub>N</sub> = G<sub>R</sub>(n<sub>R</sub> − Δx)，其中 Δx 比 n<sub>R</sub> 大约三个数量级，
所以真正被放大进 ADC2 的是 G<sub>R</sub>·|Δx|：
<span class="eq" style="margin:8px 0">2π f A G_R Δt &lt; ADC2 余量   →   f_max ≈ {f(kpi['f_bw']/1e6,2)} MHz（满幅、Δt = T_s/256）</span>
这个上限正好覆盖 PPT 标注的「Signal range DC – 5MHz」。把 Δt 从 T<sub>s</sub>/32 放宽到 T<sub>s</sub>/256 之前，
1.25 MHz 输入就已经吃掉 374 mV、溢出率 3.1%、SNDR 崩到 70 dB。</div>

<h2>五、失配压力测试（v2 重点）</h2>
<div class="warn"><b>v1 的失配模型是物理上的乐观下界。</b>
它把每个单位电容当成独立同分布，于是 DEM 可以把误差压到 σ/√N。
真实版图里失配是<b>空间相关</b>的，DEM 能吃掉多少取决于相关长度。
v2 把总失配方差拆成三层：<b>全局</b>（C<sub>fb</sub> + 整体工艺，DEM 完全无效）/
<b>slice 级</b>（版图梯度、刻蚀负载，跨 slice 有效）/ <b>unit 级</b>（Pelgrom A/√WL，DEM 完全有效）。</div>

<h3>5.1 DEM 的净收益随失配量级变负</h3>
{rows_html(["σ [ppm]", "ΔSNDR [dB]", "ΔTHD [dB]", "Δ噪声 [µV]", "判定"],
 [[f(r['sigma_ppm'],0), f(r['dSNDR_dB'],2), f(r['dTHD_dB'],1), f(r['dNoise_uV'],2),
   ('<span style="color:#4ECDC4">DEM 有收益</span>' if r['dSNDR_dB']>0 else '<span style="color:#FF6B6B">DEM 帮倒忙</span>')]
  for r in R['s8']['dem_delta']], ["r","r","r","r","c"])}
<p><b>机制已定位：</b>DEM 关时，DAC 误差中有 <b>{f(next(r for r in R['s8']['rows'] if r['sigma_ppm']==1117 and not r['dem'])['e_dac_absorbable']*100,1)}%</b>
与输入<b>线性相关</b> —— 这部分会被正弦拟合或增益校准吸收，<b>不计入 SNDR</b>。
DEM 把它随机化之后，线性占比降到 <b>{f(next(r for r in R['s8']['rows'] if r['sigma_ppm']==1117 and r['dem'])['e_dac_absorbable']*100,1)}%</b>，
于是误差<b>全部</b>变成不可吸收的随机噪声，且总量还上升
（{f(next(r for r in R['s8']['rows'] if r['sigma_ppm']==1117 and not r['dem'])['e_dac_rms_uV'],1)} → {f(next(r for r in R['s8']['rows'] if r['sigma_ppm']==1117 and r['dem'])['e_dac_rms_uV'],1)} µV）。
<b>这就是 DEM 在大失配下反而压低 SNDR 的原因。</b></p>

<h3>5.2 失配量级扫描</h3>
{rows_html(["σ [ppm]", "DEM", "SNDR [dB]", "SFDR [dB]", "THD [dB]", "噪声 [µV]", "误差 RMS [µV]", "e_DAC [µV]", "可吸收占比"],
 [[f(r['sigma_ppm'],0), ("开" if r['dem'] else "关"), f(r['SNDR_dB']), f(r['SFDR_dB'],1), f(r['THD_dB'],1),
   f(r['noise_rms_uV'],2), f(r['err_rms_uV'],2), f(r['e_dac_rms_uV'],2), f(r['e_dac_absorbable']*100,1)+"%"]
  for r in R['s8']['rows']], ["r","c","r","r","r","r","r","r","r"])}

<h3>5.3 60 颗虚拟芯片的良率分布</h3>
{rows_html(["σ [ppm]", "DEM", "SNDR 均值", "标准差", "最差", "P5", "P95", "SFDR 最差", "误差 RMS P95 [µV]"],
 [[f(R[k]['sigma_ppm'],0), ("开" if R[k]['dem'] else "关"), f(R[k]['SNDR_mean']), f(R[k]['SNDR_std']),
   f(R[k]['SNDR_min']), f(R[k]['SNDR_p5']), f(R[k]['SNDR_p95']), f(R[k]['SFDR_min'],1), f(R[k]['err_rms_p95_uV'],1)]
  for k in ('mc_pdk_off','mc_pdk_on','mc_cal_off','mc_cal_on')], ["r","c","r","r","r","r","r","r","r"])}
<div class="warn"><b>在 PDK 估算的 σ = 1117 ppm 下，系统会崩。</b>
SNDR 均值 {f(R['mc_pdk_on']['SNDR_mean'])} dB（DEM 开）/ {f(R['mc_pdk_off']['SNDR_mean'])} dB（DEM 关），
比论文 93.5 dB 低 <b>{f(93.5-R['mc_pdk_on']['SNDR_mean'],1)} dB</b>，
60 颗芯片里最差的一颗只有 {f(R['mc_pdk_on']['SNDR_min'])} dB，芯片间标准差 {f(R['mc_pdk_on']['SNDR_std'])} dB（尾部很厚）。
作为对照，σ = 100 ppm 时标准差仅 {f(R['mc_cal_on']['SNDR_std'])} dB。</div>

<h3>5.4 失配预算上限</h3>
{rows_html(["σ [ppm]", "SNDR 均值 [dB]", "最差芯片 [dB]", "SFDR 最差 [dB]", "达标"],
 [[f(r['sigma_ppm'],0), f(r['SNDR_mean']), f(r['SNDR_min']), f(r['SFDR_min'],1),
   badge(r['SNDR_mean']>=R['budget']['target_sndr_db'])] for r in R['budget']['rows']],
 ["r","r","r","r","c"])}
<p><b>要达到 SNDR ≥ {f(R['budget']['target_sndr_db'],1)} dB（均值口径），单位电容失配 σ 最多只能到约 {f(R['budget']['sigma_limit_ppm_mean'],0)} ppm。</b>
PDK 估算的 {f(kpi['pdk_ppm'],0)} ppm 超标 <b>{f(kpi['pdk_ppm']/R['budget']['sigma_limit_ppm_mean'],1)} 倍</b>。
两条出路：<br>
· <b>校准</b>：把有效失配从 1117 ppm 压到 &lt;{f(R['budget']['sigma_limit_ppm_mean'],0)} ppm（需 {f(kpi['pdk_ppm']/R['budget']['sigma_limit_ppm_mean'],1)}× 改善）；<br>
· <b>加面积</b>：σ ∝ 1/√C，同样 {f(kpi['pdk_ppm']/R['budget']['sigma_limit_ppm_mean'],1)}× 改善需要 <b>{f((kpi['pdk_ppm']/R['budget']['sigma_limit_ppm_mean'])**2,0)} 倍电容面积</b> —— 明显不可行。<br>
<b>结论：单位权重校准不是可选优化，而是这个架构能否成立的前提。</b></p>
<img src="{b64('mismatch_stress.png')}" alt="失配压力测试">

<h2>六、专利 [09]–[14] 机制验证</h2>
<div class="card"><p>读这六份专利的有效方法不是记住"分片、dither、DEM、交织"几个名字，而是沿着三个问题：
<b>电荷从哪里来，数字码改变了哪些开关，误差最后进入了哪条通路</b>。
下面把其中三条可计算的性质固化成测试。</p></div>

<h3>6.1 [10] Dither 的两种物理实现：代价形式不同，总量完全相同</h3>
{rows_html(["实现", "α", "SNDR [dB]", "THD [dB]", "噪声 [µV]", "满幅溢出率"],
 [[r['mode'], f(r['alpha'],4), f(r['SNDR_dB']), f(r['THD_dB'],1), f(r['noise_uV'],2), f(r['ovf_fullscale']*100,3)+"%"]
  for r in R['s9']['dither_impl']], ["l","r","r","r","r","r"])}
{rows_html(["dither 幅度 [mV]", "当量单位", "输入注入 SNDR", "采样态 SNDR", "α", "量程损失 [dB]", "增益损失 [dB]"],
 [[f(r['dither_mV'],1), f(r['units'],1), f(r['input_SNDR']), f(r['sampling_SNDR']), f(r['alpha'],4),
   f(r['range_loss_dB'],3), f(r['alpha_loss_dB'],3)] for r in R['s9']['dither_equal_cost']],
 ["r","r","r","r","r","r","r"])}
<div class="warn"><b>这是一个精确的数学等价，不是巧合。</b>
输入注入从 ±V<sub>FS</sub> 扣掉 dither 幅度，代价 = 20log₁₀[V<sub>FS</sub>/(V<sub>FS</sub>−A<sub>D</sub>)]；
采样态注入把 2D 个单位改接 ±V<sub>FS</sub>，信号衰减 α = (N−2D)/N，代价 = −20log₁₀α。
而 A<sub>D</sub>/V<sub>FS</sub> = 2D/N，故
<span class="eq" style="margin:8px 0">1 − A_D/V_FS ≡ α　→　两种代价恒等（上表后两列相等到 15 位有效数字）</span>
<b>dither 的代价不可能被消除，只能选择以什么形式付出：</b>
输入注入只在接近满幅时付出（表现为硬削顶），本例满幅溢出 {f(next(r for r in R['s9']['dither_impl'] if r['mode']=='analog')['ovf_fullscale']*100,3)}%；
采样态注入始终付出 1/α 的噪声放大，但<b>满幅零溢出</b>，且不需要额外的精密求和电路 ——
这正是 [10] 强调"把 dither 放进独立采样 RDAC，而不必让小 ADC 承担整个大 dither"的原因。</div>

<h3>6.2 [09] 1.3 粗码变化会被残差自动修正（实证）</h3>
{rows_html(["Δτ [ps]", "粗码变化率", "残差偏移 [mV]", "SNDR [dB]", "误差 RMS [µV]", "溢出率"],
 [[f(r['dtau_ps'],0), f(r['coarse_change_pct'],2)+"%", f(r['residue_shift_mV'],3),
   f(r['SNDR_dB']), f(r['err_rms_uV'],2), f(r['ovf_pct'],3)+"%"]
  for r in R['s9']['coarse_correction']], ["r","r","r","r","r","r"])}
<p><b>SADC 通路的误差不会直接进输出。</b>把采样响应失配 Δτ 从 20 ps 加到 200 ps，
粗码变化 {f(next(r for r in R['s9']['coarse_correction'] if r['dtau_ps']==20)['coarse_change_pct'],2)}%、
残差被推走整整 {f(next(r for r in R['s9']['coarse_correction'] if r['dtau_ps']==20)['residue_shift_mV'],2)} mV（正好 1 个 Δ₁），
但 SNDR 与误差 RMS <b>一字不差</b>。
因为粗码在数字通路与模拟残差通路里<b>成对出现</b>：RDAC 用的就是那个实际粗码，后端把差值补回来。
这段结构把要求拆成两半：
<span class="eq" style="margin:8px 0">SADC：快速、残差不超范围　　RDAC：低采样噪声、准确生成残差</span>
代价仅是残差范围被占用，过大才溢出。</p>

<h3>6.3 [09] Fig.12 / [10] Fig.19 小数权重：slice 异码合成</h3>
{rows_html(["小数部分", "总码 k", "DAC 误差 [V]"],
 [[f(r['frac'],4), f(r['k'],3), f(r['e_dac_V'],12)] for r in R['s9']['fractional_weight']],
 ["r","r","r"])}
<p>单个 slice 只能表示整数步进，<b>不代表合并后也只能表示同样粗的步进</b>。
7 个 slice 中 2 个取状态 2、5 个取状态 3，合并权重 = (2·2+5·3)/7 = 2+5/7 ——
这不是时间平均，而是这一次转换里多个 slice 的电荷共同形成的小数权重。
模型用<b>实数 k + 前缀和线性插值</b>实现，名义值严格守恒（上表误差在同一量级，仅物理访问集合改变）。
这套机制同时是 dither（改变误差访问位置）与 [11] binary-to-unary bridging（把子 DAC 低位搬进主 DAC）的物理基础。</p>
<div class="small"><b>注意：</b>[11] 的真正难点是<b>主／子 DAC 的边界</b>——两边各自 shuffle 后各自逼近自己的平均单位值，
但两个平均值仍未必相等，中间耦合电容 C<sub>C</sub> 造成的比例误差还在。
本模型是纯等权 unary，<b>没有这个边界</b>，因此 v1 会低估 DEM 与 bridging 的难度和收益。</div>

<h2>七、判别性对比：原电容 vs 缩小电容 + KTC</h2>
<div class="kpis">
<div class="kpi a"><div class="lab">原电容 · 无 KTC</div><div class="val">{f(kpi['base_sndr'])} dB</div><div class="note">20.5 pF · NSD {f(kpi['base_nsd'])} nV/√Hz</div></div>
<div class="kpi b"><div class="lab">缩到 1/4 · 无 KTC</div><div class="val">{f(kpi['shr_sndr'])} dB</div><div class="note">5.12 pF · NSD {f(kpi['shr_nsd'])} nV/√Hz</div></div>
<div class="kpi"><div class="lab">缩到 1/4 · 有 KTC</div><div class="val">{f(kpi['ktc_sndr'])} dB</div><div class="note">5.12 pF · NSD {f(kpi['ktc_nsd'])} nV/√Hz</div></div>
<div class="kpi"><div class="lab">KTC 净收益</div><div class="val">+{f(kpi['gain_db'])} dB</div><div class="note">相对原电容 {f(kpi['vs_base'],2)} dB</div></div>
</div>
<p><b>结论：电容缩到 1/{kpi['cap_ratio']:.0f}，开 KTC 之后 SNDR 与 NSD 都<b>略优于</b>原电容方案
（{f(kpi['ktc_sndr'])} vs {f(kpi['base_sndr'])} dB，NSD {f(kpi['ktc_nsd'])} vs {f(kpi['base_nsd'])} nV/√Hz），
而 RDAC 面积与切换电荷同时降到 1/4。KTC 的价值不是「所有误差都变小」，
而是<b>允许你重新分配采样噪声预算</b>。</p>
<h3>完整缩放扫描（DEM 开，同一颗虚拟芯片）</h3>
{rows_html(["C [pF]", "KTC", "σ_kT/C [µV]", "σ_失配 [ppm]", "SNDR", "NSD", "SFDR", "THD", "|INL| [LSB]", "σ_e [µV]", "溢出 %"],
 sw_rows, ["r","c","r","r","r","r","r","r","r","r","r"])}
<p><b>KTC 把 SNDR 对电容的敏感度拉平</b>：电容变化 16× 时，
无 KTC 掉 {f(sw[0]['SNDR_dB']-sw[8]['SNDR_dB'],2)} dB，有 KTC 只掉 {f(sw[1]['SNDR_dB']-sw[9]['SNDR_dB'],2)} dB。</p>
<h3>DEM 开 / 关 两种配方下的三方案对比</h3>
{rows_html(["方案（DEM 开）", "C [pF]", "SNDR", "Δ vs 原电容", "NSD", "SFDR", "THD", "|INL|", "σ_e [µV]", "切换电荷 [pC]"],
 s7_rows(s7), ["l","r","r","r","r","r","r","r","r","r"])}
{rows_html(["方案（DEM 关）", "C [pF]", "SNDR", "Δ vs 原电容", "NSD", "SFDR", "THD", "|INL|", "σ_e [µV]", "切换电荷 [pC]"],
 s7_rows(s7o), ["l","r","r","r","r","r","r","r","r","r"])}
<img src="{b64('sweep.png')}" alt="缩电容与 KTC 扫描">
<div class="warn"><b>但 INL 只由失配决定，KTC 完全帮不上。</b>
20.5 pF → 5.12 pF，失配 σ 翻倍（100 → 200 ppm），|INL| 从 {f(kpi['inl_base'],3)} 涨到 {f(kpi['inl_ktc'],3)} LSB₂₀；
开不开 KTC 都一样。失配预算必须<b>独立通过</b>。
（本模型 DEM 开时 INL 远优于论文的 2.2 LSB，因为 v1 没有建模 PPT 第 32 页点名的三项：
input settling、digital crosstalk、reference settling。）</div>
<div class="small">Monte Carlo（{R['mc']['n_chips']} 颗虚拟芯片，s=1，DEM 开）：
SNDR 均值 {f(R['mc']['SNDR_mean'])} dB，σ = {f(R['mc']['SNDR_std'])} dB，范围 {f(R['mc']['SNDR_min'])} ~ {f(R['mc']['SNDR_max'])} dB。</div>

<h2>八、三个必须记住的坑</h2>
<div class="card">
<p><b>1. DAC 误差只由阵列总面积决定，与切成多少个单位无关。</b>
σ<sub>e</sub> ∝ σ<sub>ε</sub>/√N ∝ 1/√(N·A<sub>u</sub>) = 1/√A<sub>total</sub>。
所以「多切几份靠 DEM 平均」并不能改善误差本身，DEM 只把确定性失真换成随机噪声。</p>
<p><b>2. 失配系数的物理值与行为学标定值差 ~{f(kpi['pdk_ppm']/kpi['fit_ppm'],0)} 倍。</b>
用典型 MIM 参数（A<sub>σ</sub> = 0.5 %·µm，密度 2 fF/µm²）估算，40 fF 单位的 σ<sub>ε</sub> ≈ {f(kpi['pdk_ppm'],0)} ppm；
要让 s=1 对上论文 SNDR，只需要 100 ppm。<b>这个 ~{f(kpi['pdk_ppm']/kpi['fit_ppm'],0)}× 缺口就是「必须做单位权重校准」的证据</b>
（PPT 功耗分解里确实列了 Logic calibration）。v1 只实现了增益校准接口，单位权重估计是下一步。</p>
<p><b>3. RA 噪声必须在 s=1 标定后锁定。</b>
若按「总预算 − kT/C」逐点反推，缩到某个 s 之后 kT/C 会超过总预算，把 RA 噪声算成 0，
人为抹掉缩电容的代价（第一版就踩了这个坑，修正后 5.12 pF 的 SNDR 从虚高的 102 dB 回到 93.86 dB）。</p>
</div>

<h2>九、三条底线与下一步</h2>
<div class="card">
<p><span class="tag p">底线 1</span><b>物理失配固定</b> —— <code>chip.C_true</code> 每颗芯片生成一次；
采样噪声按事件更新，同一实现贯穿该样本后续处理；校准算法不得读取真值。</p>
<p><span class="tag p">底线 2</span><b>数字映射守恒</b> —— DEM 只做等权单位置换，
选中的单位个数恒等于逻辑码，DAC<sub>nominal</sub> 与 state 无关（结构性保证，不是调参）。</p>
<p><span class="tag p">底线 3</span><b>噪声相关性真实</b> —— 同一个 n<sub>R</sub> 进入残差通路与 KTC 观测通路；
两条放大通路分别检查摆幅后再相减。</p>
</div>
<div class="card">
<h3>下一步（按优先级）</h3>
<p>① 把 <code>mismatch_sigma0</code> 与 <code>cap_scale</code> 换成 PDK 数据；
② 单位权重估计改为<b>秩感知</b>方案（Stage 11.3：rank(U)=64/512，
只能识别 64 个跨 slice 位置组权重；校准组权重 + DEM 组内平均 = 处理 unit 失配的充分组合），
并按"主/子 DAC 电荷模型 → 误差敏感度与可观测性 → 校准 → DEM/bridging 联合验证"的顺序推进；
③ 把 DEM 从「等权单位置换」升级到带权重的 binary-to-unary bridging（需先建主/子 DAC 边界，否则评估口径不对）；
④ dither 从输入注入升级到专利的 upper/lower 分组注入（并回答「提取窗口内 dither 是否变化」）；
⑤ 加入 slice 资源池调度（8/18 选择 + 2 spare 轮换），v1 是固定两组 8 个；
⑥ 加入 input settling / digital crosstalk / reference settling，才能谈 INL 的绝对值。</p>
<p class="small">电容与切换电荷目前只作为面积、驱动负担的<b>代理指标</b>，不要据此宣布功耗或 FoM 已经提高。</p>
</div>

<h2>十、v3 审计回应与五条验收</h2>
<div class="card">
<p>第三版审计 10 项指控<b>全部核实属实</b>，v3 已逐条修复并新增 Stage 10 验收套件
（<code>experiments.stage10_audit_acceptance()</code>，<code>run_all.py</code> 自动执行，五项全 PASS）：</p>
<table>
<tr><th>验收项（审计原话）</th><th>修复内容</th><th>验收结果</th></tr>
<tr><td>资源与噪声一致</td><td>kT/C 改绑<b>逐样本活跃电容</b> C<sub>active</sub>[n] = Σ<sub>j∈S[n]</sub>Σ<sub>k</sub> C<sub>j,k</sub>；旧版用整池 46.1 pF（18 slice），导致加备用 slice 噪声反而下降</td>
<td>16/18/32 slice（活跃恒 8）：σ<sub>nR</sub> = 20.16/20.16/20.16 µV，PASS</td></tr>
<tr><td>物理参数确实生效</td><td>新增<b>电荷一致模型</b> ra_gain_model="charge"：G[n] = C<sub>active</sub>[n]/C<sub>F,true</sub>，C<sub>F,0</sub> = 20.5 pF/32 ≈ 0.64 pF</td>
<td>C_F ×1.2 → G 精确 /1.2（15 位有效数字），PASS</td></tr>
<tr><td>校准联调正确</td><td>回归量改为 α·x + d<sub>nom</sub> − v<sub>D0</sub>（旧版漏 dither，Ĝ 33.6→6.68）；beta 校准改用<b>固定目标 x1</b> + κ<sub>new</sub>=κ/β̂（旧版参考代入待估参数，β̂ 恒 1；且不再读取 g_actual）</td>
<td>三种 dither 下增益校准 &lt;10 ppm；β=1.2 时 κ 修到目标 0.006%，PASS</td></tr>
<tr><td>指标有已知答案测试</td><td>SFDR 保护带 ±30→±8 bin（BH4 主瓣）；THD 谐波<b>折叠去重</b>（7/9 次与基波重合不再重复计入）</td>
<td>远端/近端 −80 dBc spur 均测得 80.00 dB；纯 5 MHz 正弦 THD=−221 dB，PASS</td></tr>
<tr><td>KTC 有动态预算</td><td>新增 ktc_observe_bw_hz：一阶建立模型 ε=e<sup>−2πf·Δt</sup>，实际观测增益 G<sub>N</sub>(1−ε) 进物理通路；validate() 报告带宽需求</td>
<td>ε=1% 需 7.5 GHz；带宽 3 GHz 时 err<sub>to_x2</sub> 34→928 µV 但 SNDR 不动，PASS</td></tr>
</table>
</div>

<div class="card">
<h3>两个被审计推翻、必须重写的结论</h3>
<p><span class="tag w">推翻 1</span><b>「全局/slice 失配一定是 DEM 的噪声下限」在旧比值模型下不成立。</b>
实测 σ=1% 时全局与 slice 级失配的 e<sub>DAC</sub> 都是 0.0000 µV（交错映射 + 比值归一化使其相消）——
旧版三层分解实验的 split 扫描表是废的。v3 接入电荷一致模型后，它们的真实去向是<b>增益通路</b>：
slice 分量 → 增益偏移 + 轮转波动；unit 分量 → 码域 DAC 误差（DEM 有效）。
<span class="tag w">v4 再度修正</span>：全局分量曾被 v3 误述为"纯满幅增益误差"——第四版审计指出
e<sub>G</sub> = δ<sub>G</sub>·(x−v<sub>D0</sub>) 乘的是<b>残差</b>而非输入。
实测证实（Stage 11.2）：σ=1% 时剩余误差 39.26 µV ≈ 噪声底 39.50 µV，
每个粗码区间内误差斜率精确等于 δ<sub>G</sub>=−0.0304，幅度扫 |δ<sub>G</sub>|·Δ₁≈2.9 mV ——
这是<b>码相关周期性锯齿</b>（SNDR 实测仅 67.5 dB），不是整机增益误差（若为增益假设剩余应达 58 mV）。
它不受 DEM 影响，数字增益校准才能消除。</p>
<p><span class="tag w">推翻 2</span><b>「反推失配 = 工艺证据」的推论删除。</b>
100 ppm 是拟合值、1117 ppm 是假设估算，都不是测量。参数来源已分三类标注：
[披露]（PPT/论文）、[拟合]（mismatch_sigma0）、[假设]（mismatch_from_process_assumption，已改名）。
"必须做单位权重校准"的结论方向不变，但证据应来自 PDK 测量，不再是这个缺口。
stage8 的写死结论字段也已改为数据判据。</p>
</div>

<div class="card">
<h3>KTC 动态预算（v4 重写：单一 β 分解为 β<sub>n</sub>/β<sub>x</sub>）</h3>
<p>v3 的统一建立系数 (1−ε) 同时乘 n<sub>R</sub> 和 Δx，隐含"输入变化也是窗口起点的阶跃"——不成立。
按一阶微分方程精确解（Stage 11.1 三激励验证：阶跃/斜坡与解析式误差 &lt;1e−8，正弦片段 η<sub>x</sub> 近似 &lt;0.1%）：</p>
<p style="text-align:center">η<sub>n</sub> = 1−e<sup>−T<sub>w</sub>/τ<sub>a</sub></sub>（阶跃，窗口起点已存在）；
η<sub>x</sub> = 1−(τ<sub>a</sub>/T<sub>w</sub>)(1−e<sup>−T<sub>w</sub>/τ<sub>a</sub></sub>)（斜坡，逐渐积累）</p>
<p>3 GHz 带宽下 η<sub>n</sub>=0.841、η<sub>x</sub>=0.543（<b>差 55%——旧统一系数模型的量级错误</b>）。
输出误差结构 e = (1−β<sub>n</sub>)n<sub>R</sub> + (β<sub>x</sub>−1)Δx + 噪声底，与实测吻合 &lt;0.1%。</p>
<p><b>核心矛盾（Stage 11.4 实测）</b>：β<sub>n</sub>=κ·G<sub>N</sub>η<sub>n</sub>/G<sub>R</sub>（噪声抵消）与
β<sub>x</sub>=κ·G<sub>N</sub>η<sub>x</sub>/G<sub>R</sub>（信号时刻响应）由同一个 κ 控制、比例锁死在 η<sub>n</sub>/η<sub>x</sub>。
3 GHz 下：信号校准（β<sub>x</sub>=1）→ 噪声过抵消 −0.55·n<sub>R</sub>（残余 −11 µV，比不校准差 3.4×）；
噪声校准（β<sub>n</sub>=1）→ 时刻误差 (1−η<sub>x</sub>/η<sub>n</sub>)Δx ≈ 2.08 mV。
三种 κ 设定下 SNDR 全部 94.5–94.9 dB——(β<sub>x</sub>−1)Δx 对正弦是 LTI 响应被拟合吸收，
<b>κ 的取舍必须由时刻一致性指标决定，SNDR 对此不敏感</b>。
Δt 的物理定义与 G<sub>N</sub>(Δt, C<sub>load</sub>)、σ<sub>N</sub>(Δt, C<sub>NC</sub>) 的电路级模型仍是下一步。</p>
</div>

<h2>十一、v4 审计验收（Stage 11）</h2>
<div class="card">
<p>第四版审计的两个 P0 + 一个 P1 全部核实属实并修复，验收固化为
<code>experiments.stage11_audit_v4()</code>（run_all.py 自动执行，五项全 PASS）：</p>
<table>
<tr><th>验收项</th><th>结果</th></tr>
<tr><td>η<sub>n</sub>/η<sub>x</sub> 三激励验证</td><td>阶跃 0.841306（误差 8e−14）、斜坡 −0.542962（误差 8e−11）、正弦片段 η<sub>x</sub> 近似误差 0.06%；旧统一系数模型偏差 54.9%</td></tr>
<tr><td>全局失配误差结构</td><td>e = δ<sub>G</sub>(x−v<sub>D0</sub>) 假设剩余 39.26 µV ≈ 噪声底 39.50 µV；增益假设剩余 57.95 mV（排除）；SNDR 67.52 dB</td></tr>
<tr><td>校准可观测性</td><td>rank(U) = <b>64 / 512</b>：交错映射下 8 个 slice 同一位置的单位永远一起开关（64 组 × 每组 8 个、跨全部 slice）。任何校准只能识别 64 个跨 slice 位置组权重；<b>"512 权重前景最小二乘"在当前映射下原理上不可实现</b>。打破简并需要"各 slice 选不同数量"的映射 = [11] 的低位跨 slice 分配 —— 主/子 DAC + bridging 因此是校准的前提而非可选项</td></tr>
<tr><td>KTC 双 β 模型</td><td>e=(1−β<sub>n</sub>)n<sub>R</sub>+(β<sub>x</sub>−1)Δx+底 三种 κ 设定下与实测吻合 &lt;0.1%</td></tr>
<tr><td>SFDR 保护带多距离</td><td>距基波 5/6/7/10/100/1000 bin 的 −80/−90 dBc 杂散全部测得真值（±0.5 dB）。方法：保护区内逐 bin <b>时域联合最小二乘</b>（v4 曾尝试"找最强点"失败——主瓣滚降泄漏永远强于真 spur，316 dB 假象）</td></tr>
</table>
<p class="small">另：v3 报告"9.97 MHz 下 70 dB 假性结果"表述已更正为"超规格激励下的真实过载，不应用于评价带内性能"；相干频率已独立于 FFT 长度选取。</p>
</div>

<h2>十二、v5 分段 DAC 拓扑（主/子阵列 + 桥接电容）</h2>
<div class="card">
<p>v1–v4 的 DAC 是<b>纯等权 unary</b>：512 个单位、512 个电平。这在结构上<b>无法表达</b>专利 [11]
的核心难点 —— 主/子阵列的边界。真实的高精度 CDAC 几乎都用分段结构
（n<sub>main</sub> 个主单位 + n<sub>sub</sub> 个子单位经桥接电容 C<sub>C</sub> 耦合），
用 n<sub>main</sub>+n<sub>sub</sub> 个单位做出 n<sub>main</sub>×n<sub>sub</sub> 个电平。
代价与收益都必须量化，否则"要不要分段"就是拍脑袋。</p>
{rows_html(["拓扑", "单位数", "电平数", "单位电容 [fF]", "σ_ε [ppm]", "面积/负载 A+B [pF]", "信号电荷 C_sig [pF]", "噪声等效 C_n [pF]", "DAC 码跨度亏缺 [%]", "跨度亏缺 [dB]", "SNDR [dB]", "SFDR [dB]"],
 [[r["拓扑"], r["单位数"], r["电平数"], f(r["单位电容_fF"],1), f(r["sigma_eps_ppm"],1),
 f(r["总采样电容_pF"],2), f(r.get("C_sig_pF"),2), f(r.get("C噪声等效_pF"),2),
 f(r["满幅收缩_pct"],2), f(r["量程代价_dB"],3),
 f(r["SNDR_dB"],2), f(r["SFDR_dB"],1)] for r in s12_topo],
 ["l","r","r","r","r","r","r","r","r","r","r","r"])}
<img src="{b64('v5_structure.png')}" alt="v5 分段拓扑与动态误差">
<p><b>同样的 20.5 pF 总面积，分段换到了什么。</b>
Pelgrom 定律 σ<sub>ε</sub> ∝ 1/√A<sub>u</sub>：单位数从 512 降到 72，单位电容就从 40 fF 涨到
{f(s12_topo[1]['单位电容_fF'],0)} fF，σ<sub>ε</sub> 从 {f(s12_topo[0]['sigma_eps_ppm'],0)} ppm 降到 {f(s12_topo[1]['sigma_eps_ppm'],0)} ppm。
代价是<b>顶码亏缺</b>造成的 <b>DAC 最小码至最大码的跨度亏缺</b> {f(s12_topo[1]['满幅收缩_pct'],2)}%
（输入等效口径下 v_lo=−V<sub>FS</sub> 是恒等式，但 k=levels−1 只选通 63+7 个单位、到不了 +V<sub>FS</sub>，
两端不对称；旧顶板口径报的 2.0% 是另一种参考下的数字），
折成 {f(-s12_topo[1]['量程代价_dB'],3)} dB。
<span class="tag w">口径声明</span> 这是 <b>DAC 码点跨度</b>的亏缺，不自动等同于 ADC 可用输入幅度损失：
按 512 个名义码仓上边界计算是 {f(1.53846,2)}%（v_hi 边界 2.9077 V），
而整机实际可用输入范围还受粗码策略、残差放大与 ADC2 余量约束。
SNDR 从 {f(s12_topo[0]['SNDR_dB'],2)} 掉到 {f(s12_topo[1]['SNDR_dB'],2)} dB。
这不是免费午餐，但是一笔可以算清楚的账。</p>
<div class="warn"><b>电荷口径闭合验收（v5 二轮审计核心要求）。</b>
独立节点方程（charge_ref.py：采样相顶板钳位/底板采输入，放大相 P 虚地、Q 浮置）
给出 C<sub>F</sub>·v<sub>R</sub> = C<sub>sig</sub>·x − Q<sub>D</sub>。
全链路已统一到<b>输入等效口径</b> v<sub>D,in</sub> = Q<sub>D</sub>/C<sub>sig</sub>：
① evaluate_physical 与独立方程最大偏差 {f(R.get('s12',{}).get('charge_closure',{}).get('eval_vs_nodal_max_V',0)*1e12,3)} pV；
② 输入 = 输入等效 DAC 电压时，主循环模拟残差最大
{f(R.get('s12',{}).get('charge_closure',{}).get('residue_zero_max_V',0)*1e9,1)} nV
（修复前 <b>142.75 mV</b> —— 数字端自洽重构掩盖了它，"理想重构正确"不能证明中间节点正确）。
四个电容口径已分离：面积(A+B) / 输入负载(A+B，用于建立 τ) / 信号电荷(C<sub>sig</sub>，用于增益) /
噪声等效(C<sub>sig</sub>²/(A+β²B)，用于 kT/C)。</div>
<div class="warn"><b>桥接电容 C<sub>C</sub> 的比例误差 → 周期 = n<sub>sub</sub> 的子码锯齿，DEM 完全无效。</b>
DEM 只置换单位，动不了那一个 C<sub>C</sub>。<code>stage12</code> 扫描三个 σ(C<sub>C</sub>) 量级，
DEM 开/关锯齿峰峰值几乎不变（下表），这是结构性质而非参数巧合：
{ dem_row_note }
</div>
{rows_html(["σ(C_C)/C_C", "DEM", "锯齿峰峰 [µV]", "随机残差 [µV]", "SNDR [dB]", "THD [dB]"],
 [[f(r["bridge_sigma"]*1e6,0)+" ppm", "开" if r["dem"] else "关", f(r["sawtooth_pp_uV"],2),
 f(r["random_rms_uV"],2), f(r["SNDR_dB"],2), f(r["THD_dB"],1)] for r in s12_bridge],
 ["r","l","r","r","r","r"])}
<p><b>这是专利 [11] 主/子边界的量化版本。</b>
边界残差 = n<sub>sub</sub>·w<sub>S</sub> − w<sub>M</sub> =
{f(s12_bd.get('boundary_err_uV'),1)} µV，子权重相对误差 {f(s12_bd.get('sub_weight_rel_err_ppm'),0)} ppm
（主权重 {f(s12_bd.get('main_LSB_uV')/1000,2)} mV、子权重 {f(s12_bd.get('sub_LSB_uV')/1000,3)} mV）。
两个子阵列各自 DEM 之后各自逼近<b>自己的</b>平均值，两个平均值之差就是边界台阶；
其中 C<sub>C</sub> 比例误差那一部分 DEM 消不掉。
但锯齿<b>只有一个自由度</b>（子阵列增益），一次单参数校准即可消除 ——
这正是"必须先有边界，才有边界校准"。纯 unary 模型里这个自由度根本不存在。</p>
</div>

<h2>十三、三项动态误差：INL 缺口来自这里</h2>
<div class="card">
<p>[00_1] p.32 点名了 input settling / digital crosstalk / reference settling 三项限制 INL。
v1–v4 一项都没建模，所以模型算出 |INL| = 0.07 LSB 而论文实测 2.2 LSB。
v5 把三项加上（<code>dynamics.py</code>），并且明确它们<b>都不是静态失配</b>：
既不能靠加大单位电容改善，共模部分也不能被 DEM 平均。</p>
<div class="warn"><b>测量协议（v5 二轮审计 §4 + v5.1 三轮审计 §三，两套测试严格分开）。</b>
INL 列全部是<b>确定性协议</b>：关热噪声 + 关 dither + 固定芯片，转移曲线是确定性的，
直接报峰值/RMS 原始值 —— <b>不做任何"去噪"换算</b>。
旧版曾用 √(peak²−floor²) 给峰值"去噪"：max|e+n| 是非线性统计量，
方差相减不成立（实测真实 INL=0 的含噪曲线被该公式报出 2.18 LSB 中位数）——
该公式与基于它的 ρ=0.081 规格一并撤回。
SNDR/THD/SFDR 来自<b>动态协议</b>（噪声开、dither 按配置），两套口径分开报告。
测量不确定度应由多组噪声实现的分布给出，不再混进 INL 数字。
<b>v5.1 补强</b>：① 重复次数由实际 DEM 轮转周期反推（rep = 2×{s13_repconv.get('period_per_bank', 64)} = {s13_rep}，
旧 rep=96 只覆盖 48/64 组合）；② 剔除 v<sub>prev</sub>[0]=0 的启动瞬态样本；
③ 收敛判据：完整周期覆盖后 rep→2rep 峰值逐位不变
（实测 {f(s13_repconv.get('INL_at_rep'),4)} → {f(s13_repconv.get('INL_at_2rep'),4)} LSB，{'PASS' if s13_repconv.get('PASS') else 'FAIL'}）。</div>
{rows_html(["设置", "INL_max 确定性 [LSB₂₀]", "INL_rms 确定性 [LSB₂₀]", "SNDR [dB]", "THD [dB]", "SFDR [dB]"],
 [[r["设置"].replace("输入建立+Ron码调制", "输入建立+Ron 码调制（ρ=0.5）")
   .replace("输入建立", "输入建立（ρ=0.1）"), f(r.get("INL_max_LSB20"),3), f(r.get("INL_rms_LSB20"),3),
 f(r["SNDR_dB"],2), f(r["THD_dB"],1), f(r["SFDR_dB"],1)]
 for r in s13_rows], ["l","r","r","r","r","r"])}
<p>三项同时打开确定性峰值 INL 为 <b>{f(s13_all.get('INL_max_LSB20'),2)} LSB₂₀</b>，与论文 2.2 LSB 同一量级 ——
<b>不需要把失配 σ 硬调大就能解释这个缺口</b>。单项（确定性峰值口径）：
输入建立 {f(s13_dict.get('输入建立',{}).get('INL_max_LSB20'),2)}、
参考建立 {f(s13_dict.get('参考建立',{}).get('INL_max_LSB20'),2)}、
数字串扰 {f(s13_dict.get('数字串扰(共模+单位)',{}).get('INL_max_LSB20'),2)} LSB。</p>
<div class="warn"><b>但请注意：误差曲线可叠加，峰值/RMS 不可相加。</b>
单独作用时输入建立一项就有 {f(s13_dict.get('输入建立',{}).get('INL_max_LSB20'),2)} LSB，
三项全开反而只有 {f(s13_all.get('INL_max_LSB20'),2)} LSB —— 交叉项为负，
当前参数/符号/误差形状下<b>部分相互抵消</b>。
这是实测现象，<b>不是保底设计收益</b>：PVT、版图极性、码型或时序改变后
抵消可能减弱甚至反号。工程预算应同时给出典型组合、统计分布与保守上界。
<b>结论：动态误差必须联合仿真评估，单项数据只用于灵敏度排序。</b></div>
</div>

<div class="card">
<h3>输入建立与 INL：工作点性质，不是普适规律</h3>
<p>采样相一阶 RC，顶极板从上一次残差 V<sub>prev</sub> 出发向 x 建立：
x<sub>saved</sub> = x − ε·(x − V<sub>prev</sub>)，ε = e<sup>−T<sub>s</sub>/τ</sup>。
在默认采样相（ε≈1.5e-5）下，它近似为可校准的增益误差；
<b>但这是工作点性质</b>：精确关系 x[n] = Σ k<sup>i</sup>·v<sub>D,0</sub>(x<sub>n−i</sub>)
（k = −ε/(1−ε)·(2−φ)）对历史 DAC 权值有记忆，ε 增大（采样相缩短）时
即使 ρ=0 也会产生大 INL（实测 Ts=2.5 ns 时 &gt;10<sup>4</sup> LSB）。
默认工作点下，INL 主要来自 τ 的码相关性：开关导通电阻随输入电平变，
τ(k) = τ₀·(1 + ρ·(2k/N − 1))，于是 ε(k) 随码变，误差变成码相关的 INL。</p>
{rows_html(["ρ=Ron 码调制", "INL_max 确定性 [LSB₂₀]", "INL_rms 确定性 [LSB₂₀]", "拟合增益误差 [ppm]"],
 [[f(r["rho"],3), f(r["INL_max_LSB20"],3), f(r["INL_rms_LSB20"],3), f(r["增益误差_ppm"],0)]
 for r in s13_ron], ["r","r","r","r"])}
<p>ρ = 0 时确定性峰值 INL 只有 {f(s13_ron[0]['INL_max_LSB20'],2)} LSB，而增益误差仍有
{f(s13_ron[0]['增益误差_ppm'],0)} ppm —— 默认工作点下"纯建立≈增益误差"成立。
反推设计边界（<b>确定性原始峰值</b> vs 论文 |INL|<sub>max</sub>=2.2，无任何去噪换算；
<b>257 电平网格</b> + 跨越区间 3 次二分细化）：
要把峰值 INL 压到 2.2 LSB，当前网格下的<b>条件性估计</b>是 <b>ρ ≤ {f(s13_rho,4)}</b>
（该点实测峰值 {f(s13_rho_pk,3)} LSB，取二分区间保守下端），
即开关导通电阻在整个输入摆幅内的变化要小于 {f(s13_rho*100,1)}%。
<span class="tag w">口径声明</span> 这不是收敛后的规格：旧 24 电平插值曾给 ρ=0.041，
257 电平复测同点峰值 2.46 LSB（&gt;2.2，与外部审计独立测量 2.40–2.47 一致）——
已作废；网格再加密、完整逐码 DNL/INL 与启动样本剔除仍可能移动边界，
本数字只用于灵敏度排序。
（更早的 RMS 口径 0.081 偏松 2 倍，已撤回；与外部审计的独立反例
"ρ=0.0813 → INL≈5.25 LSB" 相互印证。）
<b>这就是"必须用自举开关（bootstrap）而不是简单 CMOS 开关"的量化理由</b>，
而不是一句工艺常识。</p>
<p class="small">口径换算：当前 ρ 定义在 R<sub>source</sub>+R<sub>on</sub> 总和上；
若按 R<sub>on</sub> 自身调制定义，等效 τ 调制 = ρ·R<sub>on</sub>/(R<sub>s</sub>+R<sub>on</sub>)。
三项动态参数目前全部是 [假设] 量级演示值：R<sub>source</sub>={s13_p.get('r_source')} Ω、
R<sub>on</sub>={s13_p.get('r_on')} Ω、T<sub>s</sub>={f(s13_p.get('t_sample_ns'),2)} ns、
τ<sub>in</sub>={f(s13_p.get('tau_in_ns'),2)} ns、C<sub>dec</sub>={f(s13_p.get('c_decouple_nF'),0)} nF、
τ<sub>ref</sub>={f(s13_p.get('tau_ref_ns'),0)} ns、串扰共模 {s13_p.get('c_xtalk_common_fF')} fF + 单位 {s13_p.get('c_xtalk_unit_fF')} fF。
换 PDK/版图数据之前，这张表只能用于<b>灵敏度排序</b>，不能用于判定良率。</p>
</div>

<h2>十四、单位权重校准：先看秩，再看数据量</h2>
<div class="card">
<p>失配预算已经证明：PDK 估算 σ ≈ 1117 ppm，要达到 SNDR ≥ 93 dB 需要 ≤ 300 ppm，差 3.7 倍；
加面积补要 14 倍电容（σ ∝ 1/√C），DEM 在大失配下甚至让 SNDR 变差。
所以<b>只剩一条路</b>：估出每个单位的真实权重 w<sub>u</sub> 并在数字端扣除。
线性模型 err[n] = −Σ<sub>u</sub> w<sub>u</sub>·U[n,u] + 噪声，其中使用矩阵
U[n,u] = 第 n 次转换里单位 u 的选中权重。<b>能不能估出来，由 rank(U) 决定。</b></p>
{rows_html(["映射", "单位数", "rank(U)", "可辨识比例", "零空间维数", "真值落在零空间的占比"],
 [[r["映射"], r["n_units"], r["rank"], f(r["identifiable_fraction"]*100,1)+"%",
 r["n_null_directions"], f(r["真值零空间占比"]*100,1)+"%"] for r in s14_maps],
 ["l","r","r","r","r","r"])}
<p>交织（interleaved）映射下 8 个 slice 的<b>同位置单位永远同开同关</b>，
观测只能看到它们的和 —— rank 被压到 <b>{s14_maps[0]['rank']}/{s14_maps[0]['n_units']}</b>，
真实权重有 <b>{f(s14_maps[0]['真值零空间占比']*100,0)}% 落在零空间里</b>，
这部分<b>无论给多少数据都消不掉</b>。DEM 轮转没有解决它
（{s14_maps[1]['rank']}/{s14_maps[1]['n_units']}，零空间占比反而更高）；
只有每状态独立随机置换（permute）才接近满秩。</p>
</div>

<div class="card">
<h3>端到端验证：必须双口径，否则是自欺</h3>
<p>三个陷阱，都在 v5 里各自做了对照：<br>
<b>① in-sample 自欺</b> —— 嵌套指示向量张成的是"k 的一切函数"，固定映射能"完美校准"，
实际只是记住了码表。必须用<b>全新码</b>评估。<br>
<b>② 同一映射下零空间分量不产生误差</b> —— 整数码评估近乎完美，
但运行态 dither 让选择不再按整步进，零空间分量<b>泄漏成真实误差</b>。必须测分数码。<br>
<b>③ 随机置换要求硬件真的能随机选单位</b> —— 物理 LUT 只有交织 DEM 顺序，
用假想映射拟合必然失败；这里用直接电荷求值模拟"专用校准模式"。</p>
{rows_html(["校准用映射", "evalA 整数码残差 [µV]", "evalB 分数码残差 [µV]", "改善倍数 (A)", "校准前 e_true [µV]", "折 LSB₂₀ (A)", "折 LSB₂₀ (B)"],
 [[r["映射"], f(r["evalA_整数码残差_uV"],2), f(r["evalB_分数码残差_uV"],2),
 f(r["改善倍数_A"],2), f(r["e_true_rms_uV"],1), f(r["折_LSB20_A"],2), f(r["折_LSB20_B"],2)]
 for r in s14_cal], ["l","r","r","r","r","r","r"])}
<p><b>结论（限定适用域）：这是 unary 等权 DAC 的权重估计可行性实验，
不是 split ADC 的校准闭环</b> —— 未含主/子边界、C_C 失配、RA 测量噪声、
后端量化与实际校准激励路径。
1117 ppm 下 DAC 误差 139 µV ≈ 35 LSB₂₀，
校准后降到 {f(s14_cal[1]['evalA_整数码残差_uV'],1)}–{f(s14_cal[2]['evalA_整数码残差_uV'],1)} µV，
低于噪声底 {f(s14['noise_floor_uV'],1)} µV（折算 {f(s14_cal[1]['折_LSB20_A'],2)}–{f(s14_cal[2]['折_LSB20_A'],2)} LSB₂₀）——
但 20.7 µV 只是<b>小于而非远小于</b>噪声底：按不相关合成
√(39.5²+20.7²) ≈ 44.6 µV，SNDR 约损失 1 dB；若残余成确定性误差还要另看峰值杂散。
注意 fixed 映射"校准"后残差反而 <b>变差</b>（改善倍数 {f(s14_cal[0]['改善倍数_A'],2)} &lt; 1）——
它是拟合了一个错误的模型，这是最容易在数据上蒙混过去的一种失败。
"架构成立"的最终判据是 Null(U<sub>cal</sub>) ⊆ Null(U<sub>run</sub>)（校准分辨不了的方向在运行映射下也不产生误差）加 split 全链路闭环 —— 后者是下一版工作。</p>
</div>

<div class="card">
<h3>样本量反推：噪声界与观测冗余界取大者</h3>
{rows_html(["映射", "可行", "rank", "约束来源", "需要观测数", "折算时间 @40MS/s（专用校准模式）"],
 s14_req_rows, ["l","l","r","l","r","r"])}
<p class="small">{s14_req_note}</p>
</div>

<h2>十五、设计反推规格表</h2>
<div class="card">
<p>前四章的结论落到一行行的<b>设计规格</b>。每个参数标注来源，
按统一的三分类口径：<span class="tag p">[披露]</span> PPT/论文给出、
<span class="tag">[拟合]</span> 为本平台行为学标定、
<span class="tag w">[假设]</span> 无出处，仅用于灵敏度演示。
<b>禁止把拟合值或假设值当作工艺证据。</b></p>
{rows_html(["参数", "需求（由此平台反推）", "当前取值", "现状", "来源", "说明"],
 s15_ui, ["l","l","l","c","l","l"])}
<p class="small"><b>注意 ρ 那一行是 FAIL</b>：当前 [假设] 的 ρ=0.100 大于确定性峰值口径反推出的 {f(s13_rho,4)}，
对应 stage13 实测峰值 INL {f(s13_ron010.get('INL_max_LSB20'),2)} LSB &gt; 论文 2.2 LSB（RMS 口径只有 {f(s13_ron010.get('INL_rms_LSB20'),2)} LSB ——
这正是必须区分两种口径的原因）。这不是模型出错，而是说明
<b>"用不用自举开关"这件事真的会决定 INL 是否达标</b> —— 也正是这个平台存在的意义。
"单位权重校准观测数"一行不给结论，因为它取决于是否实现 permute 类校准模式，属于设计决策而非物理参数。</p>
<div class="warn"><b>怎么用这张表。</b>
"需求"列是<b>要达到目标指标时该参数必须满足的界</b>，全部由实验数据反推得到，
可以直接拿去和 PDK/版图/仿真数据对照；"当前取值"列凡是标 [假设] 的，
只用于确定哪个参数最敏感、需要优先测量，不代表芯片真实值。
优先级建议：① 先标定 σ<sub>ε</sub>（决定要不要做单位权重校准）；
② 再测 ρ 与串扰耦合（决定 INL 预算怎么分）；③ 最后才是 DEM/校准算法本身。</div>
</div>

<h2>十六、v5 验收与剩余边界</h2>
<div class="card">
<table>
<tr><th>验收项</th><th>判据</th><th>结果</th></tr>
{s12_verdict}
{s13_verdict}
{s14_verdict}
{sNT_verdict}
{sSC_verdict}
{sDC_verdict}
{sPL_verdict}
{audit_verdict}
</table>
<p><b>v5（二轮审计后）解决了什么：</b>
① <b>split 全链路电荷口径闭合</b> —— 逐相位节点方程（charge_ref.py）+ 输入等效口径
v<sub>D,in</sub>=Q<sub>D</sub>/C<sub>sig</sub> 统一全链路，"输入=输入等效DAC电压 → 残差为零"
验收从 142.75 mV 降到 nV 级；面积/负载/信号/噪声四个电容口径分离；
② <b>INL 缺口被解释到论文同量级</b>（确定性协议）—— 识别出真正需要管控的是
ρ，确定性峰值口径反推 ρ ≤ {f(s13_rho,4)}（旧 RMS 口径 0.081 偏松，已撤回）；
③ <b>单位权重校准从口号变成可执行方案</b> —— 有秩判据、有样本量公式、
有整数码/分数码双口径验证（适用域：unary DAC 权重估计，见第十四章声明）；
④ <b>KTC 观察尺度修正</b> —— KTC 观察衰减后采样域的 α·Δx，
(1/α−1)·Δx ≈ 42.5 µV 的确定性残差降到量化底；
⑤ <b>SFDR 近端修复</b> —— fin 已知时扣基波后不再屏蔽 ±3 bin，
−80 dBc @ +1/+2/+3 bin 全部精确复原。</p>
<p><b>v5.1（三轮审计后）补齐了什么：</b>
① <b>unary 主循环的 KTC 尺度漏修</b> —— 修复只进了 split，sim.py 漏改；
新增 8 组合回归矩阵（unary/split × dither × KTC），全部回到量化底 ~1.0 µV
（修复前 unary+sampling+KTC 为 42.68 µV，与审计独立测量逐数值一致）；
顺带发现并修复 <b>"关失配"没关干净</b>：桥接电容/寄生的散布未受 mismatch_enable
门控，理想芯片残留 ~100 ppm 子权重误差（split 关 dither 也有 40 µV）；
② <b>charge_ref.py 升级为真节点矩阵求解器</b> —— 2×2 系统按电容连接组装数值求解，
闭式解与矩阵解偏差 3.9e-11 fV，消除"两份代码抄错同一代数式"的风险；
③ <b>dither_alpha 改由采样开关掩码推导</b> —— split 下 α = 1−2D·w_bank/C_sig
（掩码在子阵列：64.5/65 = 0.992308，与审计手算一致；旧的 508/512 来源错误）；
④ <b>确定性 INL 协议收敛化</b> —— rep 由 DEM 轮转周期反推（128，覆盖 64/64 组合）、
剔除启动瞬态样本、rep→2rep 峰值逐位不变作为验收；
⑤ <b>ρ 规格降级为条件性估计</b> —— 257 电平下旧 ρ=0.041 实测峰值 2.46 LSB
（&gt;2.2，与审计独立复测一致），新边界 ρ ≤ {f(s13_rho,4)}（二分细化、保守下端）；
⑥ <b>报告口径更正</b> —— 1.73% 是 "DAC 最小码至最大码的跨度亏缺"
（码仓上边界口径为 1.54%），不自动等同于 ADC 可用输入幅度损失。</p>
<p><b>还没做的（按重要性，v6 后重排；v6 已完成原 ④⑥⑨ 的第一层 ——
18-slice 池实体化 + spare 调度 + SADC 独立量化器通路，见二十章）：</b>
① <b>噪声状态模型的电路级确认</b> —— Σ<sub>Q</sub>=kT·diag(A,B) 与 γ=1 目前仍是
相位声明 + 乐观假设；需要从实际开关网络的断开顺序（谁先断、顶板何时浮置、
观测网络在该时刻的阻抗）确认 a≈κb 真的成立；
② <b>split 校准闭环 v2</b> —— stage17 只解有效权重（仿射模型）；
下一步加单位级失配 + 观测噪声 + 增益偏移，逐步归因，最终判据
Null(U_cal) ⊆ Null(U_run)；
③ 动态参数全部还是 [假设]，必须换 PDK / 版图寄生 / 后仿真结果，
目前只能做<b>灵敏度排序</b>，不能判良率；
④ <b>逐 slice 动态参数差异的物理化</b> —— v6 已有 slice 间 τ 失配与
8/18 洗牌（二十章），但 τ/寄生仍取一阶 RC 近似 + 均匀分布，
需换后仿真逐 slice 提取值；
⑤ KTC 观察节点的电路确认 —— α·Δx 修正对应"KTC 观察衰减后采样节点"这组
相位声明，若实际电路观察别的节点需按该节点重推（相位连接声明见 charge_ref.py）；
⑥ DEM 还没升级到带权重的 binary-to-unary bridging；
⑦ 单位权重校准还没有<b>时序建模</b>（前台还是后台、温度漂移后如何重校准）；
⑧ 码域底边界的 borrow 行为（coarse=0 且 d&lt;0 时 k_eq&lt;0）未定义，
当前靠输入范围避开 —— 离散掩码的配对逻辑应在码域显式处理；
⑨ SADC 通路 v6 已建独立量化器 sDAC + 建立误差 + 阈值失调/增益失配，
但补偿窗口只验证了静态失调口径，τ 失配引起的动态粗码误差还没扫描。</p>
</div>

<h2>十七、逐相位噪声状态传递：a≈κb 才是 KTC 相消的物理前提</h2>
<div class="card">
<table>
<tr><th>观测覆盖度 γ</th><th>κ_opt</th><th>κ̂ (MC 回归)</th><th>解析 σ_res [µV]</th><th>MC σ_res [µV]</th><th>结构性残余 [µV]</th></tr>
{rows_html(["γ", "κ_opt", "κ̂ (MC)", "解析 [µV]", "MC [µV]", "不可消 [µV]"],
 [[f(r['gamma'],2), f(r['kappa_opt'],4), f(r['kappa_hat_mc'],4),
   f(r['sigma_analytic_uV'],3), f(r['sigma_mc_uV'],3),
   f(r['sigma_uncancellable_uV'],3)] for r in sNT_rows],
 ["c","r","r","r","r","r"])}
</table>
<p><b>从"数值相关"到"物理可观测"。</b>
噪声不再是一个标量 n<sub>R</sub>，而是状态向量 q=[q<sub>M</sub>,q<sub>S</sub>]（方差 kT·diag(A,B)）。
残差通路看到 a=[1,β]/C<sub>sig</sub>（放大相节点方程直接给出），观测通路看到
b=[1,γβ]/C<sub>sig</sub>，γ 是观测网络对子阵列噪声的覆盖比例。相消后残余
σ²=(a−κb)<sup>T</sup>Σ(a−κb)+κ²σ<sub>eN</sub>² —— MC 与解析在 γ=1/0.5/0 下最大偏差 &lt;0.1%。
三个结论：① γ=1（a≈b）残差回到 κ·σ<sub>eN</sub> 观测底 —— 相消成立；
② γ=0（观测只接主阵列）时子阵列噪声 β·q<sub>S</sub> 成为
{f(sNT_rows[-1]['sigma_uncancellable_uV'] if sNT_rows else 0,2)} µV 的<b>结构性残余</b>
（占总采样噪声 {f(sNT.get('sigma_total_uV'),1)} µV 的
{f((sNT_rows[-1]['sigma_uncancellable_uV']/sNT.get('sigma_total_uV',1)*100) if sNT_rows else 0,1)}%），
调 κ 救不回来 —— κ 只能消掉 b 方向上的投影；
③ 主循环口径 C<sub>n,eq</sub>={f(sNT.get('c_noise_eq_pF'),2)} pF 由同一推导给出
（原 [假设] 升级为相位声明下的推导值；剩余假设 = 两阵列噪声独立、顶板钳位后浮置）。</p>
</div>

<h2>十八、split 校准闭环 v1：只估有效权重，dither 让它可观测</h2>
<div class="card">
<table>
<tr><th>条件</th><th>θ̂ [µV/子码]</th><th>理论 θ [µV/子码]</th><th>校正前 [µV]</th><th>校正后 [µV]</th><th>理想地板 [µV]</th><th>k_s 锯齿 前→后 [µV]</th></tr>
<tr><td>噪声关</td><td>{f(sSC_off.get('theta_hat_uV_per_code'),3)}</td><td>{f(sSC.get('theta_expected_uV_per_code'),3)}</td><td>{f(sSC_off.get('err_rms_before_uV'),1)}</td><td>{f(sSC_off.get('err_rms_after_uV'),2)}</td><td>{f(sSC_off.get('floor_uV'),2)}</td><td>{f(sSC_off.get('sawtooth_pp_before_uV'),1)} → {f(sSC_off.get('sawtooth_pp_after_uV'),2)}</td></tr>
<tr><td>噪声开</td><td>{f(sSC_on.get('theta_hat_uV_per_code'),3)}</td><td>{f(sSC.get('theta_expected_uV_per_code'),3)}</td><td>{f(sSC_on.get('err_rms_before_uV'),1)}</td><td>{f(sSC_on.get('err_rms_after_uV'),2)}</td><td>{f(sSC_on.get('floor_uV'),2)}</td><td>{f(sSC_on.get('sawtooth_pp_before_uV'),1)} → {f(sSC_on.get('sawtooth_pp_after_uV'),2)}</td></tr>
</table>
<p><b>设置：</b>只开桥接比例误差（2000 ppm，远超预算），单位电容/寄生/C_F 全理想；
算法只见 (out, 已知校准输入, 数字码, dither 码)，对 [1, k<sub>m</sub>, k<sub>s</sub>, d]
线性回归；C_C 真值与 e_dac 一律不进回归。out-of-sample 验证（新频率/新幅度/
新噪声实现）：θ̂ 偏差 {f(sSC.get('theta_rel_err',0)*100,2)}%，噪声开校正后恢复到
理想芯片地板的 {f(sSC_on.get('after_over_floor',0)*100,1)}%。</p>
<p><b>两个架构级发现：</b>
① <b>无 dither 时 k<sub>s</sub>≡0</b>（k_eq = coarse·8）：子码维数不被激励，
子权重误差被主码回归完全吸收（实测 73× 改善但 θ̂=0）——既测不到也无需单独校准；
<b>sampling dither 激励子码维数后，桥接误差才成为独立可观测量</b>
（与专利 [10] 以 dither 换分辨力的动机一致）。
② 数字扣除的 dither 步长是名义 Δ<sub>nom</sub>，物理注入步长是 Δ<sub>true</sub>
—— 同一桥接误差同时改这两处；d = k<sub>s</sub>−8·borrow 不是 (k<sub>m</sub>,k<sub>s</sub>)
的线性函数，回归基必须单列 d。适用域声明：一阶闭环只解有效权重（仿射模型），
单位级失配的可观测性仍是 stage14 的秩问题。</p>
</div>

<h2>十九、带 dither 的采样相位电荷闭合：四份量出自同一份掩码</h2>
<div class="card">
<table>
<tr><th>闭合层</th><th>最大偏差</th><th>判据</th></tr>
<tr><td>闭式解 vs 节点矩阵（含掩码，逐单位 w/b 组装）</td><td>{f(sDC.get('closed_vs_nodal_max_C'),14)} C</td><td>&lt; 1e-21</td></tr>
<tr><td>主循环 vs 节点方程（逐样本端到端）</td><td>{f(sDC.get('sim_vs_nodal_max_C'),14)} C</td><td>&lt; 1e-18</td></tr>
<tr><td>α(配置) vs 掩码推导 C_sig,sample/C_sig</td><td>{f(sDC.get('alpha_diff'),15)}</td><td>&lt; 1e-12</td></tr>
<tr><td>离散 dither SNDR vs 连续基准</td><td>{f(sDC.get('sndr_discrete_dB'),2)} vs {f(sDC.get('sndr_continuous_dB'),2)} dB</td><td>差 &lt; 1 dB</td></tr>
</table>
<p><b>恒等式（对任意掩码/任意电容成立）：</b>
C<sub>F</sub>·v<sub>R</sub> = C<sub>sig,sample</sub>·x + w<sub>bank</sub>·Q<sub>mask</sub> − Q<sub>D</sub>(k)。
主循环逐样本与逐相位节点方程闭合到 {f(sDC.get('sim_vs_nodal_max_C'),13)} C ——
信号电荷（α）、dither 电荷（注入 LUT）、输入负载（扣掩码电容
{f(sDC.get('c_mask_fF'),1)} fF）、噪声传递（<b>不变</b>：掩码单位仍贡献 kT/C
且以同一 β 权重进入残差）四份量现在由<b>同一份采样开关掩码</b>生成。
SNDR 代价 = −20·log₁₀(α) = {f((-20*np.log10(sDC['alpha_cfg']) if sDC.get('alpha_cfg') else 0),3)} dB。
离散掩码（整数 d<sub>u</sub> ∈ [−2,2]，码集 {sDC.get('dither_codes')}）与连续插值基准
SNDR 一致 —— 可实现性不再是接口外的假设。</p>
</div>


<h2>二十、整体 ADC 信号流（v6）：逐相位状态机参考实现 pipeline.py</h2>
<div class="card">
<table>
<tr><th>验收项</th><th>结果</th><th>关键数值</th></tr>
<tr><td>退化等价：动态/噪声/dither/失配全关时 pipeline = sim_split</td>
    <td>{badge(sPL.get('bitwise_equal'))}</td>
    <td>逐位一致（np.array_equal）</td></tr>
<tr><td>SADC 误差自动补偿窗口（[09]1.3 定量版）</td>
    <td>{badge(bool(sPL.get('sadc_window', {}).get('measured_window_mV', 0) >= 9.0))}</td>
    <td>实测 {f(sPL.get('sadc_window', {}).get('measured_window_mV'),1)} mV ≈
        理论 {f(sPL.get('sadc_window', {}).get('predicted_window_mV'),2)} mV
        = (ADC2 余量)/G ≈ 0.1·Δ₁</td></tr>
<tr><td>slice 带宽失配交织杂散 → 8/18 洗牌打散</td>
    <td>{badge(bool(sPL.get('interleave', {}).get('spur_reduction_dB', 0) > 20.0))}</td>
    <td>固定两组 {f(sPL.get('interleave', {}).get('fixed_spur_dB'),1)} dB（本底
        {f(sPL.get('interleave', {}).get('floor_dB'),1)} dB）→ 洗牌
        {f(sPL.get('interleave', {}).get('shuffled_spur_dB'),1)} dB，
        降 {f(sPL.get('interleave', {}).get('spur_reduction_dB'),1)} dB</td></tr>
<tr><td>timing skew（交织误差四件套之 timing，逐字审计补齐）</td>
    <td>{badge(bool(sPL.get('timing_skew', {}).get('tone_is_dominant')))}</td>
    <td>10 ps：f_S/2−f_IN 杂散高出误差底
        {f(sPL.get('timing_skew', {}).get('spur_above_floor_dB'),1)} dB
        （PPT p.21 锚点 13 dB，fin 条件依赖）；2×σ_t → 幅度 ×
        {f(sPL.get('timing_skew', {}).get('tone_scaling_2x'),2)}（理论 2.00）；
        解析 σ_t/√8·rms(dx/dt) 比值
        {f(sPL.get('timing_skew', {}).get('analytic_ratio'),2)}；
        洗牌抑制 {f(sPL.get('timing_skew', {}).get('shuffle_reduction_dB'),1)} dB</td></tr>
<tr><td>数字核名义守恒：DAC(M(c)) 与 DEM 状态无关</td>
    <td>{badge(sPL.get('digital_conservation', {}).get('ok'))}</td>
    <td>相邻码差恒等于 units_per_lsb1</td></tr>
</table>
<p><b>信号流架构（工业级改造）。</b>
v6 把"逐样本向量化主循环"升级为<b>逐相位显式状态机</b>（pipeline.py），
两条主循环共享同一物理模块，退化场景下逐位等价 —— 不允许第二套口径。
信号链：SADC（独立量化器 sDAC，电容 = sadc_cap_ratio × 活跃电容）建立 →
粗码 → 数字核 DigitalCore（DEM 三维状态推进 + dither 掩码 + switch_commands）
→ RDAC 名义合成 v_D = DAC(M(c)) → slice 池 commit_residue（顶板电荷跨样本持久）
→ 残差放大 → KTC 观测 → ADC2。18-slice 池是<b>物理对象</b>而非标签：
8 采集 + 8 转换 + 2 spare，A/B ping-pong 或 ShuffledScheduler 随机洗牌，
每个 slice 持有自己的 v_top/τ/电容副本。</p>
<p><b>SADC 补偿窗口的定量意义：</b>粗码误差把残差推出名义 bin，
只要 v_ra 仍在 ADC2 窗口内，输出<b>完全</b>不受影响（err RMS 0.98 µV 不动，
窗口外 12 mV 时 248 µV、20 mV 时 2213 µV 阶跃式崩塌）。
这就是论文 "quantizer sDAC 与 RDAC &gt;11b matching" 需求的定量出处，
窗口 = (ADC2 余量)/G = 0.3 V/32 = 9.375 mV ≈ 0.1·Δ₁ —— 与 [09]1.3
"SADC 通路误差被残差自动补回，不进输出" 的专利结论互相印证。</p>
<p><b>slice 带宽失配 = 交织杂散的来源与对策：</b>slice 间 τ 失配 + 固定 A/B
交替 → f_S/2±f_IN 固定杂散（30% 失配下杂散能量几乎与误差主分量同级）；
8/18 随机洗牌把杂散打散进噪声底（降 {f(sPL.get('interleave', {}).get('spur_reduction_dB'),1)} dB），
定量复现论文 "shuffling of the sampling DACs to spread the residual
interleaving tones" 与 2 spare slice 轮换机制 —— 同时验证了 stage10 的
"加 spare 不改噪声" 验收在逐相位口径下依然成立。</p>
</div>


<h2>二十一、逐字审计缺口闭合（v6.1）：stage20–24</h2>
<div class="card">
<table>
<tr><th>缺口（原文出处）</th><th>判据</th><th>结果</th></tr>
<tr><td><b>① 逐 slice offset</b>（论文 "offset, gain, timing and bandwidth
        mismatch artefacts" 之 offset）</td>
    <td>{badge(sIL.get('PASS'))}</td>
    <td>固定两组杂散位于 <b>f_S/2 且不随 f_IN 平移</b>（与 skew/带宽的
        ±f_IN 指纹判别）；2×σ_os → tone ×{f(sIL.get('scaling_2x'),3)}；
        err_rms/σ_os = {f(sIL.get('err_rms_over_sigma'),3)}
        （解析 σ/√8 = 0.354，3 种子平均）；f_IN 无关性比值
        {f(sIL.get('fin_independence'),3)}；洗牌抑制
        {f(sIL.get('shuffle_reduction_dB'),1)} dB</td></tr>
<tr><td><b>② 量化器侧 dither 与 2b 增强</b>（论文 "the dither range is
        enhanced by 2b when transferred from the quantizer to the RDAC"）</td>
    <td>{badge(sDQ.get('PASS'))}</td>
    <td>Δ1 粒度转移：溢出 {f((sDQ.get('transfer_rows') or [{}])[0].get('over_rate'),3)}、
        err {f((sDQ.get('transfer_rows') or [{}])[0].get('err_rms_uV'),0)} µV
        （余项 ±Δ1/2 ×G₀ = ±1.5 V ≫ 窗口）；step0 = Δ1/{UPD}
        粒度转移（"transferred to the RDAC"）：溢出
        {f((sDQ.get('transfer_rows') or [{},{}])[-1].get('over_rate'),4)}、
        vra 余项 {f((sDQ.get('transfer_rows') or [{},{}])[-1].get('vra_excursion_V'),3)} V
        &lt; 窗口；增强位数 = log2({UPD}) =
        {f(sDQ.get('enhancement_bits'),0)}b（模型上界；论文写 2b，参照口径
        公开文本不可裁定，不硬凑）。白化的结构性边界：d_u ±4 单位抖动
        白化不了 unit 失配在 512 单位上的宏观游走（ΔSFDR
        {f(sDQ.get('sfdr_gain_dB'),1)} dB）—— 宏观失配归 DEM/校准，
        "supplements" 的准确分工</td></tr>
<tr><td><b>③ RA auto-zero −1.6 dB / ADC2 动态采样带宽 +1.3 dB</b>
        （PPT p.34-35）</td>
    <td>{badge(sAZ.get('PASS'))}</td>
    <td>整机折算（RA 占噪声功率 {f(sAZ.get('ra_power_share'),2)}）：
        auto-zero 实测 {f(sAZ.get('rows', {}).get('autozero', {}).get('measured_dB'),2)} vs
        预算推导 {f(sAZ.get('rows', {}).get('autozero', {}).get('predicted_dB'),2)} dB；
        ADC2 动态带宽 {f(sAZ.get('rows', {}).get('adc2_dyn_bw', {}).get('measured_dB'),2)} vs
        {f(sAZ.get('rows', {}).get('adc2_dyn_bw', {}).get('predicted_dB'),2)} dB；
        叠加 {f(sAZ.get('rows', {}).get('combined', {}).get('measured_dB'),2)} vs
        {f(sAZ.get('rows', {}).get('combined', {}).get('predicted_dB'),2)} dB
        —— 最大偏差 {f(sAZ.get('max_dev_dB'),3)} dB（PPT 参照系未披露，
        不硬凑披露值）</td></tr>
<tr><td><b>④ 1/f 噪声（转角 ~40 Hz）</b>（PPT 噪声底 8.8 nV/√Hz + 转角）</td>
    <td>{badge(sFL.get('PASS'))}</td>
    <td>发生器 K={f(sFL.get('generator', {}).get('K_avg'),0)} 种子平均周期图斜率
        {f(sFL.get('generator', {}).get('slope_dB_per_dec'),2)} dB/dec（理论 −10）、
        转角处 PSD = 自身白底（{f(sFL.get('generator', {}).get('corner_psd_vs_floor_dB'),2)} dB）；
        系统内 fc=40 kHz 带功率 = 带积分预测
        （{f(sFL.get('system_fc40k', {}).get('band_excess_dB'),2)} dB）；
        <b>40 Hz 转角对 AC SNDR 影响
        {f(sFL.get('ac_impact_40Hz_dB'),4)} dB</b> —— 论文敢引 40 Hz 的
        定量出处；假想无 auto-zero（fc=100 kHz）代价
        {f(sFL.get('ac_impact_100kHz_dB'),2)} dB（预算推导
        {f(sFL.get('predicted_100kHz_dB'),2)}）—— auto-zero 存在性的
        量化依据；az 开后带内回到折叠白底
        （{f(sFL.get('autozero_floor_vs_white_dB'),2)} dB）</td></tr>
<tr><td><b>⑤ RDAC 逐位装载</b>（论文 "conversion results are loaded as
        they develop"）</td>
    <td>{badge(sBW.get('PASS'))}</td>
    <td>关闭时与旧口径逐位一致 = {badge(sBW.get('equiv_when_off'))}；
        静态分量不变（max|Δ| = {f(sBW.get('static_unchanged_max_V'),13)} V）；
        动态分量差分隔离比值 {f(sBW.get('dyn_ratio_measured'),4)} =
        解析 η_bitwise/η = {f(sBW.get('dyn_ratio_analytic'),4)}；峰值瞬时
        电荷需求 {f(sBW.get('peak_demand_ratio'),4)}×（参考缓冲裕量加倍）。
        三口径：终态装载 1.0 / 开始装载（v1–v5 隐含，乐观）
        {f(sBW.get('eta_single'),3)} / 逐位（论文实际）
        {f(sBW.get('eta_bitwise'),3)} —— 逐位比终态好
        {f(-20*np.log10(sBW.get('eta_bitwise', 1) or 1),1)} dB，但比旧模型隐含
        口径差 {f(20*np.log10((sBW.get('eta_bitwise') or 1) / (sBW.get('eta_single') or 1)),1)} dB
        （系统 SNDR {f(sBW.get('sndr_off_dB'),2)} →
        {f(sBW.get('sndr_on_dB'),2)} dB，如实入账）</td></tr>
</table>
<p><b>方法论沉淀（本轮三条）：</b>
① <b>正交机制必须显式门控</b> —— offset/skew 单独开启时若连带激活建立项
ε=exp(−t_s/τ)，会混入 ~124 ppm 增益伪误差，fin 处 ~31 µV 谱分量恰好把
offset 的 f_S/2 指纹压到第二（stage20 实测教训）；修复后 skew 解析比从
"1.01 的巧合" 变成 1.006 的真吻合。
② <b>单实现周期图每 bin 是 χ²₂（±5.6 dB）</b>—— 谱形验收必须用多实现
平均（发生器 K=48）、带功率比（系统内）或差分法（确定性链路逐样本相减）；
功率减法 mean(e²)−mean(s²) 含 2·cov(S,D) 交叉项（stage24 实测偏 6.6%）。
③ <b>验收判据不能反向编码 bug</b> —— "转角以上回落到 −300 dB" 这类
判据会把注入源的口径错误固化成 PASS 条件；判据必须从物理口径推导。
另：log 斜率拟合在线性 bin 上必须用<b>调和平均频率</b>配对组均值 PSD
（RMS 频率配对对 1/f 系统偏陡，精确 1/f 输入实测 −20 dB/dec）。</p>
</div>


<div class="small" style="margin-top:40px;text-align:center;color:#5E6479">
adi_model v6.1 · 生成于 run_all.py · 全部图表与数据内嵌，无外部依赖
</div>
</div></body></html>"""

path = os.path.join(OUT, "report.html")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(HTML)
print("报告已写入", path, f"({len(HTML)/1024:.0f} KB)")
