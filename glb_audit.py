"""Dependency-free static GLB inspection and whole-asset bottom-origin export.

Only scene-node metadata is changed. Mesh, UV, normal, material and image buffers
are never decoded and re-encoded for export.
"""
import copy
import json
import math
import struct

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
               5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid {name}: expected integer >= {minimum}.")
    return value


def _object(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {name}: expected object.")
    return value


def _item(items, index, name):
    _integer(index, name)
    if not isinstance(items, list) or index >= len(items):
        raise ValueError(f"Invalid {name} reference: {index}.")
    return _object(items[index], name)


def _finite_vector(values, length, name):
    if (not isinstance(values, (list, tuple)) or len(values) != length
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
        raise ValueError(f"Invalid/non-finite {name}.")
    return values


def _no_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}.")


def read_glb(data):
    """Return JSON and untouched non-JSON chunks after validating GLB framing."""
    if not isinstance(data, bytes) or len(data) < 20:
        raise ValueError("Expected GLB v2 bytes.")
    magic, version, total = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or total != len(data):
        raise ValueError("Invalid GLB magic, version or declared file length.")
    chunks, offset = [], 12
    while offset < total:
        if offset + 8 > total:
            raise ValueError("Truncated GLB chunk header.")
        size, kind = struct.unpack_from("<II", data, offset)
        end = offset + 8 + size
        if size % 4 or end > total:
            raise ValueError("Truncated or unaligned GLB chunk.")
        chunks.append((kind, data[offset + 8:end]))
        offset = end
    if (not chunks or chunks[0][0] != JSON_CHUNK
            or sum(kind == JSON_CHUNK for kind, _ in chunks) != 1
            or sum(kind == BIN_CHUNK for kind, _ in chunks) != 1):
        raise ValueError("GLB must have one leading JSON chunk and one BIN chunk.")
    try:
        doc = json.loads(chunks[0][1].decode("utf8"), parse_constant=_no_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid GLB JSON.") from exc
    _object(doc, "GLB document")
    asset = _object(doc.get("asset"), "asset")
    if asset.get("version") != "2.0" or asset.get("minVersion", "2.0") != "2.0":
        raise ValueError("Only glTF 2.0 assets are supported.")
    return doc, chunks[1:]


def _write_glb(doc, chunks):
    text = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    text += b" " * (-len(text) % 4)
    body = b"".join(struct.pack("<II", len(payload), kind) + payload
                    for kind, payload in [(JSON_CHUNK, text), *chunks])
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


class _Accessors:
    def __init__(self, doc, chunks):
        self.doc = doc
        self.binary = next(payload for kind, payload in chunks if kind == BIN_CHUNK)
        buffers = doc.get("buffers", [])
        if len(buffers) != 1 or "uri" in _item(buffers, 0, "buffer"):
            raise ValueError("Only one embedded GLB binary buffer is supported.")
        length = _integer(buffers[0].get("byteLength"), "buffer byteLength", 1)
        if not length <= len(self.binary) <= length + 3:
            raise ValueError("BIN payload does not match buffer byteLength.")
        self.length = length
        for i in range(len(doc.get("bufferViews", []))):
            self.view(i)
        for image in doc.get("images", []):
            _object(image, "image")
            if "uri" in image:
                if not isinstance(image["uri"], str) or not image["uri"].startswith("data:"):
                    raise ValueError("External image textures are not supported.")
            elif "bufferView" in image:
                self.view(image["bufferView"])
            else:
                raise ValueError("Image is missing embedded data.")
        self.cache = {}

    def view(self, index):
        view = _item(self.doc.get("bufferViews", []), index, "bufferView")
        if view.get("buffer", 0) != 0:
            raise ValueError("External/multiple buffers are not supported.")
        if "EXT_meshopt_compression" in view.get("extensions", {}):
            raise ValueError("Meshopt-compressed geometry is not supported.")
        start = _integer(view.get("byteOffset", 0), "bufferView offset")
        length = _integer(view.get("byteLength"), "bufferView length", 1)
        if start + length > self.length:
            raise ValueError("bufferView exceeds embedded buffer.")
        return view, start, length

    def _values(self, view_id, offset, count, component, width, packed=False):
        view, start, length = self.view(view_id)
        fmt, size = _COMPONENTS[component]
        offset = _integer(offset, "accessor byteOffset")
        element = size * width
        stride = element if packed else view.get("byteStride", element)
        _integer(stride, "accessor stride", element)
        if (stride % size or (start + offset) % size
                or ("byteStride" in view and (packed or stride < 4 or stride > 252))
                or offset + (count - 1) * stride + element > length):
            raise ValueError("Invalid accessor offset, stride or range.")
        return [struct.unpack_from("<" + fmt * width, self.binary, start + offset + i * stride)
                for i in range(count)]

    def read(self, index, purpose):
        key = (index, purpose)
        if key in self.cache:
            return self.cache[key]
        accessor = _item(self.doc.get("accessors", []), index, "accessor")
        count = _integer(accessor.get("count"), "accessor count", 1)
        component = accessor.get("componentType")
        if component not in _COMPONENTS:
            raise ValueError("Unsupported accessor component type.")
        if purpose == "POSITION":
            width = 3
            if accessor.get("type") != "VEC3" or component not in (5120, 5121, 5122, 5123, 5126):
                raise ValueError("POSITION must be a supported VEC3.")
            if component != 5126 and "KHR_mesh_quantization" not in self.doc.get("extensionsUsed", []):
                raise ValueError("Integer POSITION requires KHR_mesh_quantization.")
        else:
            width = 1
            if accessor.get("type") != "SCALAR" or component not in (5121, 5123, 5125):
                raise ValueError("Triangle indices must be unsigned SCALAR.")
        normalized = accessor.get("normalized", False)
        if type(normalized) is not bool or (normalized and (purpose != "POSITION" or component == 5126)):
            raise ValueError("Invalid normalized accessor.")
        if "bufferView" in accessor:
            values = self._values(accessor["bufferView"], accessor.get("byteOffset", 0),
                                  count, component, width)
        else:
            if accessor.get("byteOffset", 0) != 0 or "sparse" not in accessor:
                raise ValueError("Accessor is missing bufferView/sparse storage.")
            # Bound sparse allocation by the payload, rather than trusting count.
            if count > max(1_000_000, self.length * 16):
                raise ValueError("Sparse accessor count exceeds supported buffer size.")
            values = [(0,) * width] * count
        if "sparse" in accessor:
            sparse = _object(accessor["sparse"], "sparse accessor")
            sparse_count = _integer(sparse.get("count"), "sparse count", 1)
            if sparse_count > count:
                raise ValueError("Sparse count exceeds accessor count.")
            ids = _object(sparse.get("indices"), "sparse indices")
            sparse_component = ids.get("componentType")
            if sparse_component not in (5121, 5123, 5125):
                raise ValueError("Invalid sparse index component type.")
            replacements = _object(sparse.get("values"), "sparse values")
            indexes = self._values(ids.get("bufferView"), ids.get("byteOffset", 0),
                                   sparse_count, sparse_component, 1, packed=True)
            updates = self._values(replacements.get("bufferView"), replacements.get("byteOffset", 0),
                                   sparse_count, component, width, packed=True)
            previous = -1
            for (slot,), update in zip(indexes, updates):
                if not previous < slot < count:
                    raise ValueError("Sparse indices must be ordered, distinct and in range.")
                values[slot] = update
                previous = slot
        if normalized:
            maximum = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535}[component]
            values = [tuple(max(-1, v / maximum) if component in (5120, 5122) else v / maximum
                            for v in row) for row in values]
        if any(not math.isfinite(value) for row in values for value in row):
            raise ValueError("Non-finite mesh vertex.")
        self.cache[key] = values
        return values


def _multiply(a, b):
    return tuple(sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
                 for row in range(4) for col in range(4))


def _matrix(node):
    if "matrix" in node:
        if any(key in node for key in ("translation", "rotation", "scale")):
            raise ValueError("Node cannot have both matrix and TRS transforms.")
        column = _finite_vector(node["matrix"], 16, "node matrix")
        result = tuple(column[col * 4 + row] for row in range(4) for col in range(4))
        if any(abs(a - b) > 1e-10 for a, b in zip(result[12:], (0, 0, 0, 1))):
            raise ValueError("Node matrix must be affine.")
        return result
    t = _finite_vector(node.get("translation", [0, 0, 0]), 3, "translation")
    s = _finite_vector(node.get("scale", [1, 1, 1]), 3, "scale")
    q = _finite_vector(node.get("rotation", [0, 0, 0, 1]), 4, "rotation")
    norm = math.sqrt(sum(v * v for v in q))
    if abs(norm - 1) > 1e-4:
        raise ValueError("Node rotation quaternion must be unit length.")
    x, y, z, w = [v / norm for v in q]
    rotation = (1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w),
                2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w),
                2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y))
    return tuple(rotation[row * 3 + col] * s[col] if col < 3 else t[row]
                 for row in range(3) for col in range(4)) + (0, 0, 0, 1)


