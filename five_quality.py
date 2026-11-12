"""Five current-image targets: explicit shape contracts and bound visual review.

No model is called here. Numeric checks support, but cannot replace, the visible
native visual model's comparison with the current reference.
"""
import copy
import hashlib
from itertools import combinations
import json
import math
import re
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw

try:
    from .core import validate_image
    from .five_routes import RECOGNITION, PE, SHAPE_RULES, split_five
    from .five_audit import audit_pairs
    from .selection import _box, crop_reference
    from .localization_view import localization_view
    from .quality_json import decode_record
    from .recognition_contract import normalize_pair
except ImportError:
    from core import validate_image
    from five_routes import RECOGNITION, PE, SHAPE_RULES, split_five
    from five_audit import audit_pairs
    from selection import _box, crop_reference
    from localization_view import localization_view
    from quality_json import decode_record
    from recognition_contract import normalize_pair


STRUCTURE_ASPECTS = {
    "peak_profile", "peak_count", "height_ratio", "ridge_spacing", "slope_profile",
    "shoulder_steps", "base_profile", "ridge_turns", "end_profile", "depth_relief",
}
VISUAL_CHECKS = (
    "source_identity", "unique_features", "continuous", "contour_clear", "rhythm",
    "sharpness", "complete", "depth", "no_unrelated", "source_proportions",
)

QUALITY_RECOGNITION = RECOGNITION.replace("不要写坐标，", "") + """
【本完整自动链路的额外JSON字段，以本段为准】
期望恰好三个近景mountain、两个远景range，确实不足时输出实际数及shortfall。
在原有每项目标字段上增加：
bbox=[left,top,right,bottom]，target_point=[x,y]，均为当前整张原图0..1归一化坐标；
bbox完整覆盖可见自然单元，target_point落在目标自身岩体，不能落在天空或遮挡物；
landmarks至少3项，每项{name:"本目标内唯一锚点名",shape:"具体中大尺度造型",point:[x,y]}，
point用整张原图0..1，须在bbox内。至少三个不同造型及不同点，不使用细纹充数；
contour_fingerprint为至少4项从左到右有序的真实轮廓描述字符串数组；
rhythm为具体高低、峰间距、宽厚和转折节奏的文本。
connected_parts至少一项可证明的本组岩土连接；单峰也描述坡身和自身岩基连接。
不输出示例坐标或旧图信息。全部字段必须来自当前图，不能通过填字段伪装合格。"""

QUALITY_PE = PE + """
【自动质量链路JSON契约，以本段为准】
objects逐一对应识别实际id，每项包含：
id、prompt、preserved_landmarks（完整列出识别中的所有锚点name）、
protected_connections（完整列出识别connected_parts原文）、
contour_rhythm（具体保留本路有序轮廓和高低疏密）、
sharpness_policy（固定字符串preserve_source_no_exaggeration）、
differences（对其他每个id一项{other_id,structures:[{aspect,evidence}]}）。
每项structures至少三种不同aspect，枚举可用：
peak_profile、peak_count、height_ratio、ridge_spacing、slope_profile、
shoulder_steps、base_profile、ridge_turns、end_profile、depth_relief。
evidence具体描述本路与该他路在原图中的结构差异，不能泛写“不同”。
prompt也必须完整写入所有造型锚点、保护连接段、轮廓节奏、尖锐限制和交叉排除。
成图为2048×2048白底PNG，保持原投影比例，不移除主体自身雪面，
仅清除无关环境。只输出合法JSON，不写Markdown。"""


def decode(value):
    return decode_record(value)


def _text(value, name):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 6000:
        raise ValueError(name + "必须是非空具体文本。")
    return value.strip()


def _texts(value, name, minimum=1):
    if not isinstance(value, list) or not minimum <= len(value) <= 40:
        raise ValueError(name + f"至少需要{minimum}项。")
    items = [_text(v, name) for v in value]
    if len(set(items)) != len(items):
        raise ValueError(name + "不能重复。")
    return items


