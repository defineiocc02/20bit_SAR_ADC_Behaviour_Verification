# =============================================================================
# 文件名     : test_sram_macro_gate.py
# 模块名     : test_sram_macro_gate
# 版本       : v1.0
# 功能       : 用可公开的合成 LEF/Liberty 验证实物接收的通过、冲突与缺证路径。
# 设计说明   : 测试数据按实际接口宽度生成，没有任何工艺厂商模型。
#              合成夹具的唯一成功状态是 FIXTURE_PASS。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sram_macro_gate as gate


# =============================================================================
# 1. 可公开的宏、引脚、工艺角与输入视图
# =============================================================================
NAME = "FIXTURE_ONLY_4096x20"
# 位映射采用低位先列，直接复用真实项目的 12 位地址和 20 位读写合同。
ADDRESSES = [f"A[{i}]" for i in range(12)]
DATA_IN = [f"D[{i}]" for i in range(20)]
DATA_OUT = [f"Q[{i}]" for i in range(20)]
SIGNAL_INPUTS = ["CLK", "CEb", "WEb", *ADDRESSES, *DATA_IN]
PG = {"VPWR": "power", "VGND": "ground"}


def make_lef(width=280, height=80, symmetry="X Y R90", omit=None, out_of_body=False, obs=True, name=NAME):
    # 几何只用人工小矩形。PORT 全部非退化，实际有 CLOCK/PG/信号与 OBS。
    pins = []
    for idx, pin in enumerate([*SIGNAL_INPUTS, *DATA_OUT, *PG]):
        if pin == omit:
            continue
        direction = "INOUT" if pin in PG else "OUTPUT" if pin in DATA_OUT else "INPUT"
        use = PG[pin].upper() if pin in PG else "CLOCK" if pin == "CLK" else "SIGNAL"
        y = idx + 1
        right = width + 1 if out_of_body and pin == "CLK" else 2
        pins.append(f"PIN {pin} DIRECTION {direction} ; USE {use} ; PORT LAYER M1 ; RECT 1 {y} {right} {y+1} ; END END {pin}")
    obstruction = "OBS LAYER M2 ; RECT 10 10 20 20 ; END" if obs else ""
    # 只用人工生成的原语和命名，回归不会读目标工艺的任何受限文件。
    return (f'VERSION 5.8 ; UNITS DATABASE MICRONS 1000 ; END UNITS\n'
            f'MACRO {name} CLASS BLOCK ; ORIGIN 0 0 ; SIZE {width} BY {height} ; SYMMETRY {symmetry} ;\n'
            + "\n".join(pins) + f"\n{obstruction}\nEND {name}\nEND LIBRARY\n")


def make_lib(oc="typ", voltage="1.0", temperature="25", process="1", units="1ns",
             min_period="12", tcq="1.5", words_bits=20, omit_pg=None, glitch_clock=False, packed_output=False):
    # 表值是虚构数字，只用于测试跨单位与超限拒收，所有位都绑定同一时钟。
    clock = "false" if glitch_clock else "true"
    inputs = [f'pin ("{pin}") {{ direction : input; }}' for pin in SIGNAL_INPUTS if pin != "CLK"]
    pg_pins = [f'pg_pin ("{pin}") {{ pg_type : primary_{role}; }}' for pin, role in PG.items() if pin != omit_pg]
    timing = ('timing () { related_pin : "CLK"; timing_type : rising_edge; '
              f'cell_rise (scalar) {{ values ("{tcq}"); }} cell_fall (scalar) {{ values ("{tcq}"); }} }}')
    outputs = [f'pin ("{pin}") {{ direction : output; {timing} }}' for pin in DATA_OUT]
    bus_type = ''
    if packed_output:
        # 总线视图也从实际 bit 范围扩成与 LEF 对应的 20 个命名，检验继承弧。
        bus_type = ('bus_naming_style : "%s[%d]"; '
                    'type (Q20) { base_type : array; data_type : bit; bit_width : 20; bit_from : 19; bit_to : 0; } ')
        outputs = [f'bus (Q) {{ bus_type : Q20; direction : output; {timing} }}']
    return (f'library (fixture_{oc}) {{ time_unit : "{units}"; voltage_unit : "1V"; '
            f'default_operating_conditions : {oc}; '
            f'operating_conditions ({oc}) {{ process : {process}; voltage : {voltage}; temperature : {temperature}; }} '
            f'{bus_type}'
            f'cell ({NAME}) {{ memory () {{ type : ram; address_width : 12; word_width : {words_bits}; }} '
            f'pin (CLK) {{ direction : input; clock : {clock}; minimum_period : {min_period}; }}\n'
            + "\n".join(inputs + pg_pins + outputs) + "\n} }\n")