def _point(matrix, xyz):
    result = [sum(matrix[row * 4 + col] * xyz[col] for col in range(3))
              + matrix[row * 4 + 3] for row in range(3)]
    _finite_vector(result, 3, "world-space position")
    return result


def _nondegenerate(a, b, c):
    u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    cross = (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])
    return any(value != 0 for value in cross)


def _geometry(doc, chunks, up_axis):
    if up_axis not in ("Y", "Z"):
        raise ValueError("up_axis must be Y or Z.")
    if doc.get("skins") or doc.get("animations"):
        raise ValueError("Animated/skinned GLBs require a separate baking workflow.")
    used = doc.get("extensionsUsed", [])
    if any(name in used for name in ("KHR_draco_mesh_compression", "EXT_meshopt_compression")):
        raise ValueError("Draco/meshopt compressed GLBs are not supported.")
    for name in doc.get("extensionsRequired", []):
        if (name != "KHR_mesh_quantization" and not name.startswith("KHR_materials_")
                and name not in ("KHR_texture_transform", "KHR_texture_basisu",
                                 "EXT_texture_webp", "EXT_texture_avif")):
            raise ValueError(f"Unsupported required extension: {name}.")
    for mesh in doc.get("meshes", []):
        _object(mesh, "mesh")
        if mesh.get("weights") or any(p.get("targets") for p in mesh.get("primitives", [])):
            raise ValueError("Morph-target meshes are not supported.")
    accessors = _Accessors(doc, chunks)
    scene_index = doc.get("scene", 0)
    scene = _item(doc.get("scenes", []), scene_index, "scene")
    roots = scene.get("nodes", [])
    if not isinstance(roots, list) or not roots:
        raise ValueError("Active scene has no root nodes.")
    nodes, seen = doc.get("nodes", []), set()
    low, high = [math.inf]*3, [-math.inf]*3
    triangles, valid_triangles, instances = 0, 0, 0
    stack = [(root, _IDENTITY, 0) for root in reversed(roots)]
    while stack:
        node_id, parent, depth = stack.pop()
        node = _item(nodes, node_id, "node")
        if node_id in seen or depth > 512:
            raise ValueError("Scene graph contains a cycle, shared node or excessive depth.")
        seen.add(node_id)
        if "skin" in node or node.get("weights"):
            raise ValueError("Skin/morph node is not supported.")
        if "EXT_mesh_gpu_instancing" in node.get("extensions", {}):
            raise ValueError("GPU-instanced meshes are not supported.")
        world = _multiply(parent, _matrix(node))
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("Node children must be a list.")
        stack.extend((child, world, depth + 1) for child in reversed(children))
        if "mesh" not in node:
            continue
        mesh = _item(doc.get("meshes", []), node["mesh"], "mesh")
        instances += 1
        for primitive in mesh.get("primitives", []):
            _object(primitive, "primitive")
            if primitive.get("mode", 4) != 4 or primitive.get("targets"):
                raise ValueError("Only static TRIANGLES primitives are supported.")
            if "KHR_draco_mesh_compression" in primitive.get("extensions", {}):
                raise ValueError("Draco compressed geometry is not supported.")
            attrs = _object(primitive.get("attributes"), "primitive attributes")
            positions = accessors.read(attrs.get("POSITION"), "POSITION")
            indexes = ([row[0] for row in accessors.read(primitive["indices"], "indices")]
                       if "indices" in primitive else list(range(len(positions))))
            if not indexes or len(indexes) % 3 or any(i >= len(positions) for i in indexes):
                raise ValueError("Invalid triangle count or out-of-range vertex index.")
            rendered = {i: _point(world, positions[i]) for i in set(indexes)}
            for point in rendered.values():
                low = [min(a, b) for a, b in zip(low, point)]
                high = [max(a, b) for a, b in zip(high, point)]
            triangles += len(indexes) // 3
            valid_triangles += sum(_nondegenerate(*(rendered[i] for i in indexes[offset:offset+3]))
                                   for offset in range(0, len(indexes), 3))
    if valid_triangles == 0:
        raise ValueError("Active scene has no nondegenerate triangle geometry.")
    extents = [high[i] - low[i] for i in range(3)]
    _finite_vector(extents, 3, "model dimensions")
    up = 1 if up_axis == "Y" else 2
    depth_axis = 2 if up == 1 else 1
    width, height, depth = extents[0], extents[up], extents[depth_axis]
    center = [(low[i] + high[i]) / 2 for i in range(3)]
    bottom = list(center)
    bottom[up] = low[up]
    return {
        "up_axis": up_axis, "scene_index": scene_index,
        "bounds": {"min": low, "max": high},
        "dimensions": {"width": width, "height": height, "depth": depth},
        "longest_edge": max(extents),
        "ratios": {"width_to_height": width / height if height else None,
                   "width_to_depth": width / depth if depth else None,
                   "height_to_depth": height / depth if depth else None},
        "triangles": triangles, "nondegenerate_triangles": valid_triangles,
        "mesh_instances": instances, "scene_nodes": len(seen),
        "bounds_center": center, "bottom_center": bottom,
    }


