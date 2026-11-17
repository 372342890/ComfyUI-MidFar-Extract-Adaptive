"""Geometry acceptance tests use real GLB bytes, without a model provider."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).parent
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def pack_glb(document, binary, extra=()):
    text = json.dumps(document, separators=(",", ":")).encode()
    chunks = [(JSON_CHUNK, text + b" " * (-len(text) % 4)),
              (BIN_CHUNK, binary + b"\0" * (-len(binary) % 4)), *extra]
    body = b"".join(struct.pack("<II", len(payload), kind) + payload
                    for kind, payload in chunks)
    return struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body


def unpack_glb(data):
    chunks = []
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        chunks.append((kind, data[offset + 8:offset + 8 + length]))
        offset += 8 + length
    return json.loads(chunks[0][1]), chunks[1:]


def fixture(points=None, nodes=None, roots=None, indices=None):
    points = points or [(2, 3, 4), (6, 3, 4), (2, 7, 8)]
    indices = indices if indices is not None else [0, 1, 2]
    positions = b"".join(struct.pack("<3f", *p) for p in points)
    index_bytes = struct.pack("<" + "H" * len(indices), *indices)
    # Unrelated embedded payload must survive bit-for-bit.
    payload = positions + index_bytes
    payload += b"\0" * (-len(payload) % 4)
    image_offset = len(payload)
    payload += b"embedded-image-data!"
    doc = {
        "asset": {"version": "2.0", "extras": {"custom": "retain"}},
        "scene": 0, "scenes": [{"nodes": roots if roots is not None else [0]}],
        "nodes": nodes or [{"mesh": 0, "name": "original-rock"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1,
                                   "material": 0}]}],
        "buffers": [{"byteLength": len(payload)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes)},
            {"buffer": 0, "byteOffset": image_offset, "byteLength": 20}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(points), "type": "VEC3",
             "min": [-999, -999, -999], "max": [999, 999, 999]},
            {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"}],
        "images": [{"bufferView": 2, "mimeType": "image/png"}],
        "textures": [{"source": 0}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "extras": {"artist": "natural connected formation"},
    }
    # Actual image payload is 20 bytes.
    doc["bufferViews"][2]["byteLength"] = len(b"embedded-image-data!")
    return doc, payload


def core_module():
    path = ROOT / "glb_audit.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_glb_audit_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GLBAudit(unittest.TestCase):
    def setUp(self):
        self.core = core_module()
        self.assertIsNotNone(self.core, "Missing GLB measurement and bottom-origin feature")

    def test_actual_bounds_ignore_stale_accessor_bounds_and_unused_vertices(self):
        doc, binary = fixture(points=[(2, 3, 4), (6, 3, 4), (2, 7, 8), (999, 999, 999)])
        result = self.core.inspect_glb(pack_glb(doc, binary))
        self.assertEqual(result["bounds"], {"min": [2, 3, 4], "max": [6, 7, 8]})
        self.assertEqual(result["dimensions"], {"width": 4, "height": 4, "depth": 4})
        self.assertEqual(result["triangles"], 1)

    def test_export_preserves_binary_materials_metadata_and_identity_root(self):
        doc, binary = fixture()
        original = pack_glb(doc, binary, [(0x12345678, b"keep")])
        output, report = self.core.process_glb(original)
        out_doc, out_chunks = unpack_glb(output)
        _, in_chunks = unpack_glb(original)
        self.assertEqual(out_chunks, in_chunks)
        for key in ("meshes", "accessors", "bufferViews", "materials", "images", "textures",
                    "buffers", "asset", "extras"):
            self.assertEqual(out_doc[key], doc[key])
        self.assertEqual(out_doc["nodes"][:len(doc["nodes"])], doc["nodes"])
        root = out_doc["nodes"][out_doc["scenes"][0]["nodes"][0]]
        self.assertNotIn("matrix", root)
        self.assertNotIn("translation", root)
        self.assertNotIn("scale", root)
        self.assertEqual(report["translation"], [-4, -3, -6])
        self.assertEqual(report["pivot_after"], [0, 0, 0])
        self.assertEqual(report["bounds_after"]["min"], [-2, 0, -2])
        self.assertEqual(report["camera_target"], [0, 2, 0])
        self.assertEqual(self.core.inspect_glb(output)["dimensions"], report["dimensions"])
        second, again = self.core.process_glb(output)
        self.assertEqual(second, output)
        self.assertEqual(again["translation"], [0, 0, 0])

    def test_multiple_roots_and_hierarchy_keep_relative_shape(self):
        nodes = [{"children": [1], "translation": [10, 0, 0]},
                 {"mesh": 0, "scale": [-2, 1, 1]},
                 {"mesh": 0, "translation": [-10, 1, 0]}]
        doc, binary = fixture(nodes=nodes, roots=[0, 2])
        source = pack_glb(doc, binary)
        before = self.core.inspect_glb(source)
        self.assertEqual(before["triangles"], 2)
        self.assertEqual(before["bounds"]["min"], [-8, 3, 4])
        self.assertEqual(before["bounds"]["max"], [6, 8, 8])
        output, report = self.core.process_glb(source)
        self.assertEqual(report["dimensions"], before["dimensions"])
        self.assertEqual(self.core.inspect_glb(output)["triangles"], 2)
        out_doc, _ = unpack_glb(output)
        self.assertEqual(out_doc["nodes"][:3], nodes)

    def test_matrix_and_quaternion_rotation_compose_and_z_up(self):
        nodes = [{"children": [1], "matrix": [1, 0, 0, 0, 0, 1, 0, 0,
                                            0, 0, 1, 0, 10, 20, 30, 1]},
                 {"mesh": 0, "rotation": [0, 0, math.sqrt(.5), math.sqrt(.5)]}]
        doc, binary = fixture(nodes=nodes)
        original = pack_glb(doc, binary)
        result = self.core.inspect_glb(original, up_axis="Z")
        for got, want in zip(result["bounds"]["min"], [3, 22, 34]):
            self.assertAlmostEqual(got, want)
        self.assertEqual(result["dimensions"]["height"], 4)
        output, report = self.core.process_glb(original, up_axis="Z")
        self.assertAlmostEqual(report["bounds_after"]["min"][2], 0)
        self.assertEqual(report["pivot_after"], [0, 0, 0])
        self.assertEqual(self.core.inspect_glb(output, "Z")["triangles"], 1)

    def test_strided_sparse_and_quantized_positions(self):
        doc, _ = fixture()
        binary = b"".join(struct.pack("<3fh", *p, 123) + b"\0\0"
                          for p in [(2, 3, 4), (6, 3, 4), (0, 0, 0)])
        binary += b"\2\0\0\0" + struct.pack("<3f", 2, 7, 8)
        doc["buffers"][0]["byteLength"] = len(binary)
        doc["bufferViews"] = [
            {"buffer": 0, "byteOffset": 0, "byteLength": 48, "byteStride": 16},
            {"buffer": 0, "byteOffset": 48, "byteLength": 1},
            {"buffer": 0, "byteOffset": 52, "byteLength": 12}]
        doc["accessors"] = [dict(doc["accessors"][0], sparse={
            "count": 1, "indices": {"bufferView": 1, "componentType": 5121},
            "values": {"bufferView": 2}})]
        del doc["meshes"][0]["primitives"][0]["indices"]
        doc["images"] = [{"bufferView": 2, "mimeType": "image/png"}]
        source = pack_glb(doc, binary)
        self.assertEqual(self.core.inspect_glb(source)["bounds"]["max"], [6, 7, 8])
        self.core.process_glb(source)
        # Sparse-only normalized int16 positions.
        doc["extensionsUsed"] = ["KHR_mesh_quantization"]
        doc["extensionsRequired"] = ["KHR_mesh_quantization"]
        binary = b"\0\1\2\0" + struct.pack("<9h", 0, 0, 0, 32767, 0, 0, 0, 32767, 32767)
        doc["buffers"][0]["byteLength"] = len(binary)
        doc["bufferViews"] = [{"buffer": 0, "byteLength": 3},
                              {"buffer": 0, "byteOffset": 4, "byteLength": 18}]
        doc["accessors"] = [{"componentType": 5122, "count": 3, "type": "VEC3",
                             "normalized": True, "sparse": {
                                 "count": 3, "indices": {"bufferView": 0, "componentType": 5121},
                                 "values": {"bufferView": 1}}}]
        doc["images"][0]["bufferView"] = 1
        self.assertEqual(self.core.inspect_glb(pack_glb(doc, binary))["dimensions"],
                         {"width": 1, "height": 1, "depth": 1})
        del doc["extensionsUsed"]
        del doc["extensionsRequired"]
        with self.assertRaises(ValueError):
            self.core.inspect_glb(pack_glb(doc, binary))

    def test_detection_only_retains_exact_source_and_no_dimension_changes(self):
        doc, binary = fixture()
        source = pack_glb(doc, binary)
        output, report = self.core.process_glb(source, bottom_origin=False)
        self.assertEqual(output, source)
        self.assertFalse(report["origin_applied"])
        self.assertEqual(report["pivot_after"], [4, 3, 6])
        self.assertEqual(report["longest_edge"], 4)

    def test_invalid_container_or_unsupported_geometry_is_rejected(self):
        doc, binary = fixture()
        source = pack_glb(doc, binary)
        for invalid in (b"bad", source[:-1], source + b"\0", source[:4] + b"\1" + source[5:]):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                self.core.inspect_glb(invalid)
        cases = []
        for key, value in (("animations", [{}]), ("skins", [{}])):
            changed = copy.deepcopy(doc)
            changed[key] = value
            cases.append(changed)
        changed = copy.deepcopy(doc)
        changed["images"][0] = {"uri": "external.png"}
        cases.append(changed)
        changed = copy.deepcopy(doc)
        changed["buffers"][0]["uri"] = "external.bin"
        cases.append(changed)
        for field, value in (("targets", [{"POSITION": 0}]), ("mode", 1),
                             ("extensions", {"KHR_draco_mesh_compression": {}})):
            changed = copy.deepcopy(doc)
            changed["meshes"][0]["primitives"][0][field] = value
            cases.append(changed)
        changed = copy.deepcopy(doc)
        changed["bufferViews"][0]["extensions"] = {"EXT_meshopt_compression": {}}
        cases.append(changed)
        for changed in cases:
            with self.subTest(document=changed), self.assertRaises(ValueError):
                self.core.inspect_glb(pack_glb(changed, binary))

    def test_corrupt_accessor_graph_nonfinite_and_empty_triangles_rejected(self):
        doc, binary = fixture()
        changes = []
        for nodes, roots in (([{"children": [0], "mesh": 0}], [0]),
                             ([{"children": [2]}, {"children": [2]}, {"mesh": 0}], [0, 1]),
                             ([{"mesh": 99}], [0]), ([{"mesh": 0}], [99]),
                             ([{"mesh": 0, "scale": [1, float("inf"), 1]}], [0])):
            changed = copy.deepcopy(doc)
            changed["nodes"], changed["scenes"][0]["nodes"] = nodes, roots
            changes.append((changed, binary))
        changed = copy.deepcopy(doc)
        changed["bufferViews"][0]["byteLength"] = 4
        changes.append((changed, binary))
        changed = copy.deepcopy(doc)
        changed["accessors"][1]["count"] = 2
        changes.append((changed, binary))
        changes.append((doc, struct.pack("<f", float("nan")) + binary[4:]))
        bad_doc, bad_binary = fixture(indices=[0, 1, 99])
        changes.append((bad_doc, bad_binary))
        flat_doc, flat_binary = fixture(points=[(1, 1, 1)] * 3)
        changes.append((flat_doc, flat_binary))
        for changed, data in changes:
            with self.subTest(document=changed), self.assertRaises(ValueError):
                self.core.inspect_glb(pack_glb(changed, data))


if __name__ == "__main__":
    unittest.main()
