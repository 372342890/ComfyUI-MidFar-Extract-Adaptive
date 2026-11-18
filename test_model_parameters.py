"""Independent face and scale recommendations, including unreliable LLM output."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent


def load_module(name):
    path = ROOT / f"{name}.py"
    if not path.exists():
        return None
    package = types.ModuleType("_parameter_test")
    package.__path__ = [str(ROOT)]
    spec = importlib.util.spec_from_file_location(f"_parameter_test.{name}", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"_parameter_test": package}):
        spec.loader.exec_module(module)
    return module


class ModelParameters(unittest.TestCase):
    def setUp(self):
        self.core = load_module("model_parameters")
        self.assertIsNotNone(self.core, "Independent face/scale analysis parser is missing")

    def test_faces_are_read_by_key_even_when_scale_is_first(self):
        self.assertEqual(self.core.parse_face_count(
            '{"scale_factor":180,"scale_reason":"2个主峰","faces":4300}'), 4300)
        self.assertEqual(self.core.parse_face_count('{"scale_factor":200}'), 4000)
        self.assertEqual(self.core.parse_face_count('{"faces":5200}'), 5000)
        self.assertEqual(self.core.parse_face_count('{"faces":2800}'), 3000)

    def test_legacy_faces_and_fenced_json(self):
        self.assertEqual(self.core.parse_face_count("4200"), 4200)
        text = '```json\n{"faces":3900,"scale_factor":170}\n```'
        self.assertEqual(self.core.parse_face_count(text), 3900)
        self.assertEqual(self.core.scale_decision(text)["scale_factor"], 170)

    def test_range_and_midpoint_fallback_are_explicit(self):
        for value in (100, 200, 300):
            decision = self.core.scale_decision(
                '{"faces":4100,"scale_factor":%s,"scale_reason":"宽厚山群"}' % value)
            self.assertEqual(decision["scale_factor"], value)
            self.assertEqual(decision["source"], "vision")
            self.assertEqual(decision["scale_range"], [100, 300])
            self.assertEqual(decision["reason"], "宽厚山群")
        for text in (None, "", "4000", "{bad", "{}", '{"scale_factor":"200"}',
                     '{"scale_factor":true}', '{"scale_factor":-1}',
                     '{"scale_factor":0}', '{"scale_factor":NaN}',
                     '{"scale_factor":Infinity}'):
            with self.subTest(text=text):
                result = self.core.scale_decision(text)
                self.assertEqual(result["scale_factor"], 200)
                self.assertEqual(result["source"], "fallback")
                self.assertTrue(result["reason"])
        self.assertEqual(self.core.scale_decision(None, 120, 180)["scale_factor"], 150)

    def test_outside_positive_recommendation_clips_and_records_original(self):
        for value, expected in ((50, 100), (450, 300)):
            result = self.core.scale_decision('{"scale_factor":%s}' % value)
            self.assertEqual(result["scale_factor"], expected)
            self.assertEqual(result["recommended_scale_factor"], value)
            self.assertEqual(result["source"], "clamped")

    def test_ambiguous_json_and_noninteger_faces_do_not_use_other_numbers(self):
        for text in ('{"faces":4100,"faces":4400,"scale_factor":100}',
                     '{"faces":true,"scale_factor":300}',
                     '{"faces":NaN}', '{"faces":4300.1}',
                     '推荐200倍，4000面', '{"scale_factor":100}'):
            with self.subTest(text=text):
                self.assertEqual(self.core.parse_face_count(text), 4000)
        self.assertEqual(self.core.scale_decision(
            '{"scale_factor":100,"scale_factor":300}')["source"], "fallback")

    def test_invalid_range_is_configuration_error(self):
        for bounds in ((300, 100), (0, 300), (-1, 300), (100, float("inf")),
                       (True, 300), ("100", 300)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                self.core.scale_decision("{}", *bounds)

    def test_face_node_replaces_parser_without_extra_outputs(self):
        module = load_module("model_parameter_nodes")
        self.assertIsNotNone(module)
        node = module.BAMidFarFaceCountFromAnalysis()
        self.assertEqual(node.RETURN_TYPES, ("INT",))
        self.assertEqual(node.RETURN_NAMES, ("faces",))
        self.assertEqual(node.convert('{"scale_factor":100,"faces":4500}'), (4500,))
        self.assertEqual(node.convert("invalid", default_faces=4100), (4100,))


if __name__ == "__main__":
    unittest.main()