def _point(value, box):
    if (not isinstance(value, list) or len(value) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in value) or
            not box[0] <= value[0] < box[2] or not box[1] <= value[1] < box[3]):
        raise ValueError("当前原图锚点必须位于bbox内，坐标使用0..1。")
    return value


def image_hash(image):
    pixels = np.ascontiguousarray(validate_image(image), dtype=np.float32)
    return hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes()).hexdigest()


def compile_plan(recognition, pe, reference, prefix, canvas_size=2048):
    pixels = validate_image(reference)
    raw, written = normalize_pair(recognition, pe, [pixels.shape[2], pixels.shape[1]])
    if raw.get("_recognition_review") is not None and (
            not isinstance(raw["_recognition_review"], dict) or
            raw["_recognition_review"].get("source_hash") != image_hash(reference)):
        raise ValueError("分组复核结果不是当前原图，禁止复用旧识别。")
    targets, entries = raw.get("objects"), written.get("objects")
    if not isinstance(targets, list) or len(targets) != 5:
        raise ValueError("需要3个近景和2个远景；当前目标不足五个，不能复制或虚构补位。")
    counts = [sum(t.get("role") == role for t in targets if isinstance(t, dict))
              for role in ("mountain", "range")]
    if counts != [3, 2]:
        raise ValueError("正式输出固定3个近景和2个远景，景别不得替换。")
    _text(raw.get("style"), "style")
    if not isinstance(entries, list) or len(entries) != 5:
        raise ValueError("五份PE必须完整。")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or
           not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", item["id"])
           for item in targets + entries):
        raise ValueError("每个目标/PE必须是对象，id为安全英文编号。")
    lookup = {e.get("id"): e for e in entries}
    if len(lookup) != 5 or set(lookup) != {t.get("id") for t in targets}:
        raise ValueError("PE实际id必须与本轮五个目标一一对应。")
    if type(canvas_size) is not int or not 64 <= canvas_size <= 4096:
        raise ValueError("画布尺寸必须是64..4096整数。")
    for target in targets:
        box = _box(target.get("bbox"))
        _point(target.get("target_point"), box)
        landmarks = target.get("landmarks")
        if not isinstance(landmarks, list) or not 3 <= len(landmarks) <= 20:
            raise ValueError("每路至少三个独有中大尺度锚点。")
        names, shapes, points = [], [], []
        for landmark in landmarks:
            if not isinstance(landmark, dict):
                raise ValueError("造型锚点需要名称、形状和原图点位。")
            names.append(_text(landmark.get("name"), "landmark name"))
            shapes.append(_text(landmark.get("shape"), "landmark shape"))
            points.append(tuple(_point(landmark.get("point"), box)))
        if min(len(set(names)), len(set(shapes)), len(set(points))) < len(landmarks):
            raise ValueError("造型锚点不能通过改名或重复坐标充数。")
        _texts(target.get("contour_fingerprint"), "有序轮廓", 4)
        _text(target.get("rhythm"), "轮廓起伏节奏")
        connections = _texts(target.get("connected_parts"), "真实岩土连接")
        if any(re.search(r"没有.{0,8}连接|无.{0,6}连通|彼此分离|互不相连|共同雪地|共同背景",
                         text) for text in connections):
            raise ValueError("缺少可证明的真实岩土连接，不能用雪地或投影连接。")
        entry = lookup[target["id"]]
        if set(_texts(entry.get("preserved_landmarks"), "PE锚点", 3)) != set(names):
            raise ValueError("PE必须保护本路全部原图锚点。")
        if set(_texts(entry.get("protected_connections"), "PE连接")) != set(connections):
            raise ValueError("PE不得漏掉不可删除连接段。")
        _text(entry.get("contour_rhythm"), "PE轮廓节奏")
        if entry.get("sharpness_policy") != "preserve_source_no_exaggeration":
            raise ValueError("PE必须明确保留原尖钝且不强化尖锐。")
        differences = entry.get("differences")
        if (not isinstance(differences, list) or len(differences) != 4 or
                any(not isinstance(d, dict) for d in differences) or
                {d.get("other_id") for d in differences} != set(lookup) - {target["id"]}):
            raise ValueError("PE必须逐一排除其他四路并证明结构差异。")
        for difference in differences:
            structures = difference.get("structures")
            if (not isinstance(structures, list) or len(structures) < 3 or
                    any(not isinstance(s, dict) for s in structures)):
                raise ValueError("每对目标至少三项独立结构差异。")
            aspects = [s.get("aspect") for s in structures]
            if len(set(aspects)) < 3 or not set(aspects) <= STRUCTURE_ASPECTS:
                raise ValueError("颜色、方位、光照、镜像和尺寸不能充当结构差异。")
            for structure in structures:
                _text(structure.get("evidence"), "结构差异原图证据")
    # Preserve the deployed identity/depth/completion checks as well.
    record = split_five(json.dumps(raw, ensure_ascii=False),
                        json.dumps(written, ensure_ascii=False), reference, prefix, "white")
    ordered = record["recognized_targets"]
    prompts = []
    for target, prompt in zip(ordered, record["execution_prompts"]):
        _, crop_info = crop_reference(reference, target["bbox"], .04)
        sw, sh = crop_info["source_size"]
        x0, y0, x1, y1 = crop_info["source_bbox_pixels"]
        local_point = [round((target["target_point"][0]*sw-x0)/(x1-x0)*1000),
                       round((target["target_point"][1]*sh-y0)/(y1-y0)*1000)]
        contract = {
            "landmarks": target["landmarks"], "contour": target["contour_fingerprint"],
            "rhythm": target["rhythm"], "connections": target["connected_parts"],
            "cross_exclusions": lookup[target["id"]]["differences"],
        }
        # Each native image gets this run's crop, without drawn markers.
        prompt = prompt.replace("当前输入是完整原图", "当前图片输入是本轮原图该目标局部裁图")
        prompts.append(prompt + "\n" + SHAPE_RULES +
                       "\n本路锁定的原图造型契约：" + json.dumps(contract, ensure_ascii=False) +
                       f"\n输出{canvas_size}×{canvas_size}，只提取该局部目标和自身真实连接，"
                       "不从裁图内选择其他深度邻山，不将原图被遮挡轮廓误当成贯穿缺口。"
                       f"\n局部图0..1000坐标中，目标自身岩体内定位点为{local_point}；"
                       "锚点契约的point仍为整张原图0..1坐标，不将两套坐标混用。"
                       "定位点仅作文字指引，不在成图上绘制点或标记。")
    record.update(schema_version=3, quality_contract="three-near-two-far-v1",
                  source_hash=image_hash(reference), canvas_size=canvas_size,
                  execution_prompts=prompts, max_retries=1,
                  normalization_audit=raw.get("_normalization_audit", []),
                  recognition_review=raw.get("_recognition_review"),
                  quality_checks=list(VISUAL_CHECKS))
    return record


