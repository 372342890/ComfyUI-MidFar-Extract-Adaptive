"""Real target counts must not fabricate a third mountain."""
import json
import unittest

from test_visible_batch import image
from test_visible_pe_split import recognition
from visible_pipeline import split_prompts


class AdaptiveSplit(unittest.TestCase):
    def call(self, count=2, raw=None, pe=None):
        if raw is None:
            raw = recognition()
            raw["objects"] = raw["objects"][:count]
        if pe is None:
            pe = {"objects": [{"id": t["id"], "prompt": "独立提取 " + t["id"]}
                              for t in raw["objects"]]}
        return split_prompts(json.dumps(raw), json.dumps(pe), image(),
                             "adaptive/test", "any")

    def test_one_and_two_targets_keep_eight_slots_with_empty_trailing_branches(self):
        for count in (1, 2):
            with self.subTest(count=count):
                out = self.call(count)
                self.assertEqual(len(out), 8)
                self.assertTrue(all(out[i] for i in range(count)))
                self.assertEqual(list(out[count:3]), [""] * (3 - count))
                self.assertEqual(list(out[4 + count:7]), [""] * (3 - count))
                record = json.loads(out[-1])
                self.assertEqual(record["target_count"], count)
                self.assertEqual(len(record["recognized_targets"]), count)
                self.assertEqual(len(record["execution_prompts"]), count)
                self.assertEqual(record["active_branches"], list(range(1, count + 1)))
                self.assertEqual(record["skipped_branches"], list(range(count + 1, 4)))

    def test_zero_and_four_targets_are_rejected(self):
        for count in (0, 4):
            raw = recognition()
            if count == 0:
                raw["objects"] = []
            else:
                raw["objects"].append(dict(raw["objects"][0], id="asset4", unit_id="unit4"))
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.call(raw=raw)

    def test_two_targets_cannot_be_replaced_or_padded_by_pe(self):
        for ids in (("asset1", "other"), ("asset1", "asset2", "asset3")):
            pe = {"objects": [{"id": i, "prompt": "提取"} for i in ids]}
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.call(pe=pe)

    def test_case_cannot_disguise_repeated_unit_with_two_targets(self):
        raw = recognition()
        raw["objects"] = raw["objects"][:2]
        raw["objects"][1]["unit_id"] = " UNIT1 "
        with self.assertRaisesRegex(ValueError, "重复"):
            self.call(raw=raw)

    def test_explicit_disconnected_stones_are_rejected_even_if_flags_claim_single(self):
        raw = recognition()
        raw["objects"] = raw["objects"][:1]
        raw["objects"][0]["connected_parts"] = ["各石块独立，无岩土连接"]
        with self.assertRaisesRegex(ValueError, "连接|连通"):
            self.call(raw=raw)

    def test_exclusion_of_unrelated_neighbors_does_not_claim_disconnected_target(self):
        raw = recognition()
        raw["objects"] = raw["objects"][:1]
        raw["objects"][0]["connected_parts"] = ["共有岩基连续，无关邻山连接不属于本组"]
        self.assertEqual(json.loads(self.call(raw=raw)[-1])["target_count"], 1)


if __name__ == "__main__":
    unittest.main()