def inspect_glb(data, up_axis="Y"):
    doc, chunks = read_glb(data)
    return _geometry(doc, chunks, up_axis)


def process_glb(data, up_axis="Y", bottom_origin=True, size_mode="keep", target_longest_edge=10.0):
    """Audit, uniformly size and translate the complete scene under an identity root."""
    if size_mode not in ("keep", "longest_edge"):
        raise ValueError("size_mode must be keep or longest_edge.")
    if (type(target_longest_edge) not in (int, float) or not math.isfinite(target_longest_edge)
            or target_longest_edge <= 0):
        raise ValueError("target_longest_edge must be a positive finite number in model units.")
    doc, chunks = read_glb(data)
    before = _geometry(doc, chunks, up_axis)
    scale = target_longest_edge / before["longest_edge"] if size_mode == "longest_edge" else 1
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Configured size produces an invalid uniform scale.")
    if abs(scale - 1) <= 1e-12:
        scale = 1
    pivot = before["bottom_center"]
    translation = [-v * scale if v else 0 for v in pivot] if bottom_origin else [0, 0, 0]
    _finite_vector(translation, 3, "scaled origin translation")
    expected_dimensions = {key: value * scale for key, value in before["dimensions"].items()}
    tolerance = max(before["longest_edge"] * scale * 1e-7, 1e-10)
    source_tolerance = max(before["longest_edge"] * 1e-7, 1e-10)
    output = data
    if bottom_origin or scale != 1:
        scene = doc["scenes"][before["scene_index"]]
        roots = scene["nodes"]
        prior_root = doc["nodes"][roots[0]] if len(roots) == 1 else {}
        already_wrapped = (prior_root.get("extras", {}).get("midfar_role") == "asset_origin"
                           and _matrix(prior_root) == _IDENTITY
                           and scale == 1
                           and (not bottom_origin or all(abs(v) <= source_tolerance for v in pivot)))
        if already_wrapped:
            translation = [0, 0, 0]
        else:
            doc = copy.deepcopy(doc)
            scene = doc["scenes"][before["scene_index"]]
            offset_index = len(doc["nodes"])
            offset_node = {
                "name": "MidFar_WholeAssetOffset", "children": list(roots),
                "translation": translation, "extras": {"midfar_role": "content_offset"}}
            if scale != 1:
                offset_node["scale"] = [scale, scale, scale]
            doc["nodes"].append(offset_node)
            origin_index = len(doc["nodes"])
            doc["nodes"].append({
                "name": "MidFar_BottomCenterOrigin" if bottom_origin else "MidFar_AssetOrigin",
                "children": [offset_index],
                "extras": {"midfar_role": "asset_origin", "up_axis": up_axis}})
            scene["nodes"] = [origin_index]
            output = _write_glb(doc, chunks)
    # Reopen the actual export; do not infer success solely from the translation.
    after_doc, after_chunks = read_glb(output)
    after = _geometry(after_doc, after_chunks, up_axis)
    preserved = (chunks == after_chunks and all(
        abs(expected_dimensions[key] - after["dimensions"][key]) <= tolerance
        for key in before["dimensions"]) and before["triangles"] == after["triangles"])
    expected_edge = before["longest_edge"] if size_mode == "keep" else target_longest_edge
    if (not preserved or abs(after["longest_edge"] - expected_edge) > tolerance
            or (bottom_origin and any(abs(v) > tolerance for v in after["bottom_center"]))):
        raise ValueError("GLB export verification failed: geometry/payload/origin changed incorrectly.")
    report = dict(before)
    report.update({
        "status": "success", "origin_applied": bool(bottom_origin),
        "size_mode": size_mode, "scale_factor": scale,
        "target_longest_edge": target_longest_edge if size_mode == "longest_edge" else None,
        "source_dimensions": before["dimensions"], "source_longest_edge": before["longest_edge"],
        "source_ratios": before["ratios"],
        "dimensions": after["dimensions"], "longest_edge": after["longest_edge"],
        "ratios": after["ratios"],
        "translation": translation, "bounds_after": after["bounds"],
        "pivot_before": pivot,
        "pivot_after": [0, 0, 0] if bottom_origin else after["bottom_center"],
        "measured_bottom_center_after": after["bottom_center"],
        "camera_target": after["bounds_center"],
        "source_file_bytes": len(data), "file_bytes": len(output),
        "file_mib": len(output) / (1024 * 1024),
        "binary_payload_preserved": True, "export_verified": True,
        "scaling_applied": scale != 1, "units": "model_units",
    })
    return output, report
