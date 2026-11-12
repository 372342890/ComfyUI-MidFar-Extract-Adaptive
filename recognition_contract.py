"""Deterministic identity/coordinate normalization and natural-unit admission."""
import math
import re
from itertools import combinations

try:
    from .quality_json import decode_record
except ImportError:
    from quality_json import decode_record


SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _identifier(value):
    return isinstance(value, str) and SAFE_ID.fullmatch(value)


def _objects(raw):
    objects = raw.get("objects")
    if not isinstance(objects, list) or not objects or any(
            not isinstance(obj, dict) for obj in objects):
        raise ValueError("识别必须包含完整目标对象数组。")
    return objects


def _identity(raw):
    aliases, used_units, used_ids = {}, set(), set()
    audit = raw.setdefault("_normalization_audit", [])
    if not isinstance(audit, list):
        raise ValueError("规范化记录必须是数组。")
    for obj in _objects(raw):
        original, unit = obj.get("id"), obj.get("unit_id")
        if not _identifier(unit) or unit in used_units:
            raise ValueError("unit_id必须安全且每个自然组唯一。")
        used_units.add(unit)
        canonical = original if _identifier(original) else unit if type(original) is int else None
        if canonical is None or canonical in used_ids:
            raise ValueError("目标id无效或存在重复。")
        used_ids.add(canonical)
        for alias in (str(original), unit, canonical):
            if alias in aliases and aliases[alias] != canonical:
                raise ValueError("目标id/unit_id别名歧义，不能自动对应。")
            aliases[alias] = canonical
        if original != canonical:
            audit.append({"operation": "identity_alias", "original": original,
                          "unit_id": unit, "id": canonical})
        obj["id"] = canonical
    return aliases


def _map_id(value, aliases):
    if type(value) not in (str, int) or str(value) not in aliases:
        raise ValueError("关联id没有唯一的本轮目标对应关系。")
    return aliases[str(value)]


def _map_links(raw, aliases):
    for obj in _objects(raw):
        if "connected_to" in obj:
            if not isinstance(obj["connected_to"], list):
                raise ValueError("connected_to必须是目标id数组。")
            obj["connected_to"] = [_map_id(v, aliases) for v in obj["connected_to"]]
    if "group_relations" in raw:
        if not isinstance(raw["group_relations"], list):
            raise ValueError("group_relations必须是数组。")
        for relation in raw["group_relations"]:
            if not isinstance(relation, dict) or not isinstance(relation.get("ids"), list):
                raise ValueError("自然组配对必须包含ids数组。")
            relation["ids"] = [_map_id(v, aliases) for v in relation["ids"]]


def _coordinates(raw, source_size):
    space = raw.get("coordinate_space", "normalized")
    if space not in ("normalized", "pixels", "0..1000"):
        raise ValueError("coordinate_space只能为normalized、pixels、0..1000。")
    declared_size = raw.get("source_size")
    if declared_size is not None:
        if (not isinstance(declared_size, list) or len(declared_size) != 2 or
                any(type(v) is not int or v <= 0 for v in declared_size)):
            raise ValueError("source_size需要原图像素宽高。")
        if source_size is not None and list(source_size) != declared_size:
            raise ValueError("声明的source_size不是当前原图尺寸。")
    if space == "pixels" and declared_size is None:
        raise ValueError("像素坐标必须明确声明source_size，不推测单位。")
    scale = declared_size if space == "pixels" else [1000, 1000] if space == "0..1000" else [1, 1]

    def convert(values, length):
        if not isinstance(values, list) or len(values) != length or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("bbox与point必须是有限数值坐标。")
        result = [v/scale[i % 2] for i, v in enumerate(values)]
        if any(v < 0 or v > 1 for v in result):
            raise ValueError("整份坐标必须使用声明单位；不能混用0..1和像素/1000。")
        return result

    for obj in _objects(raw):
        obj["bbox"] = convert(obj.get("bbox"), 4)
        box = obj["bbox"]
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("目标bbox必须有正宽高。")
        obj["target_point"] = convert(obj.get("target_point"), 2)
        marks = obj.get("landmarks")
        if not isinstance(marks, list):
            raise ValueError("目标必须提供原图landmarks。")
        points = [obj["target_point"]]
        for mark in marks:
            if not isinstance(mark, dict):
                raise ValueError("锚点必须是对象。")
            mark["point"] = convert(mark.get("point"), 2)
            points.append(mark["point"])
        if any(not box[0] <= p[0] < box[2] or not box[1] <= p[1] < box[3] for p in points):
            raise ValueError("目标点与锚点必须位于当前bbox内。")
    if space != "normalized":
        raw["_normalization_audit"].append(
            {"operation": "coordinate_space", "original": space, "scale": scale})
    raw["coordinate_space"] = "normalized"


