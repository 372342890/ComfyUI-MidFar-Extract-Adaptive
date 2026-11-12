import copy
import itertools
import unittest

from test_five_quality import contracts, source
import five_quality

try:
    from recognition_contract import normalize_recognition, normalize_pair, validate_grouping
except ImportError:
    normalize_recognition = normalize_pair = validate_grouping = None


def grouped():
    raw, _ = contracts()
    raw["grouping_contract"] = "natural-units-v1"
    for i, obj in enumerate(raw["objects"]):
        obj["component_ids"] = [f"c{i}"]
    raw["group_relations"] = [
        {"ids": [a["id"], b["id"]], "relation": "separated" if a["role"] == b["role"]
         else "separate_depth", "evidence": "可见岩基不相接，背景投影不构成岩土连接"}
        for a, b in itertools.combinations(raw["objects"], 2)]
    return raw


class RecognitionContract(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(normalize_pair, "recognition normalization missing")

    def test_numeric_ids_require_unique_unit_alias_and_map_pe_differences(self):
        raw, pe = contracts()
        for i, (obj, entry) in enumerate(zip(raw["objects"], pe["objects"]), 1):
            obj["id"] = i
            entry["id"] = i
            for difference in entry["differences"]:
                difference["other_id"] = int(difference["other_id"][1:])
        normalized, written = normalize_pair(raw, pe)
        self.assertEqual(normalized["objects"][0]["id"], "u1")
        self.assertEqual(written["objects"][0]["differences"][0]["other_id"], "u2")
        self.assertTrue(normalized["_normalization_audit"])
        self.assertEqual(raw["objects"][0]["id"], 1)
        raw["objects"][0]["unit_id"] = "u2"
        with self.assertRaises(ValueError):
            normalize_pair(raw, pe)

    def test_explicit_whole_document_coordinate_conversion_only(self):
        raw, _ = contracts()
        raw["coordinate_space"] = "pixels"
        raw["source_size"] = [256, 256]
        for obj in raw["objects"]:
            obj["bbox"] = [v*256 for v in obj["bbox"]]
            obj["target_point"] = [v*256 for v in obj["target_point"]]
            for mark in obj["landmarks"]:
                mark["point"] = [v*256 for v in mark["point"]]
        result = normalize_recognition(raw, source_size=[256, 256])
        self.assertEqual(result["objects"][0]["bbox"], [.15, .15, .75, .75])
        with self.assertRaises(ValueError):
            normalize_recognition(raw, source_size=[1024, 1024])
        raw, _ = contracts()
        raw["objects"][1]["bbox"][0] = 150
        with self.assertRaises(ValueError):
            normalize_recognition(raw)

    def test_required_groups_are_complete_distinct_and_evidence_bound(self):
        validate_grouping(grouped(), required=True)
        mutations = [
            lambda r: r.pop("grouping_contract"),
            lambda r: r["objects"][1].update(component_ids=["c0"]),
            lambda r: r["group_relations"].pop(),
            lambda r: r["group_relations"][1].update(**r["group_relations"][0]),
            lambda r: r["group_relations"][0].update(relation="connected"),
            lambda r: r["group_relations"][0].update(evidence=""),
            lambda r: r["objects"][0].update(is_single=False),
            lambda r: r["objects"][0].update(connected_to=["a2"]),
        ]
        for mutate in mutations:
            raw = grouped()
            mutate(raw)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                validate_grouping(raw, required=True)

    def test_numeric_group_links_use_the_same_alias_map_as_pe(self):
        raw, pe = contracts()
        raw["objects"][0]["id"] = 1
        pe["objects"][0]["id"] = 1
        for entry in pe["objects"][1:]:
            entry["differences"][0]["other_id"] = 1
        raw["objects"][1]["connected_to"] = [1]
        with self.assertRaisesRegex(ValueError, "连接"):
            normalize_pair(raw, pe)

    def test_rock_edge_policy_cannot_be_used_for_mountains_or_far_ranges(self):
        raw = grouped()
        obj = raw["objects"][2]
        obj.update(boundary_cut=True, completion_policy="close_visible_rock_edge")
        with self.assertRaises(ValueError):
            validate_grouping(raw, required=True)
        obj["kind"] = "rock"
        validate_grouping(raw, required=True)
        obj["role"] = "range"
        with self.assertRaises(ValueError):
            validate_grouping(raw, required=True)

    def test_plan_blocks_connected_groups_and_keeps_normalization_audit(self):
        raw, pe = contracts()
        raw["objects"][0]["connected_to"] = ["a2"]
        with self.assertRaises(ValueError):
            five_quality.compile_plan(raw, pe, source(), "test", 256)
        raw, pe = contracts()
        raw["objects"][0]["id"] = 1
        pe["objects"][0]["id"] = 1
        for entry in pe["objects"][1:]:
            entry["differences"][0]["other_id"] = 1
        plan = five_quality.compile_plan(raw, pe, source(), "test", 256)
        self.assertTrue(plan["normalization_audit"])


if __name__ == "__main__":
    unittest.main()
