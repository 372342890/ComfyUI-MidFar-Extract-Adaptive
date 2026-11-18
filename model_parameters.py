"""Parse independent face counts and whole-model multipliers from vision JSON."""
import json
import math
import re


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("分析 JSON 存在重复字段。")
        result[key] = value
    return result


def _analysis(text):
    if not isinstance(text, str) or not text.strip():
        return {}
    normalized = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n\s*```", normalized, re.DOTALL)
    if match:
        normalized = match.group(1)
    try:
        value = json.loads(normalized, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_face_count(text, default_faces=4000):
    if type(default_faces) is not int:
        raise ValueError("默认面数必须是整数。")
    value = _analysis(text).get("faces")
    if value is None and isinstance(text, str) and re.fullmatch(r"\s*\d{1,8}\s*", text):
        value = int(text)
    if type(value) is not int:
        value = default_faces
    return max(3000, min(5000, value))


def validate_scale_range(minimum, maximum):
    if (any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
            for v in (minimum, maximum)) or minimum > maximum):
        raise ValueError("缩放倍数范围必须是有限正数，且下限不能大于上限。")
    return minimum, maximum


def scale_decision(text, minimum=100, maximum=300):
    validate_scale_range(minimum, maximum)
    analysis = _analysis(text)
    value = analysis.get("scale_factor")
    result = {"scale_range": [minimum, maximum], "planned_faces": parse_face_count(text)}
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        result.update(scale_factor=minimum + (maximum - minimum) / 2,
                      recommended_scale_factor=None, source="fallback",
                      reason="倍数建议缺失或无效，使用指定范围的中点。")
        return result
    factor = max(minimum, min(maximum, value))
    reason = analysis.get("scale_reason")
    reason = reason.strip()[:400] if isinstance(reason, str) and reason.strip() else "按当前山岩整体体量判断。"
    result.update(scale_factor=factor, recommended_scale_factor=value,
                  source="vision" if factor == value else "clamped",
                  reason=reason if factor == value else reason + " 建议超出范围，已限制到指定边界。")
    return result
