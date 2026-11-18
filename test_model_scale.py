"""Final size decisions are based on measured GLB geometry, never an LLM guess."""
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_glb_audit import core_module, fixture, pack_glb

ROOT = Path(__file__).parent


def scale_module(name="model_scale"):
    package = types.ModuleType("_model_scale_test")
    package.__path__ = [str(ROOT)]
    with patch.dict(sys.modules, {"_model_scale_test": package}):
        return importlib.import_module("_model_scale_test." + name)


class ModelScale(unittest.TestCase):
    def setUp(self):
        self.core = core_module()
        self.scale = scale_module()
        doc, binary = fixture(points=[(1, 2, 3), (9, 2, 3), (1, 6, 5)])
        self.data = pack_glb(doc, binary)
        self.measurement = self.core.inspect_glb(self.data)

    def test_longest_edge_enlargement_shrinkage_and_identity(self):
        for target, factor in ((10, 1.25), (2, .25), (.5, .0625), (8, 1)):
            with self.subTest(target=target):
                result = self.scale.calculate_size_scale(self.measurement, {"longest_edge": target})
                self.assertEqual(result["scale_factor"], factor)
                self.assertEqual(result["predicted_dimensions"],
                                 {"width": 8 * factor, "height": 4 * factor, "depth": 2 * factor})
                self.assertEqual(result["limiting_constraints"], ["longest_edge"])

    def test_dimension_envelope_fits_all_limits_and_reports_ties(self):
        result = self.scale.calculate_size_scale(
            self.measurement, {"width": 12, "height": 2, "depth": 1, "longest_edge": 10})
        self.assertEqual(result["scale_factor"], .5)
        self.assertEqual(result["limiting_constraints"], ["height", "depth"])
        self.assertEqual(result["predicted_dimensions"], {"width": 4, "height": 2, "depth": 1})

    def test_axis_switch_changes_height_decision(self):
        for axis, factor in (("Y", .5), ("Z", 1)):
            result = self.scale.calculate_size_scale(
                self.core.inspect_glb(self.data, axis), {"height": 2})
            self.assertEqual(result["scale_factor"], factor)

    def test_zero_extent_can_fit_but_cannot_determine_a_multiplier_alone(self):
        measurement = {"dimensions": {"width": 8, "height": 4, "depth": 0}, "longest_edge": 8}
        result = self.scale.calculate_size_scale(measurement, {"depth": 1, "width": 4})
        self.assertEqual(result["scale_factor"], .5)
        with self.assertRaises(ValueError):
            self.scale.calculate_size_scale(measurement, {"depth": 1})

    def test_invalid_targets_and_extents_are_rejected(self):
        for targets in ({}, {"width": 0}, {"height": -1}, {"height": True},
                        {"depth": float("inf")}, {"longest_edge": float("nan")},
                        {"width": "2"}, {"unknown": 2}):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.scale.calculate_size_scale(self.measurement, targets)
        for extent in (-1, True, float("nan"), float("inf")):
            with self.subTest(extent=extent), self.assertRaises(ValueError):
                self.scale.calculate_size_scale(
                    {"dimensions": {"width": extent, "height": 4, "depth": 2},
                     "longest_edge": 8}, {"height": 2})

    def test_plan_is_recomputed_and_bound_to_actual_bytes(self):
        targets = {"longest_edge": 10}
        item = dict(self.scale.calculate_size_scale(self.measurement, targets),
                    source_sha256=hashlib.sha256(self.data).hexdigest())
        result = self.scale.verify_size_plan_item(self.data, item, targets, "Y", 1.25)
        self.assertEqual(result["scale_factor"], 1.25)
        for changed in (dict(item, source_sha256="0" * 64),
                        dict(item, scale_factor=2),
                        dict(item, predicted_dimensions={"width": 10, "height": 99, "depth": 2.5})):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.scale.verify_size_plan_item(self.data, changed, targets, "Y", 1.25)
        with self.assertRaises(ValueError):
            self.scale.verify_size_plan_item(self.data, item, targets, "Y", 2)

    def test_actual_export_must_match_prediction_and_target_envelope(self):
        targets = {"width": 12, "height": 2}
        decision = self.scale.calculate_size_scale(self.measurement, targets)
        output, _ = self.core.process_glb(self.data, scale_factor=decision["scale_factor"])
        after = self.core.inspect_glb(output)
        self.assertTrue(self.scale.verify_final_size(after, decision, targets))
        for dims in ({"width": 4, "height": 2.01, "depth": 1},
                     {"width": 3, "height": 1.5, "depth": .75}):
            with self.subTest(dims=dims), self.assertRaises(ValueError):
                self.scale.verify_final_size(dict(after, dimensions=dims), decision, targets)


if __name__ == "__main__":
    unittest.main()
