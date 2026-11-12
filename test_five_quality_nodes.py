"""Host lazy contract: request only failed retries; save only accepted five."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_five_quality import contracts, source, assets, report
from test_visible_native_preview import Tensor

ROOT = Path(__file__).parent


def node_module():
    path = ROOT / "five_quality_nodes.py"
    if not path.exists():
        return None
    package = types.ModuleType("_five_quality_host")
    package.__path__ = [str(ROOT)]
    torch = types.ModuleType("torch")
    torch.from_numpy = Tensor
    spec = importlib.util.spec_from_file_location("_five_quality_host.five_quality_nodes", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"_five_quality_host": package, "torch": torch}):
        spec.loader.exec_module(module)
    return module


class FiveQualityNodes(unittest.TestCase):
    def setUp(self):
        self.nodes = node_module()
        self.assertIsNotNone(self.nodes, "质量节点尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folders = types.ModuleType("folder_paths")
        folders.get_output_directory = lambda: self.temp.name
        self.host = patch.dict(sys.modules, {"folder_paths": folders})
        self.host.start()
        self.addCleanup(self.host.stop)
        raw, pe = contracts()
        self.plan = self.nodes.compile_plan(raw, pe, source(), "test/five", canvas_size=256)
        self.images = assets()
        self.inspection = self.nodes.inspect_images(self.plan, source(), self.images)[2]
        self.review = report(self.plan, self.inspection)
        self.state = self.nodes.evaluate_review(self.plan, self.inspection, self.review)

    def test_selector_requests_only_affected_retry_images(self):
        node = self.nodes.BAMidFarFiveSelect()
        values = {f"image_{i+1}": Tensor(x) for i, x in enumerate(self.images)}
        self.assertEqual(node.check_lazy_status(state=json.dumps(self.state), **values), [])
        failed = self.nodes.evaluate_review(
            self.plan, self.inspection, report(self.plan, self.inspection, failed=(2, 4)))
        self.assertEqual(node.check_lazy_status(state=json.dumps(failed), **values),
                         ["retry_2", "retry_4"])
        values.update(retry_2=Tensor(self.images[1]), retry_4=Tensor(self.images[3]))
        selected = node.run(state=json.dumps(failed), **values)
        self.assertIs(selected[1], values["retry_2"])
        self.assertIs(selected[0], values["image_1"])

    def test_final_node_skips_second_review_when_first_round_passed(self):
        node = self.nodes.BAMidFarFiveFinal()
        args = dict(plan=json.dumps(self.plan), first_inspection=json.dumps(self.inspection),
                    first_review=json.dumps(self.review), state=json.dumps(self.state))
        self.assertEqual(node.check_lazy_status(**args),
                         [f"image_{i}" for i in range(1, 6)])
        args.update({f"image_{i+1}": Tensor(x) for i, x in enumerate(self.images)})
        self.assertEqual(node.check_lazy_status(**args), [])
        result = node.run(**args)
        self.assertEqual(len(result), 6)
        self.assertEqual(json.loads(result[-1])["png_count"], 5)
        self.assertEqual(len(list(Path(self.temp.name).rglob("*.png"))), 5)

    def test_final_node_requests_second_inspection_only_after_failure(self):
        node = self.nodes.BAMidFarFiveFinal()
        failed_review = report(self.plan, self.inspection, failed=(3,))
        failed = self.nodes.evaluate_review(self.plan, self.inspection, failed_review)
        args = dict(plan=json.dumps(self.plan), first_inspection=json.dumps(self.inspection),
                    first_review=json.dumps(failed_review), state=json.dumps(failed))
        requested = node.check_lazy_status(**args)
        self.assertIn("final_inspection", requested)
        self.assertIn("final_review", requested)
        args.update({f"image_{i+1}": Tensor(x) for i, x in enumerate(self.images)})
        args.update(final_inspection=json.dumps(self.inspection),
                    final_review=json.dumps(failed_review))
        with self.assertRaises(ValueError):
            node.run(**args)
        self.assertEqual(list(Path(self.temp.name).rglob("*.png")), [])

    def test_malformed_state_cannot_request_or_select_unrelated_routes(self):
        node = self.nodes.BAMidFarFiveSelect()
        for value in ([0], [6], [2, 2], ["2"]):
            state = dict(self.state, failed_branches=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                node.check_lazy_status(state=json.dumps(state))

    def test_acceptance_state_cannot_hide_a_failed_first_review(self):
        node = self.nodes.BAMidFarFiveFinal()
        args = dict(plan=json.dumps(self.plan), first_inspection=json.dumps(self.inspection),
                    first_review=json.dumps(report(self.plan, self.inspection, failed=(1,))),
                    state=json.dumps(self.state))
        args.update({f"image_{i+1}": Tensor(x) for i, x in enumerate(self.images)})
        with self.assertRaises(ValueError):
            node.run(**args)
        self.assertEqual(list(Path(self.temp.name).rglob("*.png")), [])

    def test_write_failure_leaves_no_partial_five_output(self):
        node = self.nodes.BAMidFarFiveFinal()
        args = dict(plan=json.dumps(self.plan), first_inspection=json.dumps(self.inspection),
                    first_review=json.dumps(self.review), state=json.dumps(self.state))
        args.update({f"image_{i+1}": Tensor(x) for i, x in enumerate(self.images)})
        with patch.object(self.nodes.Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                node.run(**args)
        self.assertEqual(list(Path(self.temp.name).rglob("*.png")), [])

    def execute_graph(self, fail_first=(), fail_second=()):
        from five_quality_workflow import build_quality_workflow
        from test_recognition_contract import grouped
        from test_recognition_nodes import node_module as recognition_module
        review_nodes = recognition_module()
        graph = build_quality_workflow(filename_prefix="test/native")
        nodes = {n["id"]: n for n in graph["nodes"]}
        edges = {(e["target"], e["targetHandle"]): (e["source"], e["sourceHandle"])
                 for e in graph["edges"]}
        _, pe = contracts()
        raw = grouped()
        cache, called = {}, []
        def execute(identifier):
            if identifier in cache:
                return cache[identifier]
            called.append(identifier)
            node = nodes[identifier]
            kind = node["data"]["type"]
            def input_value(pin):
                edge = edges.get((identifier, pin))
                if edge:
                    return execute(edge[0])[edge[1]]
                inp = next(p for p in node["data"]["inputs"] if p["name"] == pin)
                return inp.get("value")
            if kind == "LoadImage":
                values = (Tensor(source()), None)
            elif kind == "BALLMText":
                if identifier == "2":
                    values = (json.dumps(raw),)
                elif identifier == "3":
                    input_value("text")
                    values = (json.dumps(pe),)
                elif identifier == "38":
                    input_value("image")
                    binding = json.loads(execute("37")["context"])
                    values = (json.dumps({"source_hash": binding["source_hash"],
                                          "review_id": binding["review_id"],
                                          "recognition": raw}),)
                else:
                    input_value("image")
                    context = json.loads(input_value("text").split("本轮上下文：", 1)[1])
                    plan = json.loads(execute("4")["plan"])
                    failure = fail_first if identifier == "31" else fail_second
                    values = (json.dumps(report(plan, context["numeric"], failure)),)
            elif kind == "BALLMImage":
                input_value("image")
                prompt = input_value("prompt")
                self.assertTrue(prompt, "空路图片模型不应执行")
                i = int(identifier)-(15 if int(identifier) <= 19 else 25)
                image = self.images[i].copy()
                if int(identifier) >= 25:
                    image[:, 140, 128] = .75
                values = (Tensor(image),)
            elif kind == "BAMidFarActivePrompt":
                values = (input_value("prompt"),)
            else:
                module = review_nodes if kind.startswith("BAMidFarRecognition") else self.nodes
                instance = getattr(module, kind)()
                types_ = instance.INPUT_TYPES()["required"]
                kwargs = {pin: input_value(pin) for pin, definition in types_.items()
                          if len(definition) < 2 or not definition[1].get("lazy")}
                if hasattr(instance, "check_lazy_status"):
                    for _ in range(3):
                        missing = instance.check_lazy_status(**kwargs)
                        if not missing:
                            break
                        kwargs.update({pin: input_value(pin) for pin in missing})
                    self.assertEqual(instance.check_lazy_status(**kwargs), [])
                values = instance.run(**kwargs)
            cache[identifier] = dict(zip(
                (p["name"] for p in node["data"]["outputSlots"]), values))
            return cache[identifier]
        original = self.nodes.compile_plan
        with patch.object(self.nodes, "compile_plan",
                          side_effect=lambda r, p, ref, prefix: original(
                              r, p, ref, prefix, canvas_size=256)):
            result = execute("36")
        return result, called

    def test_complete_graph_first_pass_never_executes_retry_or_second_review(self):
        result, called = self.execute_graph()
        self.assertEqual(json.loads(result["quality_record"])["png_count"], 5)
        self.assertEqual(sum(str(i) in called for i in range(15, 20)), 5)
        self.assertFalse(set(str(i) for i in range(20, 30)) & set(called))
        self.assertNotIn("34", called)
        self.assertNotIn("35", called)

    def test_complete_graph_one_failure_executes_only_that_retry_and_full_final_review(self):
        result, called = self.execute_graph(fail_first=(2,))
        record = json.loads(result["quality_record"])
        self.assertEqual(record["retried_branches"], [2])
        self.assertEqual([i for i in range(25, 30) if str(i) in called], [26])
        self.assertIn("34", called)
        self.assertIn("35", called)
        self.assertEqual(len(list(Path(self.temp.name).rglob("*.png"))), 5)

    def test_complete_graph_second_failure_saves_nothing(self):
        with self.assertRaises(ValueError):
            self.execute_graph(fail_first=(2,), fail_second=(2,))
        self.assertEqual(list(Path(self.temp.name).rglob("*.png")), [])


if __name__ == "__main__":
    unittest.main()
