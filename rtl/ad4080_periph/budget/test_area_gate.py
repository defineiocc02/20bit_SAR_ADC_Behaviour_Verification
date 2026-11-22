# =============================================================================
# 文件名     : test_area_gate.py
# 模块名     : test_area_gate
# 版本       : v1.0
# 功能       : 回归计划面积、临界边界、附带宏去重和命令行失败路径。
#
# 设计说明   :
#   * 与项目一起冻结的规划数值用于检测门槛漂移。另用小矩形构造可独立
#     手算的并集反例，模拟共边、跨分区条带与孔隙。
#   * 用退出码和数值共同验收，覆盖自动化把无效报告误当作超额或通过的风险。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import contextlib
import io
import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import area_gate


EXAMPLE = Path(__file__).with_name("example_occupancy.json")


# =============================================================================
# 1. 基线容量与门槛
# =============================================================================
class AreaGateTests(unittest.TestCase):
    def setUp(self):
        self.plan = area_gate.read_json(area_gate.DEFAULT_PLAN)

    def command(self, *args):
        # 直接走 CLI 入口，argparse 中止路径也纳入同一退出码 contract。
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = area_gate.main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_base_limits_from_live_plan(self):
        # 三块 IP 规划共 250000 µm²，两条禁放共 25000 µm²，取 60% 后为 135000。
        result = area_gate.area_limits(self.plan)
        self.assertEqual(result["ip_gross_um2"], 250000)
        self.assertEqual(result["ip_planned_row_blockage_um2"], 25000)
        self.assertEqual(result["ip_effective_rows_um2"], 225000)
        self.assertEqual(result["ip_pre_cts_max_um2"], 120000)
        self.assertEqual(result["ip_final_cells_max_um2"], 135000)
        self.assertEqual(result["ip_cts_eco_reserve_um2"], 15000)

    def test_bounds_and_area_units(self):
        # 同样微小的 µm² 超额经过 mm² 换算后仍必须失败，避免浮点比较误判。
        cases = (
            ("pre-cts", "119999.99", "um2", 0, Decimal("0.01")),
            ("pre-cts", "0.120", "mm2", 0, Decimal(0)),
            ("pre-cts", "120000.01", "um2", 1, Decimal("-0.01")),
            ("final", "134999.99", "um2", 0, Decimal("0.01")),
            ("final", "0.135", "mm2", 0, Decimal(0)),
            ("final", "0.13500001", "mm2", 1, Decimal("-0.01")),
            ("final", "0.135" + "0" * 28 + "1", "mm2", 1, Decimal("-1e-26")),
        )
        for stage, area, unit, expected_code, margin in cases:
            with self.subTest(stage=stage, area=area, unit=unit):
                code, result, error = self.command("--stage", stage, "--cell-area", area,
                                                   "--area-unit", unit)
                self.assertEqual(error, "")
                self.assertEqual(code, expected_code)
                self.assertEqual(result["status"], "AREA_FAIL" if expected_code else "AREA_PASS")
                self.assertEqual(Decimal(result["stage_margin_um2"]), margin)

    def test_limits_without_report(self):
        code, result, _ = self.command()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "LIMITS_ONLY")
        self.assertNotIn("reported_cells_um2", result)

    def test_reject_wrong_unit_and_incomplete_stage(self):
        for args in (("--stage", "pre-cts", "--cell-area", "120000", "--area-unit", "um"),
                     ("--stage", "pre-cts", "--cell-area", "120000")):
            with self.subTest(args=args):
                code, result, error = self.command(*args)
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertTrue(error)

    def test_reject_nonfinite_or_negative_area(self):
        for area in ("-1", "NaN", "inf", "123,000"):
            with self.subTest(area=area):
                code, result, error = self.command("--stage", "pre-cts", "--cell-area", area,
                                                   "--area-unit", "um2")
                self.assertEqual(code, 2)
                self.assertIsNone(result)
                self.assertTrue(error)

    def test_reject_wrong_plan_coordinate_units(self):
        self.plan["units"] = "dbu"
        with self.assertRaisesRegex(ValueError, "plan.units"):
            area_gate.area_limits(self.plan)

    def test_reject_inconsistent_plan_threshold(self):
        self.plan["ip_pre_cts_target_um2"] = 135001
        with self.assertRaisesRegex(ValueError, "高于基准"):
            area_gate.area_limits(self.plan)

    def test_reject_plan_ip_in_pad_ring(self):
        # 扩至 x=100 会把横带多算 9,000 µm²，其中一部分侵入 Pad 环。
        next(part for part in self.plan["partitions"] if part["id"] == "IP")["rects"][0][0] = 100
        with self.assertRaisesRegex(ValueError, "IP 分区须完整位于 plan.core"):
            area_gate.area_limits(self.plan)

    def test_reject_duplicate_occupancy_key(self):
        # 末尾同名空条带曾掩盖 10,500 µm² 的实际新增占用。必须退出 2 且无通过 JSON。
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"units":"um","row_blockages":[[200,1015,500,1050]],"row_blockages":[]}')
            code, result, error = self.command("--occupancy", str(path), "--stage", "final",
                                               "--cell-area", "135000", "--area-unit", "um2")
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertIn("重复 JSON 字段 row_blockages", error)

    # =========================================================================
    # 2. 宏实体与禁放并集
    # =========================================================================
    def test_halo_pdn_and_existing_blockage_overlap(self):
        # 9900 + 2000 - 450 = 11450。与旧禁放相交 2750 + 250 - 125，新增 8575。
        result = area_gate.area_limits(self.plan, area_gate.read_json(EXAMPLE))
        self.assertEqual(result["ip_extra_footprint_union_um2"], 11450)
        self.assertEqual(result["ip_extra_row_loss_um2"], 8575)
        self.assertEqual(result["ip_effective_rows_um2"], 216425)
        self.assertEqual(result["ip_pre_cts_max_um2"], 114855)
        self.assertEqual(result["ip_final_cells_max_um2"], 129855)
        for area, expected_code in (("114855", 0), ("114855.01", 1)):
            code, _, _ = self.command("--occupancy", str(EXAMPLE), "--stage", "pre-cts",
                                      "--cell-area", area, "--area-unit", "um2")
            self.assertEqual(code, expected_code)

    def test_row_blockage_is_clipped_to_ip(self):
        # IP 条带的腿形外侧没有底层行，跨区 PDN 通道只扣 IP 的 50×10。
        occ = {"units": "um", "row_blockages": [[420, 1100, 600, 1110]]}
        result = area_gate.area_limits(self.plan, occ)
        self.assertEqual(result["ip_extra_row_loss_um2"], 500)

    def test_reject_row_blockage_outside_die(self):
        # 片内跨 IP 的裁剪仍有效。完全界外或部分界外都须报告输入错误。
        for box in ([1510, 920, 1600, 960], [-5, 920, 170, 960]):
            with self.subTest(box=box):
                occ = {"units": "um", "row_blockages": [box]}
                with self.assertRaisesRegex(ValueError, "row_blockages 须完整位于裸片内"):
                    area_gate.area_limits(self.plan, occ)

    def test_overlapping_halos_are_counted_once(self):
        occ = {"units": "um", "macros": [
            {"name": "a", "body": [250, 1040, 270, 1060], "halo": [245, 1035, 275, 1065]},
            {"name": "b", "body": [275, 1040, 295, 1060], "halo": [270, 1035, 300, 1065]},
        ]}
        # 共 5×30 的 halo，两个实体之间恰可共用宏间路由的行禁放投影。
        result = area_gate.area_limits(self.plan, occ)
        self.assertEqual(result["ip_extra_row_loss_um2"], 1650)

    def test_macro_can_span_the_partition_seam(self):
        # 同一 IP 多个规划矩形共边，单宏可跨缝，仍需完整落于联合区内。
        occ = {"units": "um", "macros": [{"name": "a", "body": [270, 1080, 310, 1120]}]}
        self.assertEqual(area_gate.area_limits(self.plan, occ)["ip_extra_row_loss_um2"], 1600)

    def test_macro_rejects_halo_in_missing_corner(self):
        # 两个对角象限都位于 IP 的外接矩形内，只有下侧在 IP 并集中。
        occ = {"units": "um", "macros": [
            {"name": "a", "body": [445, 1080, 460, 1095], "halo": [430, 1070, 480, 1120]},
        ]}
        with self.assertRaisesRegex(ValueError, "足迹超出"):
            area_gate.area_limits(self.plan, occ)

    def test_reject_overlapping_macro_bodies(self):
        occ = {"units": "um", "macros": [
            {"name": "a", "body": [250, 1040, 270, 1060]},
            {"name": "b", "body": [260, 1050, 280, 1070]},
        ]}
        with self.assertRaisesRegex(ValueError, "实体发生重叠"):
            area_gate.area_limits(self.plan, occ)

    def test_reject_occupancy_coordinate_units(self):
        with self.assertRaisesRegex(ValueError, "occupancy.units"):
            area_gate.area_limits(self.plan, {"units": "mm"})


if __name__ == "__main__":
    unittest.main()
