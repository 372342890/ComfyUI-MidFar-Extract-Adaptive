"""Source identity, shape contracts and final five-output acceptance."""
import copy
import itertools
import unittest

import cv2
import numpy as np

try:
    import five_quality as quality
except ImportError:
    quality = None


def source():
    return np.random.default_rng(12).random((1, 256, 256, 3), dtype=np.float32)


def contracts():
    objects, prompts = [], []
    for i in range(5):
        identifier = f"a{i+1}"
        landmarks = [
            {"name": f"锚点{j}", "shape": f"本山独有结构{i}-{j}宽厚转折",
             "point": [.2 + .1*j, .3]}
            for j in range(3)]
        objects.append({
            "id": identifier, "unit_id": f"u{i+1}", "name": f"自然山体{i}",
            "role": "mountain" if i < 3 else "range",
            "layer": "midground" if i < 3 else "background", "kind": "mountain",
            "location": f"唯一位置{i}", "appearance": f"宽肩与阶梯{i}",
            "occlusion": "无", "is_single": True, "suitable": True,
            "boundary_cut": False, "connected_parts": ["左肩岩体与谷下岩基相连"],
            "excluded_objects": ["其他深度邻山"], "bbox": [.15, .15, .75, .75],
            "target_point": [.4, .4], "landmarks": landmarks,
            "contour_fingerprint": ["左端宽脚", f"高点{i}", "低鞍部", "右肩岩阶"],
            "rhythm": f"高低疏密及宽厚转折{i}",
        })
        prompts.append({
            "id": identifier, "prompt": f"完整提取{identifier}保留该山体原图结构",
            "preserved_landmarks": [p["name"] for p in landmarks],
            "protected_connections": ["左肩岩体与谷下岩基相连"],
            "contour_rhythm": f"高低疏密及宽厚转折{i}",
            "sharpness_policy": "preserve_source_no_exaggeration",
            "differences": [
                {"other_id": f"a{k+1}", "structures": [
                    {"aspect": "peak_profile", "evidence": f"峰形差异{i}-{k}"},
                    {"aspect": "shoulder_steps", "evidence": f"岩肩差异{i}-{k}"},
                    {"aspect": "ridge_spacing", "evidence": f"间距差异{i}-{k}"}]}
                for k in range(5) if k != i],
        })
    return {"style": "当前原图的山体画风", "objects": objects}, {"objects": prompts}


def assets():
    result = []
    for i in range(5):
        rgb = np.ones((256, 256, 3), np.float32)
        points = np.array([[55, 198], [64+i*3, 150], [96+i*6, 68+i*13],
                           [135, 133-i*5], [176-i*3, 82+i*7], [200, 198]])
        cv2.fillPoly(rgb, [points], (.12+i*.09, .21+i*.05, .3+i*.02))
        result.append(rgb[None])
    return result


def report(plan, inspection, failed=()):
    return {
        "run_id": plan["run_id"], "review_id": inspection["review_id"],
        "objects": [
            {"id": t["id"],
             "checks": {key: {"passed": (i+1 not in failed), "evidence": f"原图对照{i}可见形状"}
                        for key in quality.VISUAL_CHECKS},
             "landmarks": [{"name": p["name"], "preserved": True,
                            "evidence": f"{p['shape']}在生成图可见"}
                           for p in t["landmarks"]]}
            for i, t in enumerate(plan["recognized_targets"])],
        "pairs": [
            {"ids": [a["id"], b["id"]], "distinct": True,
             "evidence": "轮廓和连接结构不同，不共享峰段"}
            for a, b in itertools.combinations(plan["recognized_targets"], 2)],
    }


