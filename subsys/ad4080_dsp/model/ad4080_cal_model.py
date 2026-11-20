"""显式 DSP 算术约定的整数系统校正参考。

AD4080 Rev.B pp.71/93 未唯一确定非单位增益码的步长。调用方须提供
定点传递关系；本模块不定义物理增益映射，也不执行固定输入的前向拟合。
样本和 OFFSET 均采用 ADC 码。
"""

from numbers import Integral

IN_W = 20
MAXC = (1 << (IN_W - 1)) - 1
MINC = -(1 << (IN_W - 1))


def _integer(name, value, minimum, maximum, unit):
    """检查公共整数码边界，拒绝布尔值、非整数和范围外取值。

    name 与 unit 只用于错误定位；返回 Python 整数，不作截断或舍入。
    """
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"sys_corr: {name} [{unit}] must be an integer in [{minimum}, {maximum}]")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"sys_corr: {name} [{unit}] must be an integer in [{minimum}, {maximum}]")
    return result


def clip(value):
    """将整数饱和到 20 bit 补码输出范围 [-524288, 524287]。

    Args:
        value: ADC 整数码，不接受布尔值。

    Returns:
        饱和后的 Python 整数码。

    Raises:
        ValueError: value 为布尔值或非整数。
    """
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("clip: value [ADC codes] must be an integer")
    return max(MINC, min(MAXC, int(value)))


def sys_corr(
    x,
    off,
    gain,
    *,
    gain_numerator=None,
    gain_shift=None,
    gain_code_zero_supported=False,
):
    """应用调用方绑定的增益传递、OFFSET 和有符号饱和。

    非单位增益斜率为
    ``1 + (gain - 512) * gain_numerator / 2**gain_shift``。整数乘积先加
    半个量化步长再算术右移，正好半步时向正无穷方向舍入。这是工程数值规则，
    不代表器件未披露的内部舍入实现。GAIN=512 时不使用增益映射参数。

    Args:
        x: 20 bit 补码样本，范围 [-524288, 524287]，单位为 ADC 码。
        off: 12 bit 补码偏移，范围 [-2048, 2047]，单位为 ADC 码。
        gain: 10 bit 增益码，范围 [0, 1023]，512 表示单位增益。
        gain_numerator: 非单位增益所需的无量纲整数分子，范围 [1, 1023]。
        gain_shift: 非单位增益所需的小数位数，范围 [1, 30]。
        gain_code_zero_supported: 必须为 bool；为 True 才允许非单位增益的零码端点。

    Returns:
        四元组：饱和后的整数码、低端溢出布尔值、高端溢出布尔值和增益增量。
        两个溢出标志根据饱和前结果判断，增益增量仍采用 ADC 码。

    Raises:
        ValueError: 整数码或映射参数类型/范围非法，缺少非单位增益映射，
            或零码端点未获调用方明确绑定。
    """
    x = _integer("x", x, MINC, MAXC, "ADC codes")
    off = _integer("off", off, -2048, 2047, "ADC codes")
    gain = _integer("gain", gain, 0, 1023, "GAIN codes")
    if type(gain_code_zero_supported) is not bool:
        raise ValueError("gain_code_zero_supported: expected an explicit bool")
    if gain == 512:
        delta = 0
    else:
        missing = []
        if gain_numerator is None:
            missing.append("gain_numerator [dimensionless numerator]")
        if gain_shift is None:
            missing.append("gain_shift [fraction bits]")
        if missing:
            raise ValueError("sys_corr: non-unity correction requires " + ", ".join(missing))
        gain_numerator = _integer(
            "gain_numerator", gain_numerator, 1, 1023, "dimensionless numerator"
        )
        gain_shift = _integer("gain_shift", gain_shift, 1, 30, "fraction bits")
        if gain == 0 and not gain_code_zero_supported:
            raise ValueError("sys_corr: GAIN=0 requires an explicit endpoint binding")
        delta = (x * (gain - 512) * gain_numerator + (1 << (gain_shift - 1))) >> gain_shift
    corrected = x + delta + off
    return clip(corrected), corrected < MINC, corrected > MAXC, delta


if __name__ == "__main__":
    raise SystemExit(
        "The fixed-input forward-fit CLI is retired. Import sys_corr; "
        "use the sys_error_corr_contract_tb verification target for RTL checks."
    )
