"""Host contract: skip inactive generation and never save placeholder images."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from PIL import Image
import numpy as np

from test_visible_batch import image
from test_visible_native_preview import Tensor

ROOT = Path(__file__).parent


class ExecutionBlocker:
    def __init__(self, message):
        self.message = message


def node_module():
    path = ROOT / "adaptive_nodes.py"
    if not path.is_file():
        return None
    package = types.ModuleType("_adaptive_test")
    package.__path__ = [str(ROOT)]
    torch = types.ModuleType("torch")
    torch.from_numpy = Tensor
    spec = importlib.util.spec_from_file_location("_adaptive_test.adaptive_nodes", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"_adaptive_test": package, "torch": torch}):
        spec.loader.exec_module(module)
    return module


class AdaptiveNodes(unittest.TestCase):
    def setUp(self):
        self.nodes = node_module()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        folders = types.ModuleType("folder_paths")
        folders.get_output_directory = lambda: self.directory.name
        graph = types.ModuleType("comfy_execution.graph")
        graph.ExecutionBlocker = ExecutionBlocker
        execution = types.ModuleType("comfy_execution")
        execution.__path__ = []
        self.host = patch.dict(sys.modules, {"folder_paths": folders,
                                           "comfy_execution": execution,
                                           "comfy_execution.graph": graph})
        self.host.start()
        self.addCleanup(self.host.stop)

    def node(self, name):
        cls = getattr(self.nodes, name, None)
        self.assertIsNotNone(cls, "Adaptive node is missing: " + name)
        return cls()

    def test_active_prompt_is_unchanged_and_empty_prompt_blocks_model(self):
        node = self.node("BAMidFarActivePrompt")
        self.assertEqual(node.run("指定山体"), ("指定山体",))
        self.assertIsInstance(node.run(" \n")[0], ExecutionBlocker)
        with self.assertRaises(ValueError):
            node.run(None)

    def test_lazy_collector_requests_only_missing_active_images(self):
        node = self.node("BAMidFarAdaptivePNG")
        types_ = node.INPUT_TYPES()["required"]
        self.assertTrue(all(types_[f"image_{i}"][1]["lazy"] for i in (1, 2, 3)))
        args = dict(key_color="any", prefix_1="test/one", prefix_2="test/two", prefix_3="")
        self.assertEqual(node.check_lazy_status(**args), ["image_1", "image_2"])
        self.assertEqual(node.check_lazy_status(image_1=Tensor(image()), **args), ["image_2"])
        self.assertEqual(node.check_lazy_status(
            image_1=Tensor(image()), image_2=Tensor(image(1)), **args), [])

    def test_one_and_two_images_save_only_real_outputs_and_block_unused_slots(self):
        node = self.node("BAMidFarAdaptivePNG")
        for count in (1, 2):
            prefixes = [f"test/count{count}/image{i}" if i <= count else "" for i in (1, 2, 3)]
            tensors = [Tensor(image(i)) if i < count else None for i in range(3)]
            result = node.run(*tensors, "any", *prefixes)
            self.assertEqual(len(result), 4)
            for i in range(count):
                np.testing.assert_array_equal(result[i].numpy(), image(i))
            self.assertTrue(all(isinstance(v, ExecutionBlocker) for v in result[count:3]))
            record = json.loads(result[-1])
            self.assertEqual(record["png_count"], count)
            self.assertEqual(record["skipped_branches"], list(range(count + 1, 4)))
            paths = list((Path(self.directory.name) / "test" / f"count{count}").rglob("*.png"))
            self.assertEqual(len(paths), count)
            for path in paths:
                with Image.open(path) as saved:
                    self.assertEqual(saved.mode, "RGB")

    def test_duplicate_paths_and_gaps_fail_before_saving(self):
        node = self.node("BAMidFarAdaptivePNG")
        for prefixes in (("test/same", "test/same", ""), ("test/one", "", "test/three"),
                         ("", "", ""), ("test/one", None, "")):
            with self.subTest(prefixes=prefixes), self.assertRaises(ValueError):
                node.run(Tensor(image()), Tensor(image(1)), Tensor(image(2)),
                         "any", *prefixes)
        self.assertEqual(list(Path(self.directory.name).rglob("*.png")), [])

    def test_missing_active_image_cannot_silently_be_skipped(self):
        node = self.node("BAMidFarAdaptivePNG")
        with self.assertRaises(ValueError):
            node.run(Tensor(image()), None, None, "any", "test/one", "test/two", "")

    def test_new_pe_class_retains_eight_slot_contract(self):
        node = self.node("BAMidFarAdaptivePE")
        self.assertEqual(len(node.RETURN_TYPES), 8)


if __name__ == "__main__":
    unittest.main()
