"""Variable batches preserve pixels and reject exact or mirrored duplicates."""
import unittest

import numpy as np

import visible_batch
from test_visible_batch import image


class AdaptiveBatch(unittest.TestCase):
    def call(self, images):
        processor = getattr(visible_batch, "process_images", None)
        self.assertIsNotNone(processor, "Adaptive batch processor is missing.")
        return processor(images, "any")

    def test_one_and_two_images_are_preserved(self):
        for count in (1, 2):
            sources = [image(i) for i in range(count)]
            with self.subTest(count=count):
                outputs, records = self.call(sources)
                self.assertEqual(len(outputs), count)
                self.assertEqual(len(records), count)
                for output, source in zip(outputs, sources):
                    np.testing.assert_array_equal(output, source)

    def test_duplicate_pair_is_rejected(self):
        self.assertIsNotNone(getattr(visible_batch, "process_images", None))
        with self.assertRaisesRegex(ValueError, "重复"):
            self.call([image(), image().copy()])

    def test_mirrored_same_asset_is_rejected(self):
        self.assertIsNotNone(getattr(visible_batch, "process_images", None))
        source = image(2)
        with self.assertRaisesRegex(ValueError, "重复"):
            self.call([source, source[:, :, ::-1].copy()])

    def test_zero_and_four_images_are_rejected(self):
        self.assertIsNotNone(getattr(visible_batch, "process_images", None))
        for sources in ([], [image()] * 4):
            with self.subTest(count=len(sources)), self.assertRaises(ValueError):
                self.call(sources)

    def test_legacy_entry_point_still_requires_exactly_three(self):
        with self.assertRaisesRegex(ValueError, "三"):
            visible_batch.process_three([image(), image(1)], "any")


if __name__ == "__main__":
    unittest.main()