# =============================================================================
# 2. CLI 回归及几何、PVT、功能反例
# =============================================================================
class SramMacroGateTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="ad4080-sram-fixture-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.requirements = {
            "process_id": "synthetic_no_pdk", "release": "fixture-v1", "technology_node_nm": 40,
            "allowed_physical_layers": ["M1", "M2"],
            "required_pvt": {
                "tt": {"operating_conditions": "typ", "process": 1, "voltage_v": "1.0", "temperature_c": 25,
                       "clock_period_ns": 25, "max_clock_to_q_ns": 8},
                "ss": {"operating_conditions": "slow", "process": "0.8", "voltage_v": "0.9", "temperature_c": 125,
                       "clock_period_ns": 25, "max_clock_to_q_ns": 8},
            },
        }
        self.supplier = {"source_kind": "fixture", "name": NAME, "process_id": "synthetic_no_pdk", "release": "fixture-v1",
                         "compiler_id": "python-fixture", "macro_revision": "fixture-r1", "technology_node_nm": 40,
                         "words": 4096, "bits_per_word": 20,
                         "port_model": "1RW", "read_cycles": 1, "write_width_bits": 20, "clock_edge": "rising",
                         "allowed_orientations": ["N"], "lef": "bank.lef", "liberty_by_pvt": {"tt": "tt.lib", "ss": "ss.lib"},
                         "pins": {"clock": "CLK", "chip_enable": {"name": "CEb", "active": "low"},
                                  "write_enable": {"name": "WEb", "active": "low"}, "address": ADDRESSES,
                                  "data_in": DATA_IN, "data_out": DATA_OUT, "pg": PG}}
        # 采用现行规划的四个左下角。实体为 280×80，外框含另外 2 µm halo。
        self.supplier["banks"] = [{"orient": "N", "ll_um": [x, y], "footprint_um": [x, y, x+282, y+82]}
                                  for x, y in ((488,1123), (786.5,1123), (488,1219), (786.5,1219))]
        (self.path / "bank.lef").write_text(make_lef(), encoding="utf-8")
        (self.path / "tt.lib").write_text(make_lib(), encoding="utf-8")
        # 慢角故意使用 ps。通过案例必须把 23000 ps 和 4500 ps 换算为 ns。
        (self.path / "ss.lib").write_text(make_lib(oc="slow", voltage="0.9", temperature="125", process="0.8",
                                                  units="1ps", min_period="23000", tcq="4500"), encoding="utf-8")

    def exercise(self, fixture=True):
        # 主入口本身负责状态判别，测试调用同一 argv 路径。
        reqs, source = self.path / "required.json", self.path / "supplier.json"
        reqs.write_text(json.dumps(self.requirements), encoding="utf-8")
        source.write_text(json.dumps(self.supplier), encoding="utf-8")
        out = StringIO()
        with redirect_stdout(out):
            rc = gate.main(["--requirements", str(reqs), "--candidate", str(source)] + (["--fixture"] if fixture else []))
        return rc, json.loads(out.getvalue())

    def expect_rejection(self, code, text):
        # 缺证与事实冲突分别断言状态、退出码和触发条件，避免误报用例命中。
        result, report = self.exercise()
        self.assertEqual(result, code, report)
        self.assertEqual(report["status"], "FAIL" if code == 1 else "UNKNOWN")
        self.assertIn(text, report["reason"])

    def test_valid_fixture_and_time_units(self):
        code, report = self.exercise()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "FIXTURE_PASS")
        self.assertEqual(report["banks"][1]["box_um"], ["786.5", "1123", "1066.5", "1203"])
        self.assertEqual(report["pvt"]["ss"]["minimum_period_ns"], "23.000")
        self.assertEqual(report["pvt"]["ss"]["max_clock_to_q_ns"], "4.500")

    def test_fixture_cannot_enter_supplier_gate(self):
        # 即使源视图通过数值检查，实物入口也不能把公开夹具记成供应方接受证据。
        code, report = self.exercise(fixture=False)
        self.assertEqual((code, report["status"]), (1, "FAIL"))

    def test_liberty_bus_template_matches_bit_pins(self):
        # 原 LEF 不变；Liberty Q 类型从 19 到 0。位名展开后仍应逐位绑定。
        (self.path / "tt.lib").write_text(make_lib(packed_output=True), encoding="utf-8")
        code, report = self.exercise()
        self.assertEqual(code, 0, report)

    def test_required_period_cannot_underspecify_40_msps(self):
        # 项目 JSON 若错误地放宽周期，不能把偏慢候选升级为满速通过。
        self.requirements["required_pvt"]["tt"]["clock_period_ns"] = 26
        self.expect_rejection(1, "40 MSPS")

    def test_missing_supplier_current_is_unknown(self):
        out = StringIO()
        with redirect_stdout(out):
            code = gate.main([])
        self.assertEqual((code, json.loads(out.getvalue())["status"]), (2, "UNKNOWN"))

    def test_bank_outside_slot_with_valid_size(self):
        # 尺寸可放下，B1 的真实放置整体向右挪后，跨出自己的包络。
        self.supplier["banks"][1] = {"orient": "N", "ll_um": [800,1123], "footprint_um": [800,1123,1080,1203]}
        self.expect_rejection(1, "B1 尺寸或放置越界")

    def test_area_fits_but_height_does_not(self):
        # 面积低于规划上限的宏也可能法向投影过高，必须按实际朝向逐轴匹配。
        (self.path / "bank.lef").write_text(make_lef(width=250, height=100), encoding="utf-8")
        self.assertLess(250*100, 290.5*88)
        self.expect_rejection(1, "250×100")

    def test_rotation_requires_library_symmetry_and_supplier_agreement(self):
        # 80×280 只能旋转。保留同样最终物理外框，先准入再拿走 R90 证明两边须一致。
        path = self.path / "bank.lef"
        path.write_text(make_lef(width=80, height=280), encoding="utf-8")
        self.supplier["allowed_orientations"] = ["W"]
        for bank in self.supplier["banks"]:
            bank["orient"] = "W"
        code, report = self.exercise()
        self.assertEqual(code, 0, report)
        path.write_text(make_lef(width=80, height=280, symmetry="X Y"), encoding="utf-8")
        self.expect_rejection(1, "LEF SYMMETRY")

    def test_same_name_wrong_liberty_memory(self):
        # 文件仍声明正确宏名与全部引脚，但内部字宽冲突。
        (self.path / "tt.lib").write_text(make_lib(words_bits=24), encoding="utf-8")
        self.expect_rejection(1, "Liberty memory 组织")

    def test_wrong_lef_macro_name(self):
        (self.path / "bank.lef").write_text(make_lef(name="FOREIGN_OTHER_NAME"), encoding="utf-8")
        self.expect_rejection(1, "匹配的 MACRO 数量为 0")

    def test_missing_pg_in_lef(self):
        (self.path / "bank.lef").write_text(make_lef(omit="VGND"), encoding="utf-8")
        self.expect_rejection(1, "VGND")

    def test_missing_pg_in_liberty(self):
        (self.path / "tt.lib").write_text(make_lib(omit_pg="VPWR"), encoding="utf-8")
        self.expect_rejection(1, "pg_pin 集合")

    def test_pin_outside_body_and_missing_obs(self):
        path = self.path / "bank.lef"
        path.write_text(make_lef(out_of_body=True), encoding="utf-8")
        self.expect_rejection(1, "顶点")
        path.write_text(make_lef(obs=False), encoding="utf-8")
        self.expect_rejection(2, "OBS 几何")

    def test_missing_corner_is_unknown(self):
        # 已交典型角和实际打包文件，删除慢角索引后不能当成角要求已满足。
        del self.supplier["liberty_by_pvt"]["ss"]
        self.expect_rejection(2, "缺少必需 PVT")

    def test_same_name_wrong_corner_voltage(self):
        # 同名宏与同角名不能掩盖实际读回电压错误。
        (self.path / "tt.lib").write_text(make_lib(voltage="0.8"), encoding="utf-8")
        self.expect_rejection(1, "voltage 为 0.8")

    def test_clock_declaration_and_wrong_read_cycles(self):
        path = self.path / "tt.lib"
        path.write_text(make_lib(glitch_clock=True), encoding="utf-8")
        self.expect_rejection(1, "未声明为时钟")
        path.write_text(make_lib(), encoding="utf-8")
        self.supplier["read_cycles"] = 2
        self.expect_rejection(1, "读一拍")

    def test_delayed_read_actual_liberty_table(self):
        # 仍宣称一拍协议。供应方表值超出消费沿分配的 tCQ 后应拒收。
        (self.path / "tt.lib").write_text(make_lib(tcq="8.001"), encoding="utf-8")
        self.expect_rejection(1, "最大 tCQ 8.001 ns")


if __name__ == "__main__":
    unittest.main()