def validate_grouping(raw, required=False):
    objects = _objects(raw)
    required = required or raw.get("grouping_contract") is not None
    ids = [obj.get("id") for obj in objects]
    if any(not _identifier(v) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("自然组id无效或重复。")
    parts = set()
    for obj in objects:
        if obj.get("is_single") is not True:
            raise ValueError("is_single必须表示一个完整自然组；连体多峰不能拆路。")
        if obj.get("connected_to"):
            raise ValueError("跨路目标存在真实连接，必须合并后重新识别自然组。")
        if obj.get("completion_policy") == "close_visible_rock_edge" and not (
                obj.get("role") == "mountain" and obj.get("kind") == "rock" and
                obj.get("boundary_cut") is True):
            raise ValueError("close_visible_rock_edge仅允许近景岩台可见截断末端。")
        components = obj.get("component_ids")
        if required or components is not None:
            if (not isinstance(components, list) or not components or
                    any(not _identifier(v) for v in components) or
                    len(set(components)) != len(components) or parts.intersection(components)):
                raise ValueError("组成岩体component_ids缺失、重复或被多路共享。")
            parts.update(components)
    if required and raw.get("grouping_contract") != "natural-units-v1":
        raise ValueError("新链路必须提供natural-units-v1自然分组复核。")
    relations = raw.get("group_relations")
    if not required and relations is None:
        return
    expected = {tuple(sorted(pair)) for pair in combinations(ids, 2)}
    if not isinstance(relations, list) or len(relations) != len(expected):
        raise ValueError("自然组复核必须完整包含全部十对。")
    seen = set()
    lookup = {obj["id"]: obj for obj in objects}
    for relation in relations:
        if not isinstance(relation, dict) or not isinstance(relation.get("ids"), list):
            raise ValueError("自然组关系格式无效。")
        pair_ids = relation["ids"]
        if len(pair_ids) != 2 or any(not _identifier(v) for v in pair_ids):
            raise ValueError("自然组配对id格式无效。")
        pair = tuple(sorted(pair_ids))
        if pair not in expected or pair in seen:
            raise ValueError("自然组配对重复或错配。")
        seen.add(pair)
        if relation.get("relation") == "connected":
            raise ValueError("同一连接山群被拆路，必须重新识别整个自然组。")
        if relation.get("relation") not in ("separated", "separate_depth"):
            raise ValueError("自然组关系需为separated或separate_depth。")
        if relation["relation"] == "separate_depth" and (
                lookup[pair[0]].get("layer") == lookup[pair[1]].get("layer")):
            raise ValueError("同景深目标不能仅用separate_depth声明分离。")
        if not isinstance(relation.get("evidence"), str) or not relation["evidence"].strip():
            raise ValueError("每对自然组必须提供当前原图可见分离证据。")


def normalize_recognition(value, source_size=None, require_groups=False):
    raw = decode_record(value)
    aliases = _identity(raw)
    _map_links(raw, aliases)
    _coordinates(raw, source_size)
    validate_grouping(raw, required=require_groups)
    return raw


def normalize_pair(recognition, pe, source_size=None):
    original = decode_record(recognition)
    aliases = _identity(original)
    _map_links(original, aliases)
    raw = normalize_recognition(original, source_size)
    written = decode_record(pe)
    for entry in _objects(written):
        entry["id"] = _map_id(entry.get("id"), aliases)
        if not isinstance(entry.get("differences"), list):
            raise ValueError("每路PE必须提供其他四路的结构排除。")
        for difference in entry["differences"]:
            if not isinstance(difference, dict):
                raise ValueError("PE结构排除必须是对象。")
            difference["other_id"] = _map_id(difference.get("other_id"), aliases)
    return raw, written