def prepare_reference(reference, target, source_hash):
    if image_hash(reference) != source_hash:
        raise ValueError("当前原图source hash与本轮规划不一致，不能复用旧定位。")
    crop, info = crop_reference(reference, target["bbox"], .04)
    info.update(id=target["id"], bbox=target["bbox"],
                target_point=[p*1000 for p in target["target_point"]],
                connected_parts=target["connected_parts"],
                excluded_objects=target.get("excluded_objects", []),
                source_hash=source_hash, crop_hash=image_hash(crop))
    marked, _, _ = localization_view(crop, json.dumps(info))
    return crop, marked, info


def image_quality(image, canvas_size=2048):
    pixels = validate_image(image)
    if pixels.shape[0] != 1:
        raise ValueError("每路只接受一张真实生成图。")
    rgb = pixels[0, ..., :3]
    h, w = rgb.shape[:2]
    transparent = pixels.shape[-1] == 4 and np.any(pixels[0, ..., 3] < .99)
    if transparent:
        mask = pixels[0, ..., 3] > .05
    else:
        border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
        mask = np.max(np.abs(rgb-np.median(border, axis=0)), axis=-1) > .08
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    significant = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > max(16, h*w*.00005)]
    mask = np.isin(labels, significant)
    failures = []
    if (w, h) != (canvas_size, canvas_size):
        failures.append("画布尺寸不符合本轮要求")
    bbox, margins = None, None
    if not mask.any():
        failures.append("空图或没有可识别主体")
    else:
        ys, xs = np.where(mask)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()+1), int(ys.max()+1)]
        margins = [bbox[0]/w, bbox[1]/h, (w-bbox[2])/w, (h-bbox[3])/h]
        if min(margins) < .15 - 1/min(h, w):
            failures.append("主体裁边或四侧15%留白不足")
        if ((bbox[2]-bbox[0])/w > .7+1/w or (bbox[3]-bbox[1])/h > .7+1/h):
            failures.append("主体宽高超过画布70%")
        if transparent and len(significant) > 1:
            failures.append("实际透明通道表明本组断开为多个分离体块")
    return {"passed": not failures, "failures": failures, "bbox_pixels": bbox,
            "margins": margins, "component_count": len(significant),
            "topology_numeric": "alpha" if transparent else "diagnostic_only",
            "opaque_snow_limitation": "不透明白雪可能与底色同色，连通性须对照原图视觉检查",
            "image_hash": image_hash(pixels)}


