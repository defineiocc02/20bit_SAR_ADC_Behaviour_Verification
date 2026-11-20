# =============================================================================
# 文件名     : prepare_calibration.py
# 模块名     : prepare_calibration
# 版本       : v1.0
# 功能       : 用现有主机校准 CLI 生成产品仿真的 SPI 写入实体。
# 设计说明   : 直接调用校准回归的训练入口。估计、量化和测试信号生成均
#              复用校准目录。这里只检查留出集合和把返回的字节装成实体。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import json
import sys
from importlib.metadata import version
from pathlib import Path


def main():
    """参数为校准源码目录与本轮构建目录，文件只写入构建目录。"""
    calibration = Path(sys.argv[1]).resolve()
    directory = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(calibration))
    from verify_rtl import training_data

    # [来源] 原训练入口通过 subprocess 运行真正的 system_cal.py sine：
    # 已知参考正弦由 ADCToolbox 拟合。产品台架仅接其写字节，不重算增益。
    cases = training_data(directory)
    case = cases["sine"]
    held_out = {-300_000, 0, 250_000}
    if any(sample.ideal in held_out for item in cases.values() for sample in item["samples"]):
        raise ValueError("product holdout has already been used for fitting")
    result = case["fit"]
    registers = result["registers"]
    if (registers["mapping"], registers["k_gain"], registers["shift"]) != ("rtl_k_gain_shift", 1, 15):
        raise ValueError("CLI mapping must match the product SHIFT=15 test configuration")

    # [接缝] 物理 SPI 严格模式采用高地址起始、同一实体两字节。字节值
    # 全部来自刚刚运行的 CLI，禁止用 README 样例覆盖索引或量化结果。
    writes = registers["writes"]
    offset_word = (writes["0x26"] << 8) | writes["0x25"]
    gain_word = (writes["0x28"] << 8) | writes["0x27"]
    if offset_word != (registers["offset_code"] & 0xFFF) or gain_word != registers["gain_code"]:
        raise ValueError("CLI writes do not round trip to the declared signed OFFSET / GAIN")
    record = {
        "adctoolbox_version": version("adctoolbox"),
        "training_capture_sha256": case["capture_sha256"],
        "training_stimulus": case["stimulus"],
        "holdout_ideal_codes": sorted(held_out),
        "cli_result": result,
    }
    (directory / "calibration_plan.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    (directory / "calibration_words.txt").write_text(f"{offset_word:04x} {gain_word:04x}\n")
    print(f'ADCToolbox {record["adctoolbox_version"]}, '
          f'OFFSET={registers["offset_code"]} (0x{offset_word:03X}), GAIN=0x{gain_word:03X}, K=1 SHIFT=15')
    print("PRODUCT_ADCTOOLBOX_PLAN PASS")


if __name__ == "__main__":
    main()
