import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
from test_five_quality import source
from test_recognition_contract import grouped
from test_visible_native_preview import Tensor


def node_module():
    path = Path(__file__).with_name("recognition_nodes.py")
    if not path.exists():
        return None
    package = types.ModuleType("_recognition_host")
    package.__path__ = [str(path.parent)]
    torch = types.ModuleType("torch")
    torch.from_numpy = Tensor
    spec = importlib.util.spec_from_file_location("_recognition_host.recognition_nodes", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"_recognition_host": package, "torch": torch}):
        spec.loader.exec_module(module)
    return module


class RecognitionNodes(unittest.TestCase):
    def setUp(self):
        self.nodes = node_module()
        self.assertIsNotNone(self.nodes, "visible recognition review missing")

    def context(self, raw=None):
        return self.nodes.BAMidFarRecognitionContext().run(
            Tensor(source()), json.dumps(grouped()) if raw is None else raw)

    def review(self, context):
        binding = json.loads(context)
        return json.dumps({"source_hash": binding["source_hash"],
                           "review_id": binding["review_id"], "recognition": grouped()})

    def test_invalid_raw_is_visible_feedback_and_same_source_is_preserved(self):
        prompt, ref, ctx = self.context('{"objects":[{"id":"cut')
        context = json.loads(ctx)
        self.assertTrue(context["issues"])
        self.assertIn("cut", prompt)
        self.assertTrue(np.array_equal(ref.detach().cpu().numpy(), source()))
        self.assertEqual(context["max_reviews"], 1)

    def test_valid_complete_review_bound_to_reference_is_normalized(self):
        prompt, ref, ctx = self.context()
        recognition, record = self.nodes.BAMidFarRecognitionFinal().run(
            ref, ctx, self.review(ctx))
        self.assertEqual(len(json.loads(recognition)["objects"]), 5)
        self.assertEqual(json.loads(record)["status"], "accepted")
        self.assertIn("natural-units-v1", prompt)

    def test_changed_source_stale_review_invalid_groups_and_truncation_fail(self):
        _, ref, ctx = self.context()
        good = self.review(ctx)
        reviews = [good[:-20], good.replace(json.loads(ctx)["review_id"], "old")]
        invalid = json.loads(good)
        invalid["recognition"]["group_relations"][0]["relation"] = "connected"
        reviews.append(json.dumps(invalid))
        invalid = json.loads(good)
        invalid["recognition"]["objects"][0]["landmarks"].pop()
        reviews.append(json.dumps(invalid))
        for review in reviews:
            with self.subTest(review=review[:50]), self.assertRaises(ValueError):
                self.nodes.BAMidFarRecognitionFinal().run(ref, ctx, review)
        with self.assertRaises(ValueError):
            self.nodes.BAMidFarRecognitionFinal().run(Tensor(np.ones_like(source())), ctx, good)


if __name__ == "__main__":
    unittest.main()
