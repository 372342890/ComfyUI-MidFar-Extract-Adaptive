"""Bounded complete-object decoding, without evaluating or inventing content."""
import json
import math
import re
from uuid import uuid4


NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|(?P<ratio>' + NUMBER +
                    r"\s*/\s*" + NUMBER + r")")


def _pairs(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("JSON重复字段：" + key)
        record[key] = value
    return record


def _bad_constant(value):
    raise ValueError("JSON不允许非有限数：" + value)


def _coordinate_path(path):
    return (
        len(path) == 4 and path[0] == "objects" and type(path[1]) is int and
        path[2] in ("bbox", "target_point") and type(path[3]) is int
    ) or (
        len(path) == 6 and path[0] == "objects" and type(path[1]) is int and
        path[2] == "landmarks" and type(path[3]) is int and
        path[4] == "point" and type(path[5]) is int
    )


def decode_record(value):
    if isinstance(value, dict):
        try:
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("JSON对象含无效类型或非有限数。") from error
    if not isinstance(value, str) or len(value) > 150000:
        raise ValueError("JSON记录必须是150000字符内文本或对象。")
    text = value.strip().lstrip("\ufeff").strip()
    if text.startswith("```"):
        fence = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\s*```", text)
        if not fence:
            raise ValueError("JSON代码围栏被截断。")
        text = fence[1]
    ratios, audit = {}, []
    marker = "__literal_ratio_" + uuid4().hex + "_"

    def replace(match):
        if not match.group("ratio"):
            return match[0]
        token = marker + str(len(ratios))
        ratios[token] = match[0]
        return json.dumps(token)

    text = TOKENS.sub(replace, text)
    try:
        parsed = json.loads(text, object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("需要完整合法JSON；内容截断或语法错误：" + str(error)) from error
    if not isinstance(parsed, dict):
        raise ValueError("需要完整JSON对象。")

    def walk(item, path=()):
        if isinstance(item, dict):
            return {key: walk(v, path+(key,)) for key, v in item.items()}
        if isinstance(item, list):
            return [walk(v, path+(i,)) for i, v in enumerate(item)]
        if isinstance(item, str) and item in ratios:
            if not _coordinate_path(path):
                raise ValueError("仅目标坐标允许数值字面量除法。")
            numerator, denominator = map(float, ratios[item].split("/"))
            if denominator == 0:
                raise ValueError("坐标除法分母不能为零。")
            result = numerator/denominator
            if not math.isfinite(result):
                raise ValueError("坐标数值必须有限。")
            audit.append({"operation": "literal_ratio", "path": list(path),
                          "original": ratios[item], "value": result})
            return result
        if type(item) is float and not math.isfinite(item):
            raise ValueError("JSON数值必须有限。")
        return item

    try:
        parsed = walk(parsed)
    except RecursionError as error:
        raise ValueError("JSON嵌套过深。") from error
    if audit:
        previous = parsed.get("_normalization_audit", [])
        if not isinstance(previous, list):
            raise ValueError("规范化记录必须是数组。")
        parsed["_normalization_audit"] = previous + audit
    return parsed
