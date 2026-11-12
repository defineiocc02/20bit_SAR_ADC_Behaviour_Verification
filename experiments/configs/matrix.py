"""The A/C ablation matrix, as data.

Why this lives outside `tools/`: the matrix is an *experiment definition* — which
levers are switched on for each cell — and it is read by more than one script
(ablation, figures, acceptance evidence). Keeping it in the command-line tool
made it look like a tool detail and forced every consumer to import the tool.

失配 σ 不在这里写死：它引用 `DefaultParameters().unit_mismatch_std`
（该值由论文 DR 94.2 dB 与 NSD 9.3 nV/rtHz 两项指标反解得到，见
`docs/mechanisms/paper_metrics_calibration.md`）。这样"参数只有一个定义点"。

Nothing here computes a charge, a noise value or a metric. Each cell is only a
selection of existing switches; the production formulas stay in `adi_model`.

Fields per cell:

    id / label          stable identity, used in every figure and table
    mismatch_std        unit and bridge mismatch sigma for the manufactured chip
    noise               thermal noise on/off
    dem                 three-dimensional DEM masks
    uniform_plan        18-of-8 rotation vs the default schedule
    dither              independent charge-port dither (v2)
    calibration         frozen per-code calibration table
    status              RUN / NOT_RUN (never silently dropped)
"""
#: 制造芯片的失配 σ：唯一真源是 DefaultParameters，不在这里写第二份。
from adi_model.defaults import DefaultParameters as _DefaultParameters

_MANUFACTURED_MISMATCH = _DefaultParameters().unit_mismatch_std

def ablation_configs() -> list[dict]:
    """消融矩阵定义；每条都写明改了什么、没改什么。"""
    return [
        {
            "id": "A0_ideal",
            "label": "理想：零失配、零噪声",
            "mismatch_std": 0.0,
            "noise": False,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A1_mismatch",
            "label": "仅失配：sigma=论文反解值，无噪声、无 DEM",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": False,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A2_mismatch_noise",
            "label": "失配 + 热噪声（kT/C + RA）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A3_dem_rotation",
            "label": "A2 + 三维 DEM 掩码（横向按物理 ID、纵向按组内位置）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A4_dem_uniform_plan",
            "label": "A3 + 18 选 8 连续窗轮转（片选维度）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": True,
            "uniform_plan": True,
            "status": "RUN",
        },
        {
            "id": "A5_calibration",
            "label": "A2 + 冻结有效权重校准（外部已知斜坡拟合，同一条留出记录上前后对比）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "calibration": True,
            "status": "RUN",
        },
        {
            "id": "A6_dither_in_loop",
            "label": "A2 + 独立电荷口 dither（六位字，采样节点注入，码不偏移）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "dither": True,
            "status": "RUN",
        },
        {
            "id": "C1_dem_cal",
            "label": "A2 + 三维 DEM + 逐码校准",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "calibration": True,
            "status": "RUN",
        },
        {
            "id": "C2_dem_dither",
            "label": "A2 + 三维 DEM + dither（电荷口）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "dither": True,
            "status": "RUN",
        },
        {
            "id": "C3_all",
            "label": "A2 + DEM + dither + 逐码校准（全组合）",
            "mismatch_std": _MANUFACTURED_MISMATCH,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "dither": True,
            "calibration": True,
            "status": "RUN",
        },
        {
            "id": "A7_full_dynamic",
            "label": "全动态：参考建立 + RA 单极点 + ADC2 宽/窄带",
            "status": "NOT_RUN",
            "blocking_reason": (
                "ConversionEngine 已具备三者，但逐事件参考程序要求全部事件时刻与"
                "初始节点电压绑定；本轮未冻结该组物理参数，不用默认值猜"
            ),
        },
    ]