class FiveQuality(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(quality, "五路质量门禁尚未实现")
        self.raw, self.pe = contracts()

    def plan(self):
        return quality.compile_plan(self.raw, self.pe, source(), "five/quality", canvas_size=256)

    def inspect(self, plan, images=None):
        return quality.inspect_images(plan, source(), assets() if images is None else images)[2]

    def test_exact_three_near_two_far_and_all_pairs(self):
        p = self.plan()
        self.assertEqual([t["role"] for t in p["recognized_targets"]],
                         ["mountain"]*3 + ["range"]*2)
        self.assertEqual(p["pair_count"], 10)
        self.assertEqual(len(p["execution_prompts"]), 5)

    def test_no_shortfall_or_depth_substitution_or_duplicate_unit(self):
        for edit in ("missing", "far_in_near", "duplicate"):
            self.raw, self.pe = contracts()
            if edit == "missing":
                self.raw["objects"].pop(2)
                self.pe["objects"].pop(2)
            elif edit == "far_in_near":
                self.raw["objects"][2]["layer"] = "background"
            else:
                self.raw["objects"][4]["unit_id"] = "u1"
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.plan()

    def test_missing_shape_requirements_block_generation(self):
        for field in ("landmarks", "contour_fingerprint", "rhythm", "connected_parts"):
            self.raw, self.pe = contracts()
            self.raw["objects"][0][field] = [] if field != "rhythm" else ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan()
        self.raw, self.pe = contracts()
        self.raw["objects"][0]["landmarks"] = self.raw["objects"][0]["landmarks"][:2]
        with self.assertRaises(ValueError):
            self.plan()

    def test_actual_connections_cannot_be_shared_snow_or_disconnected(self):
        for text in ("没有真实岩土连接", "共同雪地不是岩体", "彼此分离的山峰"):
            self.raw, self.pe = contracts()
            self.raw["objects"][0]["connected_parts"] = [text]
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.plan()

    def test_pe_must_lock_landmarks_connections_rhythm_and_all_four_differences(self):
        for field in ("preserved_landmarks", "protected_connections", "contour_rhythm",
                      "sharpness_policy", "differences"):
            self.raw, self.pe = contracts()
            self.pe["objects"][0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan()
        self.raw, self.pe = contracts()
        self.pe["objects"][0]["differences"][0]["structures"][0]["aspect"] = "color"
        with self.assertRaises(ValueError):
            self.plan()

    def test_crop_and_hash_come_from_current_source(self):
        p = self.plan()
        crop, marked, info = quality.prepare_reference(source(), p["recognized_targets"][0],
                                                       p["source_hash"])
        self.assertFalse(info["rescaled"])
        self.assertEqual(info["source_size"], [256, 256])
        self.assertFalse(np.array_equal(crop, marked))
        with self.assertRaisesRegex(ValueError, "原图|source"):
            quality.prepare_reference(np.ones_like(source()), p["recognized_targets"][0],
                                      p["source_hash"])

    def test_complete_bound_visual_report_and_ten_pairs_are_required(self):
        p = self.plan()
        inspection = self.inspect(p)
        r = report(p, inspection)
        self.assertEqual(quality.evaluate_review(p, inspection, r)["failed_branches"], [])
        mutations = [
            lambda x: x.update(run_id="old"),
            lambda x: x.update(review_id="old"),
            lambda x: x["pairs"].pop(),
            lambda x: x["objects"].pop(),
            lambda x: x["objects"][0]["checks"].pop("rhythm"),
            lambda x: x["objects"][0]["landmarks"].pop(),
            lambda x: x["objects"][0]["checks"]["rhythm"].update(passed="true"),
            lambda x: x["objects"][0]["checks"]["rhythm"].update(evidence=""),
        ]
        for mutate in mutations:
            invalid = copy.deepcopy(r)
            mutate(invalid)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                quality.evaluate_review(p, inspection, invalid)

    def test_each_visual_failure_and_duplicate_pair_trigger_only_affected_routes(self):
        p = self.plan()
        inspection = self.inspect(p)
        r = report(p, inspection, failed=(2,))
        state = quality.evaluate_review(p, inspection, r)
        self.assertEqual(state["failed_branches"], [2])
        retry = quality.repair_prompts(p, state)
        self.assertTrue(retry[1])
        self.assertEqual([i+1 for i, x in enumerate(retry) if x], [2])
        r = report(p, inspection)
        r["pairs"][0]["distinct"] = False
        self.assertEqual(quality.evaluate_review(p, inspection, r)["failed_branches"], [1, 2])

    def test_numerical_audit_detects_duplicates_mirrors_and_missing_margins(self):
        p = self.plan()
        for mirror in (False, True):
            imgs = assets()
            imgs[1] = imgs[0][:, :, ::-1].copy() if mirror else imgs[0].copy()
            inspection = self.inspect(p, imgs)
            self.assertTrue(any(pair["status"] == "duplicate" for pair in inspection["pairs"]))
        imgs = assets()
        imgs[0] = np.zeros_like(imgs[0])
        inspection = self.inspect(p, imgs)
        self.assertIn(1, quality.evaluate_review(p, inspection, report(p, inspection))["failed_branches"])

    def test_opaque_snow_fragments_are_diagnostic_but_disjoint_alpha_is_failure(self):
        rgb = np.ones((1, 256, 256, 3), np.float32)
        rgb[:, 90:140, 60:110] = .2
        rgb[:, 90:140, 145:195] = .2
        opaque = quality.image_quality(rgb, canvas_size=256)
        self.assertTrue(opaque["passed"])
        self.assertGreater(opaque["component_count"], 1)
        rgba = np.concatenate((rgb, np.zeros((1, 256, 256, 1), np.float32)), axis=-1)
        rgba[:, 90:140, 60:110, 3] = 1
        rgba[:, 90:140, 145:195, 3] = 1
        self.assertFalse(quality.image_quality(rgba, canvas_size=256)["passed"])

    def test_final_acceptance_binds_actual_pixels_and_rejects_failed_second_round(self):
        p = self.plan()
        imgs = assets()
        inspection = self.inspect(p, imgs)
        r = report(p, inspection)
        accepted = quality.finalize(p, inspection, r, imgs)
        self.assertEqual(accepted["png_count"], 5)
        with self.assertRaises(ValueError):
            quality.finalize(p, inspection, report(p, inspection, failed=(3,)), imgs)
        imgs[0] = imgs[0].copy()
        imgs[0][:, 110, 110] = .8
        with self.assertRaisesRegex(ValueError, "hash|图片"):
            quality.finalize(p, inspection, r, imgs)

    def test_prompts_restore_all_original_shape_rules(self):
        p = self.plan()
        for prompt in p["execution_prompts"]:
            for text in ("至少三", "轮廓", "岩基", "节奏", "不拔尖", "不统一圆丘", "70%", "15%"):
                self.assertIn(text, prompt)


if __name__ == "__main__":
    unittest.main()
