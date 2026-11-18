"""Exercise host lazy selection, typed outputs, and atomic plan-to-export flow."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from test_glb_audit import fixture, pack_glb
from test_model_audit_nodes import ExecutionBlocker, ModelAuditNodes
from test_model_scale import scale_module


class ModelSizePlanNodes(unittest.TestCase):
    run_node = ModelAuditNodes.run_node

    def setUp(self):
        ModelAuditNodes.setUp(self)
        self.plan_node = scale_module("model_scale_nodes").BAMidFarModelSizePlan()
        for i, edge in enumerate((4, 6, 8)):
            doc, binary = fixture(points=[(0, 0, 0), (edge, 0, 0), (0, 2, 1)])
            Path(self.paths[i]).write_bytes(pack_glb(doc, binary))

    def plan(self, active=(1, 2, 3), **options):
        args = {f"glb_{i}": self.paths[i - 1] if i in active else ExecutionBlocker(None)
                for i in (1, 2, 3)}
        args.update(route_report=json.dumps({"active_branches": list(active)}))
        args.update(options)
        return self.plan_node.run(**args)["result"]

    def test_plan_outputs_are_float_and_only_active_models_are_lazy(self):
        self.assertEqual(self.plan_node.RETURN_TYPES, ("FLOAT", "FLOAT", "FLOAT", "STRING"))
        self.assertEqual(self.plan_node.check_lazy_status(
            route_report='{"active_branches":[1,3]}', glb_1=self.paths[0]), ["glb_3"])
        required = self.plan_node.INPUT_TYPES()["required"]
        self.assertEqual(required["target_longest_edge"][1]["default"], 10)
        for name in ("target_width", "target_height", "target_depth"):
            self.assertEqual(required[name][1]["default"], 0)

    def test_plan_handles_zero_one_two_three_and_sparse_routes(self):
        for active in ((), (1,), (1, 2), (1, 2, 3), (1, 3)):
            slots = self.plan(active)
            report = json.loads(slots[-1])
            self.assertEqual(report["active_branches"], list(active))
            self.assertEqual([r["branch"] for r in report["outputs"]], list(active))
            for i in (1, 2, 3):
                if i in active:
                    self.assertAlmostEqual(slots[i-1], 10 / (2 * i + 2))
                else:
                    self.assertIsInstance(slots[i-1], ExecutionBlocker)

    def test_auto_plan_applies_three_independent_factors_once(self):
        slots = self.plan()
        result = self.run_node(scale_factor=3, scale_factor_1=slots[0],
                               scale_factor_2=slots[1], scale_factor_3=slots[2],
                               scale_plan=slots[3])["result"]
        report = json.loads(result[-1])
        self.assertEqual(report["scale_mode"], "target_size")
        for i, audit in enumerate(report["outputs"]):
            self.assertAlmostEqual(audit["longest_edge"], 10)
            self.assertAlmostEqual(audit["scale_factor"], slots[i])
            self.assertTrue(audit["target_size_verified"])
            self.assertEqual(audit["measured_bottom_center_after"], [0, 0, 0])

    def test_audit_resolves_connected_active_scales_but_ignores_inactive_scales(self):
        inputs = self.node.INPUT_TYPES()["optional"]
        for name in ("scale_factor_1", "scale_factor_2", "scale_factor_3", "scale_plan"):
            self.assertTrue(inputs[name][1]["lazy"])
        prompt = {"9": {"inputs": {"scale_factor_1": ["8", 0], "scale_factor_2": ["8", 1],
                                   "scale_plan": ["8", 3]}}}
        self.assertEqual(set(self.node.check_lazy_status(
            glb_1=self.paths[0], route_report='{"active_branches":[1]}',
            prompt=prompt, unique_id="9")), {"scale_factor_1", "scale_plan"})

    def test_sparse_plan_to_audit_skips_blocked_slots(self):
        slots = self.plan((1, 3))
        result = self.run_node(3, route_report='{"active_branches":[1,3]}',
                               glb_2=ExecutionBlocker(None), scale_factor_1=slots[0],
                               scale_factor_2=slots[1], scale_factor_3=slots[2],
                               scale_plan=slots[3])["result"]
        self.assertIsInstance(result[2], ExecutionBlocker)
        self.assertEqual(json.loads(result[-1])["processed_count"], 2)

    def test_bad_factor_source_or_plan_aborts_all_model_exports(self):
        slots = self.plan()
        good = json.loads(slots[-1])
        changed_path = dict(good, outputs=[dict(r) for r in good["outputs"]])
        changed_path["outputs"][1]["source_glb"] = self.paths[0]
        cases = [
            {"scale_factor_2": 9},
            {"scale_plan": json.dumps(dict(good, up_axis="Z"))},
            {"scale_plan": json.dumps(dict(good, active_branches=[1]))},
            {"scale_plan": json.dumps(changed_path)},
            {"scale_plan": "broken"},
        ]
        for index, case in enumerate(cases):
            args = dict(scale_factor_1=slots[0], scale_factor_2=slots[1],
                        scale_factor_3=slots[2], scale_plan=slots[3],
                        filename_prefix=f"reject{index}/asset")
            args.update(case)
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.run_node(**args)
            self.assertEqual(list((self.output / f"reject{index}").glob("*.glb")), [])
        path = Path(self.paths[1])
        doc, binary = fixture(points=[(0, 0, 0), (7, 0, 0), (0, 2, 1)])
        path.write_bytes(pack_glb(doc, binary))
        with self.assertRaises(ValueError):
            self.run_node(scale_factor_1=slots[0], scale_factor_2=slots[1],
                          scale_factor_3=slots[2], scale_plan=slots[3], filename_prefix="stale/asset")
        self.assertEqual(list((self.output / "stale").glob("*.glb")), [])

    def test_plan_without_connected_floats_uses_verified_plan_factors(self):
        slots = self.plan((1,))
        output = self.run_node(1, scale_plan=slots[-1])["result"]
        self.assertAlmostEqual(json.loads(output[-1])["outputs"][0]["longest_edge"], 10)

    def test_per_route_manual_override_is_not_multiplied_by_shared_fallback(self):
        output = self.run_node(scale_factor=2, scale_factor_1=.5)["result"]
        edges = [r["longest_edge"] for r in json.loads(output[-1])["outputs"]]
        self.assertEqual(edges, [2, 12, 16])
        for factor in (True, 0, -1, float("nan"), "2", ExecutionBlocker(None)):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                self.run_node(1, scale_factor_1=factor)

    def test_content_fingerprint_changes_when_source_is_replaced(self):
        before = self.plan_node.IS_CHANGED(glb_1=self.paths[0], route_report='{"active_branches":[1]}')
        doc, binary = fixture()
        Path(self.paths[0]).write_bytes(pack_glb(doc, binary))
        after = self.plan_node.IS_CHANGED(glb_1=self.paths[0], route_report='{"active_branches":[1]}')
        self.assertNotEqual(before, after)

    def use_output_relative_models(self):
        directory = self.output / "douyin3d"
        directory.mkdir()
        for i, source in enumerate(self.paths):
            target = directory / Path(source).name
            target.write_bytes(Path(source).read_bytes())
            self.paths[i] = target.relative_to(self.output).as_posix()

    def test_generated_relative_models_plan_and_export_reach_target_size(self):
        self.use_output_relative_models()
        try:
            slots = self.plan()
            result = self.run_node(scale_plan=slots[-1])["result"]
        except ValueError as exc:
            self.fail(f"Generated output-relative GLBs must resolve in the host output directory: {exc}")
        report = json.loads(result[-1])
        for i, audit in enumerate(report["outputs"]):
            self.assertAlmostEqual(slots[i], 10 / (2 * i + 4))
            self.assertAlmostEqual(audit["longest_edge"], 10)
            self.assertEqual(audit["source_glb"], str((self.output / self.paths[i]).resolve()))
            self.assertTrue(audit["target_size_verified"])
            self.assertTrue(audit["binary_payload_preserved"])

    def test_generated_relative_fingerprint_detects_replaced_bytes(self):
        self.use_output_relative_models()
        for node in (self.plan_node, self.node):
            before = node.IS_CHANGED(glb_1=self.paths[0], route_report='{"active_branches":[1]}')
            self.assertIsInstance(before, str)
            doc, binary = fixture(points=[(0, 0, 0), (13, 0, 0), (0, 2, 1)])
            target = self.output / self.paths[0]
            original = target.read_bytes()
            target.write_bytes(pack_glb(doc, binary))
            after = node.IS_CHANGED(glb_1=self.paths[0], route_report='{"active_branches":[1]}')
            self.assertIsInstance(after, str)
            self.assertNotEqual(before, after)
            target.write_bytes(original)

    def test_relative_and_absolute_aliases_reject_duplicate_route_two(self):
        self.use_output_relative_models()
        alias = str((self.output / self.paths[0]).resolve())
        with self.assertRaisesRegex(ValueError, "路 2"):
            self.plan(glb_2=alias)
        with self.assertRaises(ValueError):
            self.run_node(glb_2=alias, filename_prefix="aliases/asset")
        failure = json.loads(next((self.output / "aliases").glob("*验收失败.json")).read_text())
        self.assertEqual(failure["failed_branch"], 2)
        self.assertIn("同一模型", failure["error"])
        self.assertEqual(list((self.output / "aliases").glob("*.glb")), [])

    def test_invalid_final_measurement_is_rejected_before_any_write(self):
        slots = self.plan()
        with patch.dict(self.node.run.__globals__, {
                "verify_final_size": Mock(side_effect=ValueError("bad actual dimensions"))}):
            with self.assertRaises(ValueError):
                self.run_node(scale_plan=slots[-1], filename_prefix="postcheck/asset")
        self.assertEqual(list((self.output / "postcheck").glob("*.glb")), [])


if __name__ == "__main__":
    unittest.main()
