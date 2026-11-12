"""Compose five native image branches without changing deployed node contracts."""
import copy
import json
from pathlib import Path
import re
from uuid import uuid4

try:
    from .visible_pipeline import split_prompts
    from .visible_workflow import build_workflow, _node, _slot
    from .autonomous_plan import _decode
    from .objects import object_filename
    from .selection import parse_selection
except ImportError:
    from visible_pipeline import split_prompts
    from visible_workflow import build_workflow, _node, _slot
    from autonomous_plan import _decode
    from objects import object_filename
    from selection import parse_selection

CHECK_GROUPS = ((1, 2, 3), (1, 4, 5), (2, 4, 5), (3, 4, 5))

RECOGNITION = """你是游戏场景原图资产识别助手。本轮需求：三个山体（近景），加两个远景山脉，统一五路提取并跨路查重。
只观察当前原图，分别盘点近景与远景，不预设实际够数。
山体：前三路是靠近场景前方的近景山岩资产，包含山峰、連体山群和具有独立雪顶/岩阶轮廓的宽岩台，当前节点距离层记为midground。不同位置、不同自然单元；不要求每路都是孤立山峰。禁止选background远景山体或远景独立小山补位。
山脉：最多两个远处连续的多峰山脊或长山脉，具有自身可见连续岩土/雪脊连接和自然两端；保留远景低对比蓝灰色空气透视，不能拿前两类山体扩大画布冒充山脉。
先统一列出自然单元，再分为role=mountain或range；同一unit_id只能使用一次。前后重叠不同深度不算连接；同一连续双峰、主峰与岩肩必须整组作为一个单元。山脉不能包含前三路山体，两个山脉不能共享同一段主峰。位置、轮廓、峰数、天然连接、岩阶和色调必须分别描述。
不要提取前景散石、科考站、卫星天线、地面托盘、整个雪原、天空、左右被画面截断的整层背景。可见近景岩台只有末端被边缘截到时，可以限定为可见岩台资产，仅自然收束截断末端，不延展整层台地或新添峰。此类目标kind=rock、boundary_cut=true、completion_policy="close_visible_rock_edge"，不得伪报完整原图边界。可以补被挡住的短自然山脚，不能臆造画面外整条山脉。若某类确实没有可见独立资产，输出实际数量并说明缺口，不能拆份或换编号凑数。
输出合法JSON对象：style、shortfall、objects。objects先最多3个mountain，再最多2个range。
每项包含id、unit_id（安全英文编号）、name、role（mountain/range）、kind（mountain或rock）、layer（mountain必须midground，range必须background）、location、appearance、occlusion、connected_parts、excluded_objects、is_single、suitable、boundary_cut、completion_policy。
location描述在完整原图中的唯一位置与邻近参照，appearance详细描述原图可见峰形与相连轮廓；connected_parts和excluded_objects是字符串数组。完整自然单元is_single=true、suitable=true、boundary_cut=false。不要写坐标，不要输出Markdown或注释。"""

PE = """你是独立PE。输入为本轮完整原图和统一五路识别JSON。
对每个实际id只写一份具体提取提示词，不能增删id或换目标；最多三个近景山体和两个远景山脉。前三路只能来自midground近景，后两路只能来自background远景，不得用远景小山补第三路。
每路明确原图唯一位置、主峰/次峰数量、尖钝、峰间高差与距离、左右坡形、岩肩、连续岩基、雪脊连接、岩阶、岩面颜色。保持原图峰尖和结构连续性，禁止将连体山群拆峰，也禁止用同一主峰的镜像、光照变体、局部裁片充当另一路。
近景提取对应山峰/连体山群/独立岩台整个可见自然单元。kind=rock且completion_policy=close_visible_rock_edge的近景岩台仅允许收束被原图边缘截到的末端，保留可见平宽雪顶、纵向岩阶和轮廓，不增加峰、不复制邻山、不延展画外台地。山脉提取远处该条连续多峰山脊，保留长横向轮廓、远景低对比蓝灰色空气透视，不能重新画成近景高对比孤峰。移除天空、雪原环境、科考站、散石和不同深度邻山；只补短自然山脚，不能添加新峰/场外山脉。
每路写清需排除的其他全部id和它们的可见位置，尤其禁止山脉与山体局部包含、两条远景山脉共享峰段。
主体完整居中，宽高不超过画布70%，四侧至少15%空白。白底，保留自身白色雪面；不画外部雾气、地面托盘、棋盘格、文字或水印。
只输出合法JSON：{"objects":[{"id":"实际识别id","prompt":"本路完整中文PE"}]}，数量与识别实际目标一致，不写Markdown。"""

