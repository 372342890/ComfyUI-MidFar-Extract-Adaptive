"""All generation edges are gated; the exported graph contains extraction only."""
import importlib.util
import unittest


class AdaptiveWorkflow(unittest.TestCase):
    def graph(self):
        self.assertIsNotNone(importlib.util.find_spec("adaptive_workflow"),
                             "Adaptive extraction workflow is missing.")
        from adaptive_workflow import build_workflow
        return build_workflow("026.png", "midfar-adaptive/asset", "any")

    def test_extraction_graph_uses_distinct_adaptive_keys(self):
        graph = self.graph()
        kinds = [n["data"]["type"] for n in graph["nodes"]]
        self.assertEqual(len(kinds), 11)
        self.assertEqual(kinds.count("LoadImage"), 1)
        self.assertEqual(kinds.count("BALLMText"), 2)
        self.assertEqual(kinds.count("BALLMImage"), 3)
        self.assertEqual(kinds.count("BAMidFarActivePrompt"), 3)
        self.assertEqual(kinds.count("BAMidFarAdaptivePE"), 1)
        self.assertEqual(kinds.count("BAMidFarAdaptivePNG"), 1)
        self.assertFalse(any("3D" in k or "GLB" in k or "Face" in k for k in kinds))

    def test_every_model_prompt_is_gated_and_original_image_is_direct(self):
        graph = self.graph()
        lookup = {n["id"]: n for n in graph["nodes"]}
        edges = graph["edges"]
        source = next(n for n in graph["nodes"] if n["data"]["type"] == "LoadImage")
        splitter = next(n for n in graph["nodes"]
                        if n["data"]["type"] == "BAMidFarAdaptivePE")
        models = [n for n in graph["nodes"] if n["data"]["type"] == "BALLMImage"]
        for i, model in enumerate(models, 1):
            prompt = next(e for e in edges if e["target"] == model["id"]
                          and e["targetHandle"] == "prompt")
            gate = lookup[prompt["source"]]
            self.assertEqual(gate["data"]["type"], "BAMidFarActivePrompt")
            upstream = next(e for e in edges if e["target"] == gate["id"])
            self.assertEqual((upstream["source"], upstream["sourceHandle"]),
                             (splitter["id"], f"prompt_{i}"))
        for n in [n for n in graph["nodes"] if n["data"]["type"] in ("BALLMText", "BALLMImage")]:
            self.assertTrue(any(e["source"] == source["id"] and e["target"] == n["id"]
                                and e["targetHandle"] == "image" for e in edges))
        for e in edges:
            output = next(s for s in lookup[e["source"]]["data"]["outputSlots"]
                          if s["name"] == e["sourceHandle"])
            input_ = next(s for s in lookup[e["target"]]["data"]["inputs"]
                          if s["name"] == e["targetHandle"])
            self.assertEqual(output["type"], input_["type"])
            self.assertEqual(e["data"]["type"], output["type"])

    def test_prompts_use_actual_count_and_image_truth_over_rounding_bias(self):
        graph = self.graph()
        texts = []
        for node in graph["nodes"]:
            if node["data"]["type"] in ("BALLMText", "BALLMImage"):
                prompt = next(s["value"] for s in node["data"]["inputs"]
                              if s["name"] == "user_prompt")
                self.assertNotIn("恰好三", prompt)
                self.assertNotIn("{KEY_COLOR}", prompt)
                texts.append(prompt)
        combined = "\n".join(texts)
        self.assertIn("1–3", combined)
        self.assertIn("原图", combined)
        self.assertIn("尖峰", combined)
        self.assertIn("岩阶", combined)
        self.assertIn("连接段", combined)
        self.assertIn("底色不限", combined)
        self.assertIn("镜像", combined)

    def test_native_resolution_matches_landscape_prompt(self):
        for node in self.graph()["nodes"]:
            if node["data"]["type"] == "BALLMImage":
                size = node["data"]["extra"]["modelConfig"]["size"]
                self.assertTrue(size["switch"])
                self.assertEqual(size["value"], "2016x1344")


if __name__ == "__main__":
    unittest.main()
