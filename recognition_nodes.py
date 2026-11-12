"""Visible native source review; no model calls or fabricated JSON repair."""
import hashlib
import json
import re
from uuid import uuid4

from .core import validate_image
from .five_quality import image_hash, _text, _texts
from .quality_json import decode_record
from .recognition_contract import normalize_recognition
from .five_routes import _validate_depth
from .recognition_instructions import GROUP_RULES


def _json(record):
    return json.dumps(record, ensure_ascii=False, indent=2)


def _array(tensor):
    if tensor is None or not hasattr(tensor, "detach"):
        raise ValueError("识别复核必须连接当前原图。")
    return tensor.detach().cpu().numpy()


class BAMidFarRecognitionContext:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reference": ("IMAGE",),
                             "recognition": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "IMAGE", "STRING")
    RETURN_NAMES = ("review_prompt", "reference", "context")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, reference, recognition):
        pixels = validate_image(_array(reference))
        if not isinstance(recognition, str) or len(recognition) > 150000:
            raise ValueError("原始识别文本超过上限或类型无效。")
        size = [pixels.shape[2], pixels.shape[1]]
        normalized, issues = None, []
        try:
            normalized = normalize_recognition(recognition, size, require_groups=True)
        except ValueError as error:
            issues.append(str(error))
        context = {
            "source_hash": image_hash(pixels), "source_size": size,
            "review_id": uuid4().hex, "max_reviews": 1,
            "recognition_digest": hashlib.sha256(recognition.encode("utf8")).hexdigest(),
            "raw_recognition": recognition, "normalized_candidate": normalized, "issues": issues,
        }
        prompt = """独立对照所连接的同一张原图，重新复核五个自然组与全部点位。
原识别可能截断、含重复字段、坐标混用、把连体山群拆开或把不同深度岩体并入。
不要猜测补完截断JSON；重新看图，给出完整的三近景两远景识别。原识别仅为待核候选，
每个锚点必须在自身岩体，不取天空/邻山/遮挡物。确认岩基和连接连续、全部十对独立。
原图不足五组时如实报告，禁止假通过。此次只有一次独立复核机会。
输出包装JSON：{"source_hash":"上下文原值","review_id":"上下文原值",
"recognition":{完整识别JSON}}，必须逐字复制本次绑定值。
""" + GROUP_RULES + "\n本轮原图绑定和待核识别：" + _json(context)
        return prompt, reference, _json(context)


class BAMidFarRecognitionFinal:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reference": ("IMAGE",),
                             **{k: ("STRING", {"forceInput": True})
                                for k in ("context", "review")}}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("recognition", "review_record")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, reference, context, review):
        pixels = validate_image(_array(reference))
        binding, result = decode_record(context), decode_record(review)
        if (binding.get("source_hash") != image_hash(pixels) or
                result.get("source_hash") != binding["source_hash"] or
                result.get("review_id") != binding.get("review_id") or
                not re.fullmatch(r"[a-f0-9]{32}", str(binding.get("review_id", ""))) or
                binding.get("max_reviews") != 1 or
                hashlib.sha256(str(binding.get("raw_recognition")).encode("utf8")).hexdigest()
                != binding.get("recognition_digest")):
            raise ValueError("识别复核未绑定当前原图/原识别与本次review_id。")
        raw = normalize_recognition(result.get("recognition"),
                                    [pixels.shape[2], pixels.shape[1]], require_groups=True)
        objects = raw["objects"]
        if len(objects) != 5 or [sum(o.get("role") == r for o in objects)
                                for r in ("mountain", "range")] != [3, 2]:
            raise ValueError("分组复核不足三近两远，停止生成，不复制或补位。")
        _text(raw.get("style"), "style")
        for obj in objects:
            _validate_depth(obj)
            marks = obj["landmarks"]
            if not 3 <= len(marks) <= 20:
                raise ValueError("分组复核每路至少三个独有造型锚点。")
            for field in ("name", "shape"):
                values = [_text(mark.get(field), "造型锚点") for mark in marks]
                if len(set(values)) != len(values):
                    raise ValueError("造型锚点不能通过重复内容充数。")
            if len({tuple(mark["point"]) for mark in marks}) != len(marks):
                raise ValueError("造型锚点点位不能重复。")
            _texts(obj.get("contour_fingerprint"), "有序轮廓", 4)
            _texts(obj.get("connected_parts"), "可见真实连接")
            _text(obj.get("rhythm"), "轮廓节奏")
        record = {"status": "accepted", "source_hash": binding["source_hash"],
                  "review_id": binding["review_id"], "recognition_digest": binding["recognition_digest"],
                  "original_issues": binding["issues"], "normalization_audit":
                  raw.get("_normalization_audit", []), "max_reviews": 1, "hidden_model_calls": 0}
        raw["_recognition_review"] = record
        return _json(raw), _json(record)
