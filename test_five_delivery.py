"""Keep cut provenance, encode real pixels, and give models complete JSON shapes."""
import base64
import hashlib
import io
import json
import unittest
import zipfile

import numpy as np
from PIL import Image

import five_quality as quality
from test_five_quality import assets, contracts, source
from test_five_quality_nodes import node_module
from test_visible_native_preview import Tensor


class FiveDelivery(unittest.TestCase):
    def test_json_value_is_a_visible_validated_constant_with_no_model(self):
        nodes = node_module()
        self.assertTrue(hasattr(nodes, "BAMidFarJSONValue"), "缺少恢复规划的可见JSON输入")
        node = nodes.BAMidFarJSONValue()
        self.assertFalse(node.INPUT_TYPES()["required"]["value"][1].get("forceInput", False))
        value = {"source_hash": "same-source", "target": {"id": "m3", "bbox": [.1, .2, .8, .9]}}
        self.assertEqual(json.loads(node.run(json.dumps(value))[0]), value)
        for invalid in ('{"id":"m1","id":"m3"}', '{"target":', '{"x":NaN}'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                node.run(invalid)

    def test_fit_preserves_whole_canvas_and_increases_all_margins(self):
        image = np.ones((1, 256, 256, 3), np.float32)
        image[:, 20:235, 4:252] = .2
        self.assertTrue(hasattr(quality, "fit_canvas"), "缺少保留全图的等比留白")
        fitted, record = quality.fit_canvas(image, 256)
        self.assertTrue(quality.image_quality(fitted, 256)["passed"])
        self.assertEqual(record["source_hash"], quality.image_hash(image))
        self.assertFalse(record["source_cut"])
        self.assertEqual(record["operation"], "whole_canvas_scale")
        self.assertEqual(record["scaled_size"][0], record["scaled_size"][1])
        self.assertEqual(record["source_bbox_pixels"], [4, 20, 252, 235])

    def test_fit_never_hides_a_cut_edge_or_changes_an_already_passing_image(self):
        self.assertTrue(hasattr(quality, "fit_canvas"), "缺少来源裁边保护")
        for edge in ("left", "right", "top", "bottom"):
            image = assets()[0].copy()
            if edge == "left":
                image[:, 90:170, :25] = .2
            elif edge == "right":
                image[:, 90:170, -25:] = .2
            elif edge == "top":
                image[:, :25, 90:170] = .2
            else:
                image[:, -25:, 90:170] = .2
            fitted, record = quality.fit_canvas(image, 256)
            self.assertTrue(record["source_cut"])
            self.assertTrue(np.array_equal(image, fitted))
            self.assertFalse(quality.image_quality(fitted, 256)["passed"])
        passing = assets()[0]
        fitted, record = quality.fit_canvas(passing, 256)
        self.assertTrue(np.array_equal(passing, fitted))
        self.assertEqual(record["operation"], "unchanged")

    def test_visual_prompt_has_five_separate_real_ids_and_all_ten_pairs(self):
        raw, pe = contracts()
        plan = quality.compile_plan(raw, pe, source(), "delivery", canvas_size=256)
        _, prompt, inspection = quality.inspect_images(plan, source(), assets())
        self.assertIn("完整输出模板：", prompt, "未给模型完整五对象模板")
        template = json.loads(prompt.split("完整输出模板：", 1)[1].split("本轮上下文：", 1)[0])
        self.assertEqual(template["review_id"], inspection["review_id"])
        self.assertEqual([o["id"] for o in template["objects"]], ["a1", "a2", "a3", "a4", "a5"])
        self.assertEqual(len(template["pairs"]), 10)
        for obj in template["objects"]:
            self.assertEqual(set(obj), {"id", "checks", "landmarks"})
            self.assertEqual(set(obj["checks"]), set(quality.VISUAL_CHECKS))
            self.assertEqual(len(obj["landmarks"]), 3)
            self.assertTrue(all(c["passed"] is False for c in obj["checks"].values()))

    def test_export_node_round_trips_five_real_pngs_without_claiming_acceptance(self):
        nodes = node_module()
        self.assertTrue(hasattr(nodes, "BAMidFarFiveArchive"), "缺少实际像素导出通道")
        result = nodes.BAMidFarFiveArchive().run(
            quality_record="", **{f"image_{i+1}": Tensor(x) for i, x in enumerate(assets())})
        payload = json.loads(result["result"][0])
        self.assertEqual(result["ui"]["text"], [result["result"][0]])
        self.assertFalse(payload["accepted"])
        data = base64.b64decode(payload["zip_base64"], validate=True)
        self.assertEqual(hashlib.sha256(data).hexdigest(), payload["zip_sha256"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(len([n for n in archive.namelist() if n.endswith(".png")]), 5)
            manifest = json.loads(archive.read("manifest.json"))
            for item, original in zip(manifest["files"], assets()):
                pixels = np.asarray(Image.open(io.BytesIO(archive.read(item["name"]))))
                np.testing.assert_array_equal(pixels, np.round(original[0]*255).astype(np.uint8))

    def test_export_cannot_relabel_unverified_or_changed_pixels_as_accepted(self):
        nodes = node_module()
        self.assertTrue(hasattr(nodes, "BAMidFarFiveArchive"), "缺少验收身份绑定")
        args = {f"image_{i+1}": Tensor(x) for i, x in enumerate(assets())}
        for record in ({"status": "accepted"}, {"status": "retry_required"},
                       {"status": "accepted", "image_hashes": ["bad"]*5}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                nodes.BAMidFarFiveArchive().run(quality_record=json.dumps(record), **args)


if __name__ == "__main__":
    unittest.main()
