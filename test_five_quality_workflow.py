"""A dynamic single-upload native graph, with conditional review and retry."""
import json
from pathlib import Path
import unittest

try:
    from five_quality_workflow import build_quality_workflow
except ImportError:
    build_quality_workflow = None


class FiveQualityWorkflow(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(build_quality_workflow, "单入口完整工作流尚未实现")
        self.graph = build_quality_workflow()

    def test_one_upload_five_first_models_five_visible_optional_retries(self):
        nodes = self.graph["nodes"]
        self.assertEqual(sum(n["data"]["type"] == "LoadImage" for n in nodes), 1)
        first = [n for n in nodes if n["data"]["title"].startswith("首轮生成")]
        retry = [n for n in nodes if n["data"]["title"].startswith("失败路重试")]
        self.assertEqual(len(first), 5)
        self.assertEqual(len(retry), 5)
        model_ids = {n["id"] for n in first}
        self.assertFalse(any(e["source"] in model_ids and e["target"] in model_ids
                             for e in self.graph["edges"]))
        serialized = json.dumps(self.graph, ensure_ascii=False)
        for legacy in ("026", "derived_source_crops", "source_localizations", "previewImage"):
            self.assertNotIn(legacy, serialized)

    def test_every_edge_has_matching_real_named_ports_and_no_cycle(self):
        nodes = {n["id"]: n for n in self.graph["nodes"]}
        seen, visiting = set(), set()
        incoming = {}
        for edge in self.graph["edges"]:
            a, b = nodes[edge["source"]], nodes[edge["target"]]
            output = next(p for p in a["data"]["outputSlots"] if p["name"] == edge["sourceHandle"])
            inp = next(p for p in b["data"]["inputs"] if p["name"] == edge["targetHandle"])
            self.assertEqual(output["type"], inp["type"])
            self.assertNotIn("value", inp)
            incoming.setdefault(b["id"], []).append(a["id"])
        def visit(identifier):
            self.assertNotIn(identifier, visiting, "工作流有循环依赖")
            if identifier in seen:
                return
            visiting.add(identifier)
            for parent in incoming.get(identifier, []):
                visit(parent)
            visiting.remove(identifier)
            seen.add(identifier)
        for identifier in nodes:
            visit(identifier)

    def test_all_new_nodes_registered_and_force_input_ports_connected(self):
        init = Path(__file__).with_name("__init__.py").read_text()
        linked = {(e["target"], e["targetHandle"]) for e in self.graph["edges"]}
        for node in self.graph["nodes"]:
            if node["data"]["type"].startswith("BAMidFar"):
                self.assertIn('"'+node["data"]["type"]+'":', init)
                for pin in node["data"]["inputs"]:
                    if pin.get("format") == "slot":
                        self.assertIn((node["id"], pin["name"]), linked)

    def test_text_budget_and_expected_quality_rules_visible(self):
        models = [n for n in self.graph["nodes"] if n["data"]["type"] == "BALLMText"]
        self.assertEqual(len(models), 4)
        for node in models:
            self.assertGreaterEqual(node["data"]["extra"]["model"]["max_tokens"], 12000)
            self.assertGreaterEqual(node["data"]["extra"]["modelConfig"]["max_tokens"]["value"], 12000)
        text = json.dumps(self.graph, ensure_ascii=False)
        self.assertIn("三个近景", text)
        self.assertIn("两个远景", text)
        self.assertIn("至少三", text)
        self.assertIn("不拔尖", text)


if __name__ == "__main__":
    unittest.main()