def _thumb(image, width, height):
    pixels = validate_image(image)[0]
    rgb = pixels[..., :3]
    if pixels.shape[-1] == 4:
        rgb = rgb*pixels[..., 3:4] + 1-pixels[..., 3:4]
    picture = Image.fromarray(np.round(rgb.clip(0, 1)*255).astype(np.uint8))
    picture.thumbnail((width, height), Image.Resampling.LANCZOS)
    return picture


def inspect_images(plan, reference, images):
    plan = decode(plan)
    if plan.get("quality_contract") != "three-near-two-far-v1" or len(images) != 5:
        raise ValueError("必须使用本轮完整五路质量规划。")
    if image_hash(reference) != plan["source_hash"]:
        raise ValueError("验收原图不是本轮原图。")
    checks = [image_quality(image, plan["canvas_size"]) for image in images]
    try:
        pairs = audit_pairs(images, "any")["pairs"]
    except ValueError:
        # An empty image already fails its branch, but cannot prevent useful
        # comparisons between the remaining real images.
        pairs = []
        for a, b in combinations(range(5), 2):
            try:
                pair = audit_pairs([images[a], images[b]], "any", [a+1, b+1])["pairs"][0]
            except ValueError:
                pair = {"branches": [a+1, b+1], "status": "unavailable"}
            pairs.append(pair)
    inspection = {
        "run_id": plan["run_id"], "review_id": uuid4().hex,
        "source_hash": plan["source_hash"], "image_hashes": [c["image_hash"] for c in checks],
        "objects": [{"id": t["id"], "branch": i+1, **c}
                    for i, (t, c) in enumerate(zip(plan["recognized_targets"], checks))],
        "pairs": pairs, "pair_count": 10, "hidden_model_calls": 0,
    }
    sheet = Image.new("RGB", (1536, 2304), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 8), f"RUN {plan['run_id']}   REVIEW {inspection['review_id']}", fill="black")
    pic = _thumb(reference, 1512, 348)
    sheet.paste(pic, ((1536-pic.width)//2, 30+(348-pic.height)//2))
    for i, (target, image) in enumerate(zip(plan["recognized_targets"], images)):
        y = 384*(i+1)
        crop, _, _ = prepare_reference(reference, target, plan["source_hash"])
        draw.text((12, y+6), f"{i+1} {target['id']} {target['role']} CURRENT SOURCE CROP", fill="black")
        draw.text((780, y+6), f"{i+1} {target['id']} GENERATED ASSET", fill="black")
        for column, item in enumerate((crop, image)):
            pic = _thumb(item, 744, 350)
            sheet.paste(pic, (column*768+(768-pic.width)//2, y+28+(350-pic.height)//2))
    context = {"run_id": plan["run_id"], "review_id": inspection["review_id"],
               "targets": plan["recognized_targets"], "pe": plan["original_pe"],
               "numeric": inspection}
    prompt = """对照所连接的本轮原图/成图对照表，执行五路严格视觉验收。
顶部是完整原图，下方五行左为同一原图本路裁图、右为实际成图；不可看旧预览。
检查3近景2远景身份、至少三处独有中大尺度锚点、真实连续岩土和天然山脚、
轮廓清晰、有序轮廓高低疏密节奏、原比例、禁止额外拔尖削薄拉高及密集锯齿。
保持原图尖钝和阶梯，不能统一圆丘；远景不能变成近景。
boundary_cut=true且close_visible_rock_edge的近景岩台，只允许可见末端自然收束；
不能臆造画外台地、新峰或新连接，必须保留原图真实截断来源说明。
对全部10对判断同组拆分、主峰共享、局部包含、镜像和替身；
数值相似证据不是身份结论，review_required的配对务必逐一对照原图解释。
看不清、证据不足或缺锚点必须passed=false，不能只声明“通过”。
只输出合法JSON，run_id和review_id必须逐字使用本次上下文：
{"run_id":"本次值","review_id":"本次值","objects":[
{"id":"本路实际id","checks":{""" + ",".join(
        f'"{key}":{{"passed":true,"evidence":"具体原图及成图造型对照"}}'
        for key in VISUAL_CHECKS) + """},
"landmarks":[{"name":"识别锚点原名","preserved":true,"evidence":"在成图的具体位置/形态"}]}],
"pairs":[{"ids":["实际id1","实际id2"],"distinct":true,"evidence":"具体峰形/岩阶/连接差异及有无包含"}]}
objects必须完整5项，landmarks逐项包含本路全部原名，pairs必须完整10对且无重复；
所有布尔值必须JSON布尔，各项都写具体证据，任一失败填false。
本轮上下文：""" + json.dumps(context, ensure_ascii=False)
    return np.asarray(sheet, dtype=np.float32)[None]/255, prompt, inspection


def _bind(plan, inspection):
    ids = [t["id"] for t in plan["recognized_targets"]]
    if (inspection.get("run_id") != plan["run_id"] or
            inspection.get("source_hash") != plan["source_hash"] or
            not re.fullmatch(r"[a-f0-9]{32}", inspection.get("review_id", "")) or
            [o.get("id") for o in inspection.get("objects", [])] != ids or
            len(inspection.get("image_hashes", [])) != 5 or
            inspection.get("pair_count") != 10):
        raise ValueError("检查记录没有绑定本轮原图及五个实际目标。")
    actual = [tuple(sorted(p.get("branches", []))) for p in inspection.get("pairs", [])]
    if len(actual) != 10 or set(actual) != set(combinations(range(1, 6), 2)):
        raise ValueError("数值检查必须完整包含十对。")


def evaluate_review(plan, inspection, review):
    plan, inspection, review = decode(plan), decode(inspection), decode(review)
    _bind(plan, inspection)
    if (review.get("run_id") != plan["run_id"] or
            review.get("review_id") != inspection["review_id"]):
        raise ValueError("视觉报告run_id/review_id与实际成图检查不一致。")
    ids = [t["id"] for t in plan["recognized_targets"]]
    objects = review.get("objects")
    if (not isinstance(objects, list) or len(objects) != 5 or
            any(not isinstance(o, dict) for o in objects) or
            {o.get("id") for o in objects} != set(ids)):
        raise ValueError("视觉报告必须逐项核验五个实际目标。")
    failed, reasons = set(), {i: [] for i in range(1, 6)}
    for check in inspection["objects"]:
        if check["passed"] is not True:
            failed.add(check["branch"])
            reasons[check["branch"]].extend(check["failures"])
    for obj in objects:
        branch = ids.index(obj["id"])+1
        checks = obj.get("checks")
        if not isinstance(checks, dict) or set(checks) != set(VISUAL_CHECKS):
            raise ValueError("视觉报告缺少逐项造型门禁。")
        for key, item in checks.items():
            if not isinstance(item, dict) or type(item.get("passed")) is not bool:
                raise ValueError("每项视觉门禁必须使用真正布尔值。")
            evidence = _text(item.get("evidence"), "视觉门禁证据")
            if not item["passed"]:
                failed.add(branch)
                reasons[branch].append(key + "：" + evidence)
        landmarks = obj.get("landmarks")
        names = [p["name"] for p in plan["recognized_targets"][branch-1]["landmarks"]]
        if (not isinstance(landmarks, list) or len(landmarks) != len(names) or
                any(not isinstance(p, dict) for p in landmarks) or
                {p.get("name") for p in landmarks} != set(names)):
            raise ValueError("视觉报告必须核验全部独有锚点。")
        for landmark in landmarks:
            if type(landmark.get("preserved")) is not bool:
                raise ValueError("锚点核验必须使用真正布尔值。")
            evidence = _text(landmark.get("evidence"), "锚点证据")
            if not landmark["preserved"]:
                failed.add(branch)
                reasons[branch].append(landmark["name"] + "：" + evidence)
    pairs = review.get("pairs")
    expected = {tuple(sorted(pair)) for pair in combinations(ids, 2)}
    if not isinstance(pairs, list) or len(pairs) != 10:
        raise ValueError("视觉查重必须完整十对。")
    observed = set()
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("ids"), list):
            raise ValueError("视觉配对格式无效。")
        key = tuple(sorted(pair["ids"]))
        if key not in expected or key in observed or type(pair.get("distinct")) is not bool:
            raise ValueError("视觉配对id重复、错配或没有布尔结论。")
        observed.add(key)
        evidence = _text(pair.get("evidence"), "跨路造型差异证据")
        if not pair["distinct"]:
            for identifier in key:
                branch = ids.index(identifier)+1
                failed.add(branch)
                reasons[branch].append("跨路重复/包含：" + evidence)
    for pair in inspection["pairs"]:
        if pair["status"] == "duplicate":
            for branch in pair["branches"]:
                failed.add(branch)
                reasons[branch].append("数值查重发现整图或镜像重复")
    return {"run_id": plan["run_id"], "review_id": inspection["review_id"],
            "status": "retry_required" if failed else "accepted",
            "failed_branches": sorted(failed),
            "reasons": {str(k): v for k, v in reasons.items() if v}, "max_retries": 1}


def repair_prompts(plan, state):
    plan, state = decode(plan), decode(state)
    if state.get("run_id") != plan["run_id"]:
        raise ValueError("重试状态与本轮规划不一致。")
    return [prompt + "\n【本路唯一一次重试】保持同一原图、id、自然组和造型契约。"
            "修复以下实际验收失败，不换目标，不复制他路：\n" +
            "\n".join(state["reasons"].get(str(i+1), []))
            if i+1 in state["failed_branches"] else ""
            for i, prompt in enumerate(plan["execution_prompts"])]


def finalize(plan, inspection, review, images):
    plan, inspection = decode(plan), decode(inspection)
    state = evaluate_review(plan, inspection, review)
    if state["failed_branches"]:
        raise ValueError("五路最终验收失败，停止保存：" + json.dumps(state, ensure_ascii=False))
    if len(images) != 5 or [image_hash(i) for i in images] != inspection["image_hashes"]:
        raise ValueError("最终图片hash与实际已验收成图不一致。")
    return {"status": "accepted", "run_id": plan["run_id"], "review_id": inspection["review_id"],
            "source_hash": plan["source_hash"], "image_hashes": inspection["image_hashes"],
            "png_count": 5, "near_count": 3, "far_count": 2, "pair_count": 10,
            "hidden_model_calls": 0, "quality_checks": list(VISUAL_CHECKS),
            "recognized_targets": plan["recognized_targets"], "inspection": inspection,
            "normalization_audit": plan.get("normalization_audit", []),
            "recognition_review": plan.get("recognition_review"),
            "visual_review": decode(review)}
