"""Dependency-free size decisions and verification against actual GLB bytes."""
import hashlib
import json
import math

from .glb_audit import inspect_glb

SIZE_KEYS = ("longest_edge", "width", "height", "depth")
DIMENSION_KEYS = SIZE_KEYS[1:]


def normalize_targets(targets):
    if not isinstance(targets, dict) or any(key not in SIZE_KEYS for key in targets):
        raise ValueError("目标尺寸必须使用 longest_edge/width/height/depth。")
    result = {key: targets.get(key, 0.0) for key in SIZE_KEYS}
    for key, value in result.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"目标 {key} 必须是有限非负数；0 表示关闭该尺寸。")
    if not any(result.values()):
        raise ValueError("至少填写一个大于 0 的最终目标尺寸。")
    return result


def _positive_factor(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("缩放倍数必须是有限正数。")
    return value


def _same_number(actual, expected):
    return (type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=0.0))


def calculate_size_scale(measurement, targets):
    targets = normalize_targets(targets)
    dimensions = measurement.get("dimensions")
    if (not isinstance(dimensions, dict)
            or any(type(dimensions.get(key)) not in (int, float)
                   or not math.isfinite(dimensions[key]) or dimensions[key] < 0
                   for key in DIMENSION_KEYS)):
        raise ValueError("真实模型尺寸无效。")
    edge = max(dimensions.values())
    if edge <= 0 or not _same_number(measurement.get("longest_edge"), edge):
        raise ValueError("真实模型最长边无效或与宽高深不一致。")
    sizes = dict(dimensions, longest_edge=edge)
    ratios = {key: targets[key] / sizes[key] for key in SIZE_KEYS
              if targets[key] > 0 and sizes[key] > 0}
    if not ratios:
        raise ValueError("选定尺寸在真实模型中为 0，无法据此计算倍数；请填写其他尺寸。")
    factor = _positive_factor(min(ratios.values()))
    predicted = {key: dimensions[key] * factor for key in DIMENSION_KEYS}
    if any(not math.isfinite(value) for value in predicted.values()):
        raise ValueError("目标尺寸导致模型数值溢出。")
    return {
        "scale_factor": factor,
        "source_dimensions": dict(dimensions), "source_longest_edge": edge,
        "predicted_dimensions": predicted, "predicted_longest_edge": edge * factor,
        "limiting_constraints": [key for key, ratio in ratios.items() if _same_number(ratio, factor)],
    }


def parse_size_plan(text, active, up_axis):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("scale_plan 必须是自动尺寸节点输出的完整 JSON。")
    try:
        plan = json.loads(text)
    except ValueError as exc:
        raise ValueError("scale_plan 不是合法 JSON。") from exc
    if (not isinstance(plan, dict) or plan.get("node") != "BAMidFarModelSizePlan"
            or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1
            or plan.get("status") != "success" or plan.get("up_axis") != up_axis
            or plan.get("units") != "model_units"):
        raise ValueError("自动尺寸计划类型、版本、状态或坐标轴不匹配。")
    routes = plan.get("active_branches")
    items = plan.get("outputs")
    if (not isinstance(routes, list) or any(type(i) is not int for i in routes)
            or routes != active or not isinstance(items, list)
            or len(items) != len(active) or any(not isinstance(item, dict) for item in items)
            or any(type(item.get("branch")) is not int for item in items)
            or [item["branch"] for item in items] != active
            or plan.get("skipped_branches") != [i for i in (1, 2, 3) if i not in active]):
        raise ValueError("自动尺寸计划与实际有效路不一致。")
    return normalize_targets(plan.get("targets")), {item["branch"]: item for item in items}


def verify_size_plan_item(data, item, targets, up_axis, applied_scale):
    if not isinstance(item, dict) or item.get("source_sha256") != hashlib.sha256(data).hexdigest():
        raise ValueError("源模型已变化，自动尺寸计划已过期，请重新计算。")
    decision = calculate_size_scale(inspect_glb(data, up_axis), targets)
    for key in ("scale_factor", "source_longest_edge", "predicted_longest_edge"):
        if not _same_number(item.get(key), decision[key]):
            raise ValueError(f"自动尺寸计划的 {key} 与真实模型重算结果不一致。")
    for key in ("source_dimensions", "predicted_dimensions"):
        value = item.get(key)
        if not isinstance(value, dict) or any(
                not _same_number(value.get(d), decision[key][d]) for d in DIMENSION_KEYS):
            raise ValueError(f"自动尺寸计划的 {key} 与真实模型不一致。")
    if item.get("limiting_constraints") != decision["limiting_constraints"]:
        raise ValueError("自动尺寸计划的限制尺寸不一致。")
    _positive_factor(applied_scale)
    if not _same_number(applied_scale, decision["scale_factor"]):
        raise ValueError("接入的缩放倍数与目标尺寸计算结果不一致。")
    return decision


def verify_final_size(measurement, decision, targets):
    targets = normalize_targets(targets)
    dimensions = measurement.get("dimensions", {})
    tolerance = max(decision["predicted_longest_edge"] * 1e-7, 1e-10)
    for key in DIMENSION_KEYS:
        value = dimensions.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value)
                or abs(value - decision["predicted_dimensions"][key]) > tolerance):
            raise ValueError("实际导出模型尺寸与自动倍率预测不一致。")
    sizes = dict(dimensions, longest_edge=measurement.get("longest_edge"))
    for key in SIZE_KEYS:
        if targets[key] and (type(sizes.get(key)) not in (int, float)
                             or not math.isfinite(sizes[key])
                             or sizes[key] > targets[key] + tolerance):
            raise ValueError(f"实际导出模型的 {key} 超过指定尺寸。")
    if not _same_number(sizes["longest_edge"], max(dimensions.values())):
        raise ValueError("实际导出模型的最长边与宽高深不一致。")
    return True
