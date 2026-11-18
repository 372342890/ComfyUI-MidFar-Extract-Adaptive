"""ComfyUI host contracts for lazy route selection and atomic acceptance."""
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from test_glb_audit import fixture, pack_glb, unpack_glb

ROOT = Path(__file__).parent


class ExecutionBlocker:
    def __init__(self, message):
        self.message = message


def node_module():
    path = ROOT / "model_audit_nodes.py"
    if not path.is_file():
        return None
    package = types.ModuleType("_model_audit_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location("_model_audit_test.model_audit_nodes", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"_model_audit_test": package}):
        spec.loader.exec_module(module)
    return module


class ModelAuditNodes(unittest.TestCase):
    def setUp(self):
        module = node_module()
        self.assertIsNotNone(module, "Missing shared three-route model audit node")
        self.node = module.BAMidFarModelAudit()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "output"
        self.output.mkdir()
        folders = types.ModuleType("folder_paths")
        folders.get_output_directory = lambda: str(self.output)
        graph = types.ModuleType("comfy_execution.graph")
        graph.ExecutionBlocker = ExecutionBlocker
        execution = types.ModuleType("comfy_execution")
        execution.__path__ = []
        host = patch.dict(sys.modules, {"folder_paths": folders, "comfy_execution": execution,
                                       "comfy_execution.graph": graph})
        host.start()
        self.addCleanup(host.stop)
        self.paths = []
        for i in (1, 2, 3):
            doc, binary = fixture(nodes=[{"mesh": 0, "translation": [i, 0, 0]}])
            path = self.root / f"source{i}.glb"
            path.write_bytes(pack_glb(doc, binary))
            self.paths.append(str(path))

    def run_node(self, count=3, **options):
        args = {f"glb_{i}": self.paths[i-1] if i <= count else ExecutionBlocker(None)
                for i in (1, 2, 3)}
        args.update(route_report=json.dumps({"active_branches": list(range(1, count + 1)),
                                             "target_count": count}),
                    min_triangles=0, max_triangles=0)
        args.update(options)
        return self.node.run(**args)

    def test_lazy_routes_do_not_request_inactive_blocked_generators(self):
        inputs = self.node.INPUT_TYPES()
        for i in (1, 2, 3):
            self.assertTrue(inputs["optional"][f"glb_{i}"][1]["lazy"])
        self.assertFalse(inputs["required"]["filename_prefix"][1].get("forceInput", False))
        self.assertEqual(self.node.check_lazy_status(
            route_report='{"active_branches":[1],"target_count":1}'), ["glb_1"])
        self.assertEqual(self.node.check_lazy_status(
            route_report='{"active_branches":[1,3]}', glb_1=self.paths[0]), ["glb_3"])
        self.assertEqual(self.node.check_lazy_status(route_report='{"active_branches":[]}'), [])
        self.assertEqual(self.node.check_lazy_status(
            route_report='{"active_branches":[1]}', glb_1=self.paths[0]), [])

    def test_hidden_prompt_discovers_connections_without_leaking_credentials(self):
        prompt = {"99": {"inputs": {"glb_1": ["11", 2], "glb_3": ["23", 2]}},
                  "11": {"inputs": {"cookie": "DO_NOT_PUBLISH_SECRET"}}}
        self.assertEqual(self.node.check_lazy_status(prompt=prompt, unique_id="99"),
                         ["glb_1", "glb_3"])
        result = self.run_node(1, prompt=prompt, unique_id="99")
        report = result["result"][-1]
        self.assertNotIn("DO_NOT_PUBLISH_SECRET", report)
        self.assertNotIn("DO_NOT_PUBLISH_SECRET",
                         "".join(p.read_text() for p in self.output.rglob("*.json")))

    def test_zero_one_two_three_routes_create_only_actual_outputs(self):
        for count in (0, 1, 2, 3):
            with self.subTest(count=count):
                result = self.run_node(count, filename_prefix=f"count{count}/asset")
                slots = result["result"]
                self.assertEqual(len(slots), 7)
                record = json.loads(slots[-1])
                self.assertEqual(record["status"], "success")
                self.assertEqual(record["processed_count"], count)
                self.assertEqual(record["skipped_branches"], list(range(count + 1, 4)))
                self.assertEqual(len(list((self.output / f"count{count}").glob("*.glb"))), count)
                self.assertEqual(result["ui"]["text"], [slots[-1]])
                for i in range(3):
                    if i < count:
                        self.assertTrue(Path(slots[2*i]).is_file())
                        self.assertTrue(record["outputs"][i]["export_verified"])
                    else:
                        self.assertIsInstance(slots[2*i], ExecutionBlocker)
                        self.assertIsInstance(slots[2*i+1], ExecutionBlocker)
        self.assertEqual(len(self.node.RETURN_TYPES), 7)

    def test_warning_limits_and_strict_rejection_do_not_publish_models(self):
        result = self.run_node(1, max_longest_edge=3, max_file_mib=.0001,
                               min_triangles=3000, max_triangles=5000)
        record = json.loads(result["result"][-1])
        self.assertEqual(record["status"], "warning")
        self.assertEqual(len(record["outputs"][0]["warnings"]), 3)
        with self.assertRaises(ValueError):
            self.run_node(2, filename_prefix="strict/asset", strict_limits=True, max_longest_edge=3)
        self.assertEqual(list((self.output / "strict").glob("*.glb")), [])
        failure = list((self.output / "strict").glob("*验收失败.json"))
        self.assertEqual(len(failure), 1)
        self.assertEqual(json.loads(failure[0].read_text())["status"], "failed")

    def test_one_bad_active_model_aborts_all_outputs_and_reports_failure(self):
        bad = self.root / "bad.glb"
        bad.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            self.run_node(3, glb_2=str(bad), filename_prefix="bad/asset")
        self.assertEqual(list((self.output / "bad").glob("*.glb")), [])
        report = json.loads(next((self.output / "bad").glob("*验收失败.json")).read_text())
        self.assertEqual(report["failed_branch"], 2)
        with self.assertRaises(ValueError):
            self.run_node(2, glb_2=None, filename_prefix="missing/asset")
        self.assertEqual(list((self.output / "missing").glob("*.glb")), [])

    def test_output_paths_and_download_urls_are_unique_and_encoded(self):
        args = dict(filename_prefix="山体 输出/底部中心", base_url="https://byteartist.bytedance.net",
                    view_endpoint="/api/view")
        first = self.run_node(1, **args)["result"]
        second = self.run_node(1, **args)["result"]
        self.assertNotEqual(first[0], second[0])
        parsed = urlparse(first[1])
        self.assertEqual(parsed.path, "/api/view")
        query = parse_qs(parsed.query)
        self.assertEqual(query["filename"], [Path(first[0]).name])
        self.assertEqual(query["subfolder"], ["山体 输出"])
        self.assertEqual(query["type"], ["output"])
        alternate = self.run_node(1, filename_prefix="standard/asset", view_endpoint="/view")["result"]
        self.assertEqual(urlparse(alternate[1]).path, "/view")
        self.assertNotEqual(first[0], self.paths[0])
        self.assertEqual(unpack_glb(Path(first[0]).read_bytes())[1],
                         unpack_glb(Path(self.paths[0]).read_bytes())[1])

    def test_bad_reports_configs_and_traversal_paths_fail_before_model_output(self):
        for report in ("bad JSON", '{"active_branches":[1,1]}',
                       '{"active_branches":[4]}', '{"active_branches":[1],"target_count":2}',
                       '{"active_branches":[true]}', '{"status":"failed","active_branches":[1]}'):
            with self.subTest(report=report), self.assertRaises(ValueError):
                self.run_node(1, route_report=report)
        for prefix in ("../escape", "/absolute", "a/../../escape", "a\\b", "a//b"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                self.run_node(1, filename_prefix=prefix)
        for config in ({"max_longest_edge": -1}, {"max_file_mib": float("nan")},
                       {"min_triangles": 100, "max_triangles": 50},
                       {"base_url": "file:///private"}, {"view_endpoint": "https://external/view"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.run_node(1, **config)
        with self.assertRaises(ValueError):
            self.run_node(2, glb_2=self.paths[0])
        self.assertEqual(list(self.output.rglob("*.glb")), [])

    def test_detection_only_and_limits_disabled_keep_geometry(self):
        result = self.run_node(1, bottom_origin=False)["result"]
        self.assertEqual(Path(result[0]).read_bytes(), Path(self.paths[0]).read_bytes())
        report = json.loads(result[-1])
        self.assertFalse(report["outputs"][0]["origin_applied"])
        self.assertFalse(report["outputs"][0]["scaling_applied"])
        self.assertEqual(report["outputs"][0]["warnings"], [])

    def test_only_multiplier_input_is_exposed_with_identity_default(self):
        required = self.node.INPUT_TYPES()["required"]
        self.assertIn("scale_factor", required)
        self.assertEqual(required["scale_factor"][1]["default"], 1.0)
        self.assertNotIn("size_mode", required)
        self.assertNotIn("target_longest_edge", required)
        self.assertEqual(self.node.RETURN_NAMES,
                         ("glb_1", "download_url_1", "glb_2", "download_url_2",
                          "glb_3", "download_url_3", "report"))

    def test_shared_multiplier_preserves_relative_sizes_between_routes(self):
        self.assertIn("scale_factor", inspect.signature(self.node.run).parameters)
        source_edges = (4.0, 6.0, 8.0)
        for i, edge in enumerate(source_edges):
            doc, binary = fixture(points=[(0, 0, 0), (edge, 0, 0), (0, 2, 1)])
            Path(self.paths[i]).write_bytes(pack_glb(doc, binary))
        for factor in (2.0, 0.5, 1.0):
            with self.subTest(factor=factor):
                result = self.run_node(3, scale_factor=factor)["result"]
                record = json.loads(result[-1])
                self.assertEqual(record["scale_factor"], factor)
                self.assertEqual([a["longest_edge"] for a in record["outputs"]],
                                 [edge * factor for edge in source_edges])
                for audit in record["outputs"]:
                    self.assertEqual(audit["measured_bottom_center_after"], [0, 0, 0])
                    self.assertTrue(audit["binary_payload_preserved"])

    def test_multiplier_limits_check_final_dimensions_and_reject_invalid_input(self):
        self.assertIn("scale_factor", inspect.signature(self.node.run).parameters)
        with self.assertRaises(ValueError):
            self.run_node(1, scale_factor=2.0, strict_limits=True,
                          max_longest_edge=6.0, filename_prefix="scaled-limit/asset")
        self.assertEqual(list((self.output / "scaled-limit").glob("*.glb")), [])
        for factor in (0, -1, True, float("nan"), float("inf"), "2"):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                self.run_node(1, scale_factor=factor)

    def test_io_failure_cleans_partial_models(self):
        # Third file write fails after the first two completed.
        original = Path.write_bytes
        calls = []

        def fail_third(path, data):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("simulated disk failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", fail_third), self.assertRaises(ValueError):
            self.run_node(3, filename_prefix="disk/asset")
        self.assertEqual(list((self.output / "disk").glob("*.glb")), [])

    def test_analysis_range_uniformly_scales_models_100_200_300(self):
        self.assertIn("scaling_mode", inspect.signature(self.node.run).parameters)
        options = {f"analysis_{i}": json.dumps(
            {"scale_factor": i * 100, "faces": 4000, "scale_reason": "完整宽厚山群"})
            for i in (1, 2, 3)}
        slots = self.run_node(3, scaling_mode="analysis_range", **options)["result"]
        record = json.loads(slots[-1])
        self.assertEqual(record["scale_mode"], "analysis_range")
        self.assertEqual(record["scale_range"], [100, 300])
        for i, audit in enumerate(record["outputs"], 1):
            factor = i * 100
            self.assertEqual(audit["scale_factor"], factor)
            self.assertEqual(audit["dimensions"], dict(width=4*factor, height=4*factor, depth=4*factor))
            self.assertEqual(audit["measured_bottom_center_after"], [0, 0, 0])
            self.assertEqual(audit["scale_decision"]["source"], "vision")
            original_doc, original_chunks = unpack_glb(Path(self.paths[i-1]).read_bytes())
            final_doc, final_chunks = unpack_glb(Path(slots[2*(i-1)]).read_bytes())
            self.assertEqual(final_chunks, original_chunks)
            for key in ("meshes", "materials", "images", "textures", "accessors"):
                self.assertEqual(final_doc[key], original_doc[key])

    def test_analysis_range_fallback_clamps_only_active_routes(self):
        self.assertIn("scaling_mode", inspect.signature(self.node.run).parameters)
        slots = self.run_node(2, scaling_mode="analysis_range",
                              analysis_1='{"scale_factor":450}',
                              analysis_2='{"scale_factor":NaN}',
                              analysis_3=ExecutionBlocker(None))["result"]
        outputs = json.loads(slots[-1])["outputs"]
        self.assertEqual([a["scale_factor"] for a in outputs], [300, 200])
        self.assertEqual([a["scale_decision"]["source"] for a in outputs], ["clamped", "fallback"])
        self.assertEqual(self.run_node(0, scaling_mode="analysis_range")["result"][-1].count(
            '"processed_count": 0'), 1)

    def test_analysis_lazy_requests_only_active_linked_analysis(self):
        self.assertIn("scaling_mode", inspect.signature(self.node.run).parameters)
        prompt = {"32": {"inputs": {"analysis_1": ["9", 0], "analysis_3": ["21", 0]}}}
        missing = self.node.check_lazy_status(
            glb_1=self.paths[0], route_report='{"active_branches":[1]}',
            prompt=prompt, unique_id="32", scaling_mode="analysis_range")
        self.assertEqual(missing, ["analysis_1"])

    def test_analysis_mode_rejects_old_size_plan_and_factor_connections(self):
        self.assertIn("scaling_mode", inspect.signature(self.node.run).parameters)
        for options in ({"scale_plan": "{}"}, {"scale_factor_1": 10},
                        {"prompt": {"32": {"inputs": {"scale_plan": ["34", 3]}}},
                         "unique_id": "32"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_node(1, scaling_mode="analysis_range",
                              filename_prefix="conflict/asset", **options)
        self.assertEqual(list(self.output.rglob("*.glb")), [])


if __name__ == "__main__":
    unittest.main()
