# =============================================================================
# 文件名     : test_characterize_registers.py
# 模块名     : 实测寄存器传递关系回归
# 版本       : v1.0
# 功能       : 核对同板单位档归一和非单位增益下的 OFFSET 步长。
# 设计说明   : 使用不同斜率与偏置的试验器件，避免已写的估计公式充当测试预期。
# 变更记录   : 加入未回读单位档、非单调和原始采集保护反例。
# =============================================================================
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from characterize_registers import gain_table, offset_trim
from system_cal import CalibrationError


def scan_rows():
    # 取 VREF=2V 与 ±1V 得到整码 ±262144；下列增益均为二的有理数。
    # 固定的板级偏置和增益出现在每个档位，除以单位档后应准确相消。
    rows = []
    for code, multiplier in [(384, 255/256), (512, 1.0), (640, 257/256)]:
        for voltage in (-1, 1):
            raw = round(1.125 * multiplier * voltage * 262144 + 27)
            for noise in (-1, 0, 1):
                rows.append(dict(gain_code=code, vin_diff_v=voltage, code=raw+noise, offset_lsb=0))
    return rows


class RegisterCharacterization(unittest.TestCase):
    def test_table_normalizes_against_same_chip_unity(self):
        self.assertEqual(gain_table(scan_rows(), vref_v=2, code_format="signed20"),
                         [(384, 255/256), (512, 1.0), (640, 257/256)])

    def test_reject_missing_unity_or_changed_offset(self):
        # 单位档是增益表的物理归一基准；缺失时不能用邻近码假设它等于一。
        rows = scan_rows()
        with self.assertRaises(CalibrationError):
            gain_table([r for r in rows if r["gain_code"] != 512], vref_v=2, code_format="signed20")
        rows[0]["offset_lsb"] = 1
        with self.assertRaises(CalibrationError):
            gain_table(rows, vref_v=2, code_format="signed20")

    def test_equal_gains_are_not_swapped_or_interpolated(self):
        # 码值不同而输出斜率相等时，应提示重新量测，不能用排序制造单调性。
        rows = scan_rows()
        for row in rows:
            row["code"] = round(1.125 * row["vin_diff_v"] * 262144)
        with self.assertRaises(CalibrationError):
            gain_table(rows, vref_v=2, code_format="signed20")

    def test_offset_step_is_measured_at_actual_gain(self):
        # 受控向量故意取1.25码/步来分辨“先加后乘”和固定1码/步假设。
        def samples(offset, mean):
            return [dict(gain_code=600, vin_diff_v=0., offset_lsb=offset, code=mean+n) for n in (-1,0,1)]
        plan = offset_trim(samples(0,80) + samples(32,120), vref_v=3., code_format="signed20")
        self.assertEqual(plan["codes_per_offset_lsb"], 1.25)
        self.assertEqual(plan["offset_code"], -64)
        self.assertEqual(plan["writes"], {"0x25":192,"0x26":15})
        self.assertEqual(plan["predicted_mean_error_codes"], 0.)

    def test_reject_range_or_mixed_config(self):
        # 试调前后若还换了增益，观测到的差值无法归给 OFFSET 的一个步长。
        rows = [dict(gain_code=512, vin_diff_v=0, offset_lsb=o, code=3000+o) for o in (0,0,32,32)]
        with self.assertRaises(CalibrationError):
            offset_trim(rows, vref_v=3, code_format="signed20")
        rows[0]["gain_code"] = 511
        with self.assertRaises(CalibrationError):
            offset_trim(rows, vref_v=3, code_format="signed20")

    def test_board_table_is_readable_by_estimator_and_capture_kept(self):
        # 扫描结果与系统校准共用公开 CSV 接口，输出意外指回输入时数据必须保持。
        import json
        here=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/"scan.csv"; table=Path(td)/"gain.csv"; training=Path(td)/"dc.csv"
            with source.open("w", newline="") as f:
                writer=csv.DictWriter(f,fieldnames=scan_rows()[0].keys())
                writer.writeheader();writer.writerows(scan_rows())
            training.write_text("vin_diff_v,code\n-1,-262144\n-1,-262144\n1,262144\n1,262144\n")
            cmd=[sys.executable,str(here/"characterize_registers.py"),"gain-table","--csv",str(source),"--vref-v","2"]
            old=source.read_bytes()
            self.assertNotEqual(subprocess.run(cmd+["--output",str(source)],capture_output=True).returncode,0)
            self.assertEqual(old,source.read_bytes())
            subprocess.run(cmd+["--output",str(table)],check=True)
            out=subprocess.check_output([sys.executable,str(here/"system_cal.py"),"dc","--csv",str(training),
                                         "--vref-v","2","--gain-table",str(table)],text=True)
            self.assertEqual(json.loads(out)["registers"]["gain_code"],512)


if __name__=="__main__":
    unittest.main()
