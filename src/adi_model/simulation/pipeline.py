"""唯一公开pipeline入口；所有未知物理与数字绑定由调用者显式提供。"""

from __future__ import annotations

from adi_model.simulation.pipeline_engine import execute_pipeline


def run_pipeline(
    cfg,
    input_fn,
    n_samples,
    *,
    pool,
    mapper,
    sample_ids,
    dem_states,
    sampled_top_v,
    sampled_subnode_v,
    reference_high_v,
    reference_low_v,
    nominal_reference_high_v,
    nominal_reference_low_v,
    static_charge_domain,
    state=None,
    rng=None,
    ra_input_error_v=None,
    rdac_noise_cap_f=None,
    sampling_control=None,
    initial_reference_error_v=None,
    initial_ra_output_v=None,
    initial_adc2_input_v=None,
    dither_program=None,
    dither_port=None,
):
    """执行实际pool、整数控制与独立数字观测的raw记录。

    Args:
        cfg: 显式Config，9bit目标不被入口改写。
        input_fn: 单孔径波形t[s]→输入V。
        n_samples: raw笔数N；无效样本保留。
        pool: 明确chip/codebook/mask/acquisition/schedule绑定的PhysicalSlicePool。
        mapper: 显式粗码→RDAC码表，参考动态另有逐事件表。
        sample_ids: (N,)实际整数身份，不生成或跳过启动事务。
        dem_states: (N,)实际DEM整数状态，不生成旧PRNG。
        sampled_top_v: 采集顺序的标量或(N,A)真实主节点V。
        sampled_subnode_v: 同顺序真实sub节点V，不能默认零。
        reference_high_v: 实际全局高参考驱动基准V，标量或(N,)；动态误差另叠加。
        reference_low_v: 实际全局低参考驱动基准V，标量或(N,)。
        nominal_reference_high_v: 独立数字已知高轨V。
        nominal_reference_low_v: 独立数字已知低轨V。
        static_charge_domain: 请求静态学习资格，实际域由引擎保守核验。
        state: 可选DigitalState；没有冻结系数时只产生float诊断。
        rng: 可选噪声Generator，活跃源仍有明确参数和seed合同。
        ra_input_error_v: 可选(N,)RA输入等效V，真实进入增益/限幅/ADC2前。
        rdac_noise_cap_f: 活跃采样噪声所需显式(N,)Ceq[F]。
        sampling_control: 仅传给实际采集binding的数字控制。
        initial_reference_error_v: 动态参考所需显式初始两轨误差V。
        initial_ra_output_v: 动态RA所需显式初始输出V。
        initial_adc2_input_v: 动态ADC2所需显式初始输入V，不默填零。
        dither_program: 可选Fig19静态等效lower/upper控制（v1 code-shift 拓扑）；
            与dither_enable一致，真实进入SADC/RDAC采样及逐事件码处理。
        dither_port: 可选独立电荷口（v2 charge-port 拓扑）：RDAC 码不偏移，
            upper 段折算成残差节点注入；与 dither_program 互斥。

    Returns:
        SimResult；to_codes须显式format与冻结/显式校准系数。

    Side effects:
        重置并消费pool电气采集资格及活跃RNG，不学习、不改制造数据或Config。

    Source:
        源9bit/18/8架构与本轮显式绑定合同；Fig19仅为标明假设的静态等效端口。
    """
    return execute_pipeline(
        cfg,
        input_fn,
        n_samples,
        pool=pool,
        mapper=mapper,
        sample_ids=sample_ids,
        dem_states=dem_states,
        sampled_top_v=sampled_top_v,
        sampled_subnode_v=sampled_subnode_v,
        reference_high_v=reference_high_v,
        reference_low_v=reference_low_v,
        nominal_reference_high_v=nominal_reference_high_v,
        nominal_reference_low_v=nominal_reference_low_v,
        static_charge_domain=static_charge_domain,
        state=state,
        rng=rng,
        ra_input_error_v=ra_input_error_v,
        rdac_noise_cap_f=rdac_noise_cap_f,
        sampling_control=sampling_control,
        initial_reference_error_v=initial_reference_error_v,
        initial_ra_output_v=initial_ra_output_v,
        initial_adc2_input_v=initial_adc2_input_v,
        dither_program=dither_program,
        dither_port=dither_port,
    )
