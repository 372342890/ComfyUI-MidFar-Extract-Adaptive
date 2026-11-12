import unittest
import numpy as np

from five_audit import audit_pairs


def textured(seed=1, crop=False):
    rng = np.random.default_rng(seed)
    texture = rng.uniform(.1, .8, (160, 240, 3)).astype(np.float32)
    if crop:
        texture = texture[:, 70:190]
    h, w = texture.shape[:2]
    image = np.ones((1, 300, 360, 3), np.float32)
    image[0, 50:50 + h, 40:40 + w] = texture
    return image


class FiveAudit(unittest.TestCase):
    def test_ten_pairs_and_exact_mirror_detection(self):
        images = [textured(i) for i in range(5)]
        images[4] = images[0][:, :, ::-1].copy()
        result = audit_pairs(images)
        self.assertEqual(result["pair_count"], 10)
        pair = next(p for p in result["pairs"] if p["branches"] == [1, 5])
        self.assertEqual(pair["status"], "duplicate")
        self.assertLess(pair["orientations"][1]["color_error"], .008)

    def test_partial_crop_has_consistent_detail_evidence(self):
        result = audit_pairs([textured(3), textured(3, crop=True)])
        self.assertEqual(result["pairs"][0]["partial_containment"]["status"], "review_required")
        self.assertEqual(result["status"], "review_required")

    def test_distinct_textures_do_not_claim_containment(self):
        result = audit_pairs([textured(10), textured(20)])
        self.assertEqual(result["status"], "no_duplicate_evidence")


if __name__ == "__main__":
    unittest.main()