SCENE_026 = """【026原图人工复核的定位纠正，逐项对照原图，不可违背可见事实】
前一轮识别把左侧低峰与中央高尖峰拆开，这是错误的；两者之间有连续雪坡和岩肩，必须整组提取，包含中央前方小凸峰和右肩。不要描述为两座圆钝主峰，保留中央更高的尖锐峰顶与左低峰的高差。
右侧中景尖峰和左中央大山群之间是雪谷，不是同一岩体；右侧尖峰前下方的横向宽岩台位于另一深度，可见雪谷分隔，不要连同被右边界裁断的横向整层台地一起提取。仅提取完整可见的右侧尖峰及其自身相连岩肩。
第三路选择右前方、右侧尖峰前下方、天线右侧的横向覆雪岩台：上沿平宽、厚雪顶、下方深灰蓝色纵向岩阶，前下方附着小岩阶，与尖峰以不同深度雪谷区分。它是近景岩台资产而非孤立山峰，kind=rock、layer=midground、role=mountain。右端被原图边缘截到，必须boundary_cut=true、completion_policy="close_visible_rock_edge"；仅将可见岩台末端自然收束，不推演画外延伸，不添新峰，不并入右下方另一个截断岩块。画面右上方小雪山是远景，绝不能作为第三路；左前岩体与第一路相连，绝不能拆出补位。前三路使用m1/m2/m3，分别为连体山群、独立尖峰、横向岩台。
两路远景山脉应选择画面上方图内具有真实连续峰脊和自然两端的两个完整自然组：检查上方偏左的最高尖雪峰及其自身左肩/右侧低峰连接；检查上方偏右的大雪峰及其自身相连起伏峰脊和宽缓台地。它们和右上独立小尖锥山是不同深度/不同自然单元。不要把从左边界到右边界的全部远山切成左右两段，也不要并入更后方互不连通的天际线小峰。
每个id用双引号英文字符串m1/m2/m3/r1/r2，unit_id也用英文字符串。若复核发现某个候选并非真正连续或完整，换选真实完整单元或报告缺口；不可凭上述文字臆造原图没有的连接。"""

SHAPE_RULES = """
【恢复原PE造型限制】至少三处可直接对照原图核实的独有中大尺度锚点，
不能用细碎纹理、小石头或噪点充数。按左端→左肩→主高点→鞍部/凹口→
次高点/岩阶→右端描述有序轮廓；没有的项写无，不固定峰数。
保留主次高点比例、峰间距、陡缓坡、凸肩岩阶、两端收势及高低疏密节奏，
禁止泛化为圆包、单坡或均匀重复峰列。
本组岩土连接必须有原图证据，雪地、共同背景、遮挡或投影重叠不能算连接。
不可删除连接段：相接山肩、低肩、鞍部下方岩土、共有岩基和天然山脚；
不挖穿背景缝，不断脊、不悬空、不切散，不焊接原本分離的邻山。
保留原图实际尖钝、宽厚根部和真实棱角：不拔尖、不拉高、不削薄、
不强化密集锯齿或细长针柱，不统一圆丘化。
逐一对其他四路至少写三项中大尺度结构差异；位置、颜色、光照、镜像、
整体尺寸和细纹不能算独立结构差异。不共享主峰、不拆同组、不局部包含。
成图缺少任一锚点、轮廓不清晰、没有起伏节奏、真实连接断开或被额外拔尖，
都判本路失败，重新提取同一目标。完整居中，宽高各不超过画布70%，
四侧至少15%空白，保持原投影宽高比，仅整体等比缩放。"""

RECOGNITION += SHAPE_RULES
PE += SHAPE_RULES


def _instruction(node, text):
    for inp in node["data"]["inputs"]:
        if inp["name"] == "user_prompt":
            inp["value"] = text
    node["data"]["extra"]["user_prompt"] = [
        {"type": "paragraph", "children": [{"text": line}]} for line in text.splitlines()]


def _validate_depth(target):
    role = target.get("role")
    if role not in ("mountain", "range"):
        raise ValueError("目标role必须是mountain或range。")
    if role == "mountain" and target.get("layer") != "midground":
        raise ValueError("前三路山体必须来自近景midground，禁止远景山体补位。")
    if role == "range" and target.get("layer") != "background":
        raise ValueError("山脉必须来自原图远景background。")


def build_planning(input_filename="026.png", scene_correction=False):
    graph = build_workflow(input_filename, "midfar-five/asset", "white")
    graph["nodes"] = graph["nodes"][:3]
    graph["edges"] = graph["edges"][:3]
    recognition = RECOGNITION + ("\n" + SCENE_026 if scene_correction else "")
    for node, text, title in zip(graph["nodes"][1:], (recognition, PE),
                                 ("五路统一识别 · 三个山体（近景）＋两个远景山脉",
                                  "五路独立PE · 全局排除与峰形连续")):
        _instruction(node, text)
        node["data"]["title"] = title
    return graph


