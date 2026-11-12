import unittest

try:
    from quality_json import decode_record
except ImportError:
    decode_record = None


class QualityJSON(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(decode_record, "strict decoder missing")

    def test_complete_fence_bom_and_detached_object(self):
        self.assertEqual(decode_record('\ufeff```json\n{"a":1}\n```'), {"a": 1})
        obj = {"a": [1]}
        decoded = decode_record(obj)
        decoded["a"].append(2)
        self.assertEqual(obj, {"a": [1]})

    def test_duplicate_truncation_nonfinite_and_nonobject_fail(self):
        for value in ('{"a":1,"a":2}', '{"a":"cut', '{"a":NaN}',
                      '{"a":1e999}', '[1]', '```json\n{"a":1}',
                      {"a": float("inf")}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_record(value)

    def test_actual_failed_output_fragment_does_not_silently_replace_bbox(self):
        value = ('{"objects":[{"id":"mid_left_mountain",'
                 '"bbox":[0.0,123.0/1024,605.0/1024,482.0/1024],'
                 '"bbox":[0.0,0.12,0.59,0.47]}]}')
        with self.assertRaisesRegex(ValueError, "重复"):
            decode_record(value)

    def test_coordinate_literal_ratio_has_audit_and_strings_untouched(self):
        result = decode_record(
            '{"objects":[{"bbox":[0,123.0/1024,1,1],'
            '"target_point":[512/1024,0.5],'
            '"landmarks":[{"point":[256/1024,0.5]}]}],"note":"512/1024"}')
        self.assertEqual(result["objects"][0]["target_point"], [.5, .5])
        self.assertEqual(result["objects"][0]["bbox"][1], 123/1024)
        self.assertEqual(result["note"], "512/1024")
        self.assertEqual(len(result["_normalization_audit"]), 3)

    def test_ratios_cannot_execute_or_modify_arbitrary_fields(self):
        for value in ('{"score":1/2}', '{"bbox":[1/2,0,1,1]}',
                      '{"objects":[{"target_point":[1/0,0]}]}',
                      '{"objects":[{"target_point":[(1+2)/3,0]}]}',
                      '{"objects":[{"target_point":[1/2/3,0]}]}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_record(value)


if __name__ == "__main__":
    unittest.main()
