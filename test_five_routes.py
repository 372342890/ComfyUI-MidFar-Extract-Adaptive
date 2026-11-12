"""Five identities and all ten pairs must survive native graph composition."""
import importlib.util
import json
from pathlib import Path
import unittest

from test_visible_batch import image
from test_visible_pe_split import recognition

if importlib.util.find_spec("five_routes"):
    from five_routes import split_five, build_execution, build_planning, CHECK_GROUPS
else:
    split_five = build_execution = build_planning = None
    CHECK_GROUPS = ()


def five():
    template = recognition()["objects"][0]
    targets = []
    for i in range(1, 6):
        targets.append(dict(template, id=f"asset{i}", unit_id=f"unit{i}",
                            location=f"原图不同位置{i}", appearance=f"独特峰形{i}",
                            role="mountain" if i <= 3 else "range",
                            layer="midground" if i <= 3 else "background"))
    return {"objects": targets}


class FiveRoutes(unittest.TestCase):
    def call(self, raw=None):
        self.assertIsNotNone(split_five, "Five-route identity validation is missing")
        raw = five() if raw is None else raw
        pe = {"objects": [{"id": t["id"], "prompt": f"只提取{t['id']}，保留原图。"}
                          for t in raw["objects"]]}
        return split_five(json.dumps(raw), json.dumps(pe), image(), "five/test", "any")

    def test_five_prompts_exclude_all_other_four_targets(self):
        record = self.call()
        self.assertEqual(len(record["execution_prompts"]), 5)
        for i, prompt in enumerate(record["execution_prompts"], 1):
            self.assertTrue(all(f"id=asset{j}" in prompt for j in range(1, 6)))
        self.assertEqual(record["mountain_count"], 3)
        self.assertEqual(record["range_count"], 2)
        self.assertEqual(record["pair_count"], 10)

    def test_same_natural_unit_cannot_be_a_mountain_and_a_range(self):
        raw = five()
        raw["objects"][3]["unit_id"] = " UNIT1 "
        with self.assertRaisesRegex(ValueError, "重复|单元"):
            self.call(raw)

    def test_range_must_actually_be_background(self):
        raw = five()
        raw["objects"][3]["layer"] = "midground"
        with self.assertRaisesRegex(ValueError, "远景"):
            self.call(raw)

    def test_distant_mountain_cannot_fill_a_near_mountain_slot(self):
        raw = five()
        raw["objects"][2]["layer"] = "background"
        with self.assertRaisesRegex(ValueError, "近景"):
            self.call(raw)

    def test_execution_rejects_old_record_with_distant_third_mountain(self):
        record = self.call()
        record["recognized_targets"][2]["layer"] = "background"
        with self.assertRaisesRegex(ValueError, "近景"):
            build_execution(record)

    def test_missing_mountain_keeps_range_slots_four_and_five(self):
        raw = five()
        raw["objects"].pop(2)
        record = self.call(raw)
        self.assertEqual(record["active_branches"], [1, 2, 4, 5])
        self.assertEqual(record["skipped_branches"], [3])
        self.assertEqual(record["pair_count"], 6)
        self.assertIn("requirement_satisfied", record)
        self.assertFalse(record["requirement_satisfied"])
        self.assertEqual(record["required_counts"], {"near_mountains": 3, "far_ranges": 2})
        self.assertEqual(record["missing_counts"], {"near_mountains": 1, "far_ranges": 0})

    def test_complete_record_reports_three_near_and_two_far(self):
        record = self.call()
        self.assertIn("requirement_satisfied", record)
        self.assertTrue(record["requirement_satisfied"])
        self.assertEqual(record["actual_counts"], {"near_mountains": 3, "far_ranges": 2})

    def test_graph_has_five_independent_models_and_all_ten_checks(self):
        record = self.call()
        self.assertIsNotNone(build_execution)
        graph = build_execution(record)
        models = [n for n in graph["nodes"] if n["data"]["title"].startswith("五路生成")]
        self.assertEqual(len(models), 5)
        model_ids = {n["id"] for n in models}
        self.assertFalse(any(e["source"] in model_ids and e["target"] in model_ids
                             for e in graph["edges"]))
        pairs = {tuple(sorted((a, b))) for group in CHECK_GROUPS
                 for a in group for b in group if a < b}
        self.assertEqual(pairs, {(a, b) for a in range(1, 6) for b in range(a + 1, 6)})
        self.assertEqual(len([n for n in graph["nodes"]
                              if n["data"]["type"] == "BAMidFarAdaptivePNG"]), 4)
        self.assertEqual(graph["name"], "ba-clipboard")
        self.assertFalse(any("3D" in n["data"]["type"] for n in graph["nodes"]))

    def test_planning_has_one_recognition_and_one_pe_with_no_image_generation(self):
        self.assertIsNotNone(build_planning)
        graph = build_planning()
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 3)
        text = json.dumps(graph, ensure_ascii=False)
        self.assertIn("三个山体", text)
        self.assertIn("两个远景山脉", text)

    def test_collector_force_inputs_are_connected_to_native_string_nodes(self):
        graph = build_execution(self.call())
        linked = {(e["target"], e["targetHandle"]) for e in graph["edges"]}
        for node in graph["nodes"]:
            if node["data"]["type"] == "BAMidFarAdaptivePNG":
                for field in ("key_color", "prefix_1", "prefix_2", "prefix_3"):
                    self.assertIn((node["id"], field), linked,
                                  "ByteArtist ignores literals in forceInput sockets")

    def test_localization_feeds_only_the_selected_model_a_current_source_crop(self):
        record = self.call()
        record["source_localizations"] = {"asset2": [.6, .1, .95, .35]}
        graph = build_execution(record)
        crops = [n for n in graph["nodes"] if n["data"]["type"] == "BAMidFarSelectedAsset"]
        self.assertEqual(len(crops), 1)
        crop_id = crops[0]["id"]
        self.assertTrue(any(e["source"] == "1" and e["target"] == crop_id
                            for e in graph["edges"]))
        self.assertTrue(any(e["source"] == crop_id and e["target"] == "3" and
                            e["sourceHandle"] == "reference_crop" for e in graph["edges"]))

    def test_near_rock_edge_completion_preserves_source_cut_flag(self):
        raw = five()
        raw["objects"][2].update(
            kind="rock", boundary_cut=True,
            completion_policy="close_visible_rock_edge")
        record = self.call(raw)
        self.assertTrue(record["requirement_satisfied"])
        self.assertTrue(record["recognized_targets"][2]["boundary_cut"])
        self.assertIn("边缘", record["execution_prompts"][2])

    def test_cut_mountain_cannot_use_rock_edge_policy(self):
        raw = five()
        raw["objects"][2].update(
            boundary_cut=True, completion_policy="close_visible_rock_edge")
        with self.assertRaisesRegex(ValueError, "完整|边缘"):
            self.call(raw)

    def test_derived_crop_uses_loader_without_falsifying_selected_asset_contract(self):
        raw = five()
        raw["objects"][2].update(
            kind="rock", boundary_cut=True,
            completion_policy="close_visible_rock_edge")
        record = self.call(raw)
        record["derived_source_crops"] = {"asset3": "026_right_front_rock.png"}
        graph = build_execution(record)
        loaders = [n for n in graph["nodes"] if n["data"]["type"] == "LoadImage"]
        derived = next(n for n in loaders if n["id"] != "1")
        self.assertEqual(derived["data"]["inputs"][0]["value"], "026_right_front_rock.png")
        self.assertTrue(any(e["source"] == derived["id"] and e["target"] == "4"
                            for e in graph["edges"]))
        self.assertFalse(any(n["data"]["type"] == "BAMidFarSelectedAsset"
                             for n in graph["nodes"]))


if __name__ == "__main__":
    unittest.main()