def split_five(recognition, pe, reference, prefix, background_mode="white"):
    raw = _decode(recognition, "objects")
    targets = raw["objects"]
    entries = _decode(pe, "objects")["objects"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 5:
        raise ValueError("需要1–5个真实目标，不虚构补位。")
    if not isinstance(entries, list):
        raise ValueError("PE必须是实际目标列表。")
    ids, units, identities = set(), set(), set()
    groups = {"mountain": [], "range": []}
    for target in targets:
        _validate_depth(target)
        role = target.get("role")
        identifier = target.get("id")
        unit = str(target.get("unit_id", "")).strip().casefold()
        identity = tuple(re.sub(r"[\W_]+", "", str(target.get(k, "")).casefold())
                         for k in ("layer", "location", "appearance"))
        if identifier in ids or unit in units or identity in identities:
            raise ValueError("五路存在重复自然单元或位置外观，禁止跨类型拆份。")
        ids.add(identifier)
        units.add(unit)
        identities.add(identity)
        groups[role].append(target)
    if len(groups["mountain"]) > 3 or len(groups["range"]) > 2:
        raise ValueError("最多三个山体和两个远景山脉。")
    if (len(entries) != len(ids) or
            {entry.get("id") for entry in entries} != ids):
        raise ValueError("PE必须逐项对应全部实际目标，不能漏目标或增加目标。")
    prompts, slots, ordered = [], [], []
    key = None
    for role, start in (("mountain", 1), ("range", 4)):
        group = groups[role]
        if not group:
            continue
        subset = {"objects": [entry for entry in entries if entry.get("id") in
                               {target["id"] for target in group}]}
        validated = split_prompts(json.dumps({"objects": group}, ensure_ascii=False),
                                  json.dumps(subset, ensure_ascii=False),
                                  reference, prefix, background_mode)
        key = validated[3]
        for index, target in enumerate(group):
            excluded = "；".join(
                f"id={other['id']}「{other['name']}」，位置：{other['location']}"
                for other in targets if other["id"] != target["id"])
            prompts.append(validated[index] +
                           "\n五路全局排除：" + excluded +
                           "。禁止同组拆峰、镜像替代、局部包含或重复峰段。")
            slots.append(start + index)
            ordered.append(target)
    run_id = uuid4().hex
    prefixes = [object_filename(t, prefix + "/run_" + run_id) for t in ordered]
    pairs = [[a, b] for i, a in enumerate(slots) for b in slots[i + 1:]]
    actual = {"near_mountains": len(groups["mountain"]), "far_ranges": len(groups["range"])}
    required = {"near_mountains": 3, "far_ranges": 2}
    return {"schema_version": 2, "status": "ready", "run_id": run_id,
            "source_image_modified": False, "key_color": key,
            "background_mode": background_mode, "target_count": len(ordered),
            "mountain_count": len(groups["mountain"]), "range_count": len(groups["range"]),
            "required_counts": required, "actual_counts": actual,
            "missing_counts": {k: required[k] - actual[k] for k in required},
            "requirement_satisfied": actual == required,
            "active_branches": slots, "skipped_branches": [i for i in range(1, 6) if i not in slots],
            "recognized_targets": ordered, "execution_prompts": prompts,
            "prefixes": prefixes, "pairs": pairs, "pair_count": len(pairs),
            "original_recognition": raw, "original_pe": _decode(pe, "objects"),
            "handoff": "validated explicit prompts; original image goes to each native model",
            "hidden_model_calls": 0}


def build_execution(record, input_filename="026.png"):
    for target in record["recognized_targets"]:
        _validate_depth(target)
    templates = json.loads(Path(__file__).with_name("visible_native_templates.json").read_text())
    nodes = [_node(1, "LoadImage", "五路原图 · 026", 0, 800,
                   [{"name": "image", "label": "image", "type": "COMBO",
                     "value": input_filename}], [("IMAGE", "IMAGE"), ("MASK", "MASK")],
                   ["IMAGE"], 350)]
    edges = []

    def connect(source, output, target, input_name):
        node = next(n for n in nodes if n["id"] == str(target))
        inp = next(s for s in node["data"]["inputs"] if s["name"] == input_name)
        inp.pop("value", None)
        inp["format"] = "slot"
        typ = inp["type"]
        edges.append({"id": f"five-edge-{len(edges)}", "type": "baEdge",
                      "source": str(source), "sourceHandle": output,
                      "target": str(target), "targetHandle": input_name,
                      "data": {"type": typ, "sourceType": typ, "targetType": typ,
                               "extra": {"type": typ}}})

    slots = record["active_branches"]
    prefix_by_slot = dict(zip(slots, record["prefixes"]))
    output_by_slot = {}
    def string_node(identifier, title, value, y):
        nodes.append(_node(identifier, "PrimitiveString", title, -430, y,
                           [{"name": "value", "label": "value", "type": "STRING",
                             "value": value}], [("STRING", "STRING")], [], 350))

    string_node(10, "五路统一白底检查", record["key_color"], 0)
    for slot in slots:
        string_node(10 + slot, f"第{slot}路输出路径", prefix_by_slot[slot], slot * 200)
    if len(slots) < 5:
        string_node(16, "缺路空路径", "", 1200)
    for slot, target, prompt in zip(slots, record["recognized_targets"],
                                    record["execution_prompts"]):
        node = copy.deepcopy(templates["image"])
        node.update(id=str(slot + 1), position={"x": 470, "y": (slot - 1) * 720},
                    width=440, initialWidth=440, initialHeight=620, selected=False)
        kind = "近景山体" if slot <= 3 else "远景山脉"
        node["data"]["title"] = f"五路生成{slot} · {kind} · {target['name']}"
        _instruction(node, prompt)
        nodes.append(node)
        bbox = record.get("source_localizations", {}).get(target["id"])
        derived_crop = record.get("derived_source_crops", {}).get(target["id"])
        if derived_crop is not None:
            crop_id = 200 + slot
            nodes.append(_node(
                crop_id, "LoadImage", f"第{slot}路同一原图派生局部 · {target['name']}",
                30, (slot - 1) * 720,
                [{"name": "image", "label": "image", "type": "COMBO",
                  "value": derived_crop}], [("IMAGE", "IMAGE"), ("MASK", "MASK")],
                ["IMAGE"], 350))
            connect(crop_id, "IMAGE", slot + 1, "image")
        elif bbox is not None:
            localized = dict(target, bbox=bbox, selection_reason="原图复核定位，避免同类邻山",
                             asset_unit="connected_formation")
            plan = {"style": record["original_recognition"].get("style", "原图3D风格"),
                    "objects": [localized]}
            parse_selection(json.dumps(plan, ensure_ascii=False))
            crop_id = 100 + slot
            nodes.append(_node(
                crop_id, "BAMidFarSelectedAsset", f"第{slot}路原图局部定位",
                30, (slot - 1) * 720,
                [{"name": "plan", "label": "plan", "type": "STRING",
                  "value": json.dumps(plan, ensure_ascii=False)},
                 _slot("reference", "IMAGE"),
                 {"name": "key_mode", "label": "key_mode", "type": "COMBO", "value": "auto"},
                 {"name": "filename_prefix", "label": "filename_prefix", "type": "STRING",
                  "value": prefix_by_slot[slot] + "_定位"},
                 {"name": "crop_context", "label": "crop_context", "type": "FLOAT", "value": 0.}],
                [("prompt", "STRING"), ("reference_crop", "IMAGE"), ("key_color", "STRING"),
                 ("filename_prefix", "STRING"), ("manifest", "STRING")], ["manifest"], 380))
            connect(1, "IMAGE", crop_id, "reference")
            connect(crop_id, "reference_crop", slot + 1, "image")
        else:
            connect(1, "IMAGE", slot + 1, "image")
        output_by_slot[slot] = (slot + 1, "image")

    # Cover each pair when fewer than five targets exist too. Empty slots are
    # absent nodes, so they cannot cause model calls or fabricated output.
    checks = [tuple(s for s in group if s in slots) for group in CHECK_GROUPS]
    checks = list(dict.fromkeys(group for group in checks if group))
    latest = dict(output_by_slot)
    for index, group in enumerate(checks):
        identifier = 20 + index
        inputs = [_slot(f"image_{i}", "IMAGE") for i in range(1, 4)]
        inputs += [_slot("key_color", "STRING")]
        inputs += [_slot(f"prefix_{i}", "STRING") for i in range(1, 4)]
        node = _node(identifier, "BAMidFarAdaptivePNG",
                     f"五路查重{index + 1} · 配对组{''.join(map(str, group))}",
                     1050 + index * 500, 800, inputs,
                     [("rgba_1", "IMAGE"), ("rgba_2", "IMAGE"), ("rgba_3", "IMAGE"),
                      ("quality_record", "STRING")],
                     ["rgba_1", "rgba_2", "rgba_3", "quality_record"], 450)
        nodes.append(node)
        connect(10, "STRING", identifier, "key_color")
        for position in range(1, 4):
            prefix_node = 10 + group[position - 1] if position <= len(group) else 16
            connect(prefix_node, "STRING", identifier, f"prefix_{position}")
        # Dependencies carry checked images forward. All five model nodes
        # stay independent; executing the last collector includes every check.
        for position, slot in enumerate(group, 1):
            source, output = latest[slot]
            connect(source, output, identifier, f"image_{position}")
        for position, slot in enumerate(group, 1):
            latest[slot] = (identifier, f"rgba_{position}")
    return {"name": "ba-clipboard", "version": 1, "nodes": nodes, "edges": edges}
