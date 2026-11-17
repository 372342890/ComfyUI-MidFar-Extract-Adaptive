"""Behavior tests for uniform whole-asset size control."""
import inspect
import json
import math
from pathlib import Path
import sys
import unittest

PLUGIN = Path(__file__).parent
if not (PLUGIN / "model_audit_nodes.py").is_file():
    PLUGIN = Path("/Users/bytedance/Documents/Trae-CN/temp_20261111_01/ComfyUI-MidFar-Extract-Adaptive")
sys.path.insert(0, str(PLUGIN))
from test_glb_audit import core_module, fixture, pack_glb, unpack_glb
import test_model_audit_nodes as host_contract
from test_model_audit_nodes import ExecutionBlocker


class UniformModelScaling(unittest.TestCase):
    def setUp(self):
        self.core = core_module()
        self.assertIn("size_mode", inspect.signature(self.core.process_glb).parameters,
                      "Missing whole-asset size control")

    def source(self, **options):
        doc, binary = fixture(**options)
        return pack_glb(doc, binary)

    def test_enlargement_changes_final_size_and_preserves_all_asset_data(self):
        source = self.source()
        output, report = self.core.process_glb(source, size_mode="longest_edge", target_longest_edge=10)
        self.assertEqual(report["scale_factor"], 2.5)
        self.assertTrue(report["scaling_applied"])
        self.assertEqual(report["source_dimensions"], {"width": 4, "height": 4, "depth": 4})
        self.assertEqual(report["dimensions"], {"width": 10, "height": 10, "depth": 10})
        self.assertEqual(report["longest_edge"], 10)
        self.assertEqual(report["translation"], [-10, -7.5, -15])
        self.assertEqual(report["bounds_after"]["min"], [-5, 0, -5])
        self.assertEqual(report["bounds_after"]["max"], [5, 10, 5])
        self.assertEqual(report["camera_target"], [0, 5, 0])
        before, before_chunks = unpack_glb(source)
        after, after_chunks = unpack_glb(output)
        self.assertEqual(before_chunks, after_chunks)
        for key in ("meshes", "accessors", "bufferViews", "buffers", "images", "textures", "materials"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(before["nodes"], after["nodes"][:len(before["nodes"])])
        root = after["nodes"][after["scenes"][0]["nodes"][0]]
        self.assertFalse(any(key in root for key in ("matrix", "translation", "rotation", "scale")))
        offset = after["nodes"][root["children"][0]]
        self.assertEqual(offset["scale"], [2.5, 2.5, 2.5])

    def test_shrink_preserves_proportions_with_transformed_multiple_roots(self):
        nodes = [{"children": [1], "translation": [10, 0, 0]},
                 {"mesh": 0, "scale": [-2, 1, 1]},
                 {"mesh": 0, "translation": [-10, 1, 0]}]
        source = self.source(nodes=nodes, roots=[0, 2])
        output, report = self.core.process_glb(source, size_mode="longest_edge", target_longest_edge=7)
        self.assertEqual(report["scale_factor"], .5)
        self.assertEqual(report["source_dimensions"], {"width": 14, "height": 5, "depth": 4})
        self.assertEqual(report["dimensions"], {"width": 7, "height": 2.5, "depth": 2})
        self.assertEqual(report["triangles"], 2)
        self.assertEqual(report["ratios"], report["source_ratios"])
        self.assertEqual(self.core.inspect_glb(output)["bottom_center"], [0, 0, 0])
        self.assertEqual(unpack_glb(output)[0]["nodes"][:3], nodes)

    def test_z_up_uses_the_scaled_bottom_plane(self):
        output, report = self.core.process_glb(self.source(), up_axis="Z",
                                               size_mode="longest_edge", target_longest_edge=2)
        self.assertEqual(report["scale_factor"], .5)
        self.assertEqual(report["dimensions"]["height"], 2)
        self.assertEqual(report["bounds_after"]["min"][2], 0)
        self.assertEqual(self.core.inspect_glb(output, "Z")["bottom_center"], [0, 0, 0])

    def test_scale_without_bottom_origin_preserves_world_origin(self):
        output, report = self.core.process_glb(self.source(), bottom_origin=False,
                                               size_mode="longest_edge", target_longest_edge=8)
        self.assertEqual(report["translation"], [0, 0, 0])
        self.assertFalse(report["origin_applied"])
        self.assertEqual(report["bounds_after"], {"min": [4, 6, 8], "max": [12, 14, 16]})
        self.assertEqual(report["pivot_after"], [8, 6, 12])
        self.assertEqual(self.core.inspect_glb(output)["longest_edge"], 8)

    def test_same_target_reprocessing_is_idempotent(self):
        first, _ = self.core.process_glb(self.source(), size_mode="longest_edge",
                                         target_longest_edge=10)
        again, report = self.core.process_glb(first, size_mode="longest_edge", target_longest_edge=10)
        self.assertEqual(first, again)
        self.assertEqual(report["scale_factor"], 1)
        self.assertFalse(report["scaling_applied"])
        changed, changed_report = self.core.process_glb(first, size_mode="longest_edge",
                                                       target_longest_edge=2)
        self.assertEqual(changed_report["scale_factor"], .2)
        self.assertAlmostEqual(self.core.inspect_glb(changed)["longest_edge"], 2)

    def test_keep_mode_does_not_apply_the_target(self):
        source = self.source()
        output, report = self.core.process_glb(source, bottom_origin=False,
                                               size_mode="keep", target_longest_edge=10)
        self.assertEqual(output, source)
        self.assertEqual(report["scale_factor"], 1)
        self.assertFalse(report["scaling_applied"])
        self.assertEqual(report["longest_edge"], 4)

    def test_invalid_targets_and_modes_are_rejected(self):
        for value in (0, -1, math.nan, math.inf, True, "10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.core.process_glb(self.source(), size_mode="longest_edge",
                                      target_longest_edge=value)
        with self.assertRaises(ValueError):
            self.core.process_glb(self.source(), size_mode="stretch", target_longest_edge=10)


class SharedNodeScaling(unittest.TestCase):
    def setUp(self):
        host_contract.ModelAuditNodes.setUp(self)
        self.assertIn("size_mode", self.node.INPUT_TYPES()["required"], "Missing node size mode widget")

    def run_node(self, count=3, **options):
        return host_contract.ModelAuditNodes.run_node(self, count, **options)

    def test_widgets_append_after_existing_controls_for_compatibility(self):
        names = list(self.node.INPUT_TYPES()["required"])
        self.assertEqual(names[-2:], ["size_mode", "target_longest_edge"])
        self.assertEqual(self.node.INPUT_TYPES()["required"]["size_mode"][1]["default"], "keep")
        self.assertEqual(self.node.INPUT_TYPES()["required"]["target_longest_edge"][1]["default"], 10)

    def test_three_routes_export_configured_size_and_origin(self):
        result = self.run_node(3, size_mode="longest_edge", target_longest_edge=10)["result"]
        report = json.loads(result[-1])
        self.assertEqual(report["size_mode"], "longest_edge")
        self.assertEqual(report["target_longest_edge"], 10)
        self.assertEqual(report["processed_count"], 3)
        for audit in report["outputs"]:
            self.assertEqual(audit["longest_edge"], 10)
            self.assertEqual(audit["scale_factor"], 2.5)
            self.assertEqual(audit["measured_bottom_center_after"], [0, 0, 0])

    def test_limits_check_final_size_and_strict_failure_publishes_no_glb(self):
        good = self.run_node(1, size_mode="longest_edge", target_longest_edge=2,
                             max_longest_edge=3, strict_limits=True)["result"]
        self.assertEqual(json.loads(good[-1])["status"], "success")
        with self.assertRaises(ValueError):
            self.run_node(3, size_mode="longest_edge", target_longest_edge=10,
                          max_longest_edge=3, strict_limits=True, filename_prefix="failed/asset")
        self.assertEqual(list((self.output / "failed").glob("*.glb")), [])

    def test_inactive_routes_stay_blocked_when_scaling_enabled(self):
        for count in (1, 2):
            with self.subTest(count=count):
                result = self.run_node(count, size_mode="longest_edge",
                                       target_longest_edge=10)["result"]
                self.assertEqual(json.loads(result[-1])["processed_count"], count)
                for i in range(count, 3):
                    self.assertIsInstance(result[2*i], ExecutionBlocker)
                    self.assertIsInstance(result[2*i + 1], ExecutionBlocker)
        with self.assertRaises(ValueError):
            self.run_node(1, size_mode="longest_edge", target_longest_edge=0,
                          filename_prefix="invalid/asset")
        self.assertEqual(list((self.output / "invalid").glob("*.glb")), [])


if __name__ == "__main__":
    unittest.main()
