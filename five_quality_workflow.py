"""One current upload, five independent native generations and one retry limit."""
import copy
import json
from pathlib import Path

try:
    from .five_quality import QUALITY_RECOGNITION, QUALITY_PE
    from .five_routes import _instruction
    from .visible_workflow import _node, _slot
except ImportError:
    from five_quality import QUALITY_RECOGNITION, QUALITY_PE
    from five_routes import _instruction
    from visible_workflow import _node, _slot


def build_quality_workflow(input_filename="reference.png", filename_prefix="midfar-five-quality"):
    templates = json.loads(Path(__file__).with_name("visible_native_templates.json").read_text())
    nodes, edges = [], []

    def custom(identifier, kind, title, x, y, inputs, outputs, previews=()):
        nodes.append(_node(identifier, kind, title, x, y, inputs, outputs, list(previews), 420))

    def native(template, identifier, title, x, y, instruction):
        node = copy.deepcopy(templates[template])
        node.update(id=str(identifier), position={"x": x, "y": y},
                    width=420, initialWidth=420, initialHeight=650, selected=False)
        node["data"]["title"] = title
        _instruction(node, instruction)
        if template != "image":
            node["data"]["extra"]["model"]["max_tokens"] = 16000
            node["data"]["extra"]["modelConfig"]["max_tokens"] = {
                "switch": True, "value": 16000}
        nodes.append(node)

    def connect(source, output, target, input_name):
        a = next(n for n in nodes if n["id"] == str(source))
        b = next(n for n in nodes if n["id"] == str(target))
        out = next(s for s in a["data"]["outputSlots"] if s["name"] == output)
        inp = next(s for s in b["data"]["inputs"] if s["name"] == input_name)
        assert out["type"] == inp["type"], (source, output, target, input_name)
        inp.pop("value", None)
        inp["format"] = "slot"
        edges.append({
            "id": f"quality-{len(edges)}", "type": "baEdge",
            "source": str(source), "sourceHandle": output,
            "target": str(target), "targetHandle": input_name,
            "data": {"type": out["type"], "sourceType": out["type"],
                     "targetType": inp["type"], "extra": {"type": out["type"]}},
        })

    custom(1, "LoadImage", "① 上传一张原图 · 三个近景＋两个远景", 0, 1100,
           [{"name": "image", "label": "原图", "type": "COMBO", "value": input_filename}],
           [("IMAGE", "IMAGE"), ("MASK", "MASK")], ("IMAGE",))
    native("recognition", 2, "② 本轮统一识别 · 五个自然组与造型锚点",
           460, 1100, QUALITY_RECOGNITION)
    native("pe", 3, "③ 原PE约束 · 连续轮廓/节奏/不拔尖/全局排除",
           940, 1100, QUALITY_PE)
    custom(4, "BAMidFarFivePlan", "④ 严格准入 · 必须三近两远且各有至少三锚点",
           1420, 1100,
           [_slot("reference", "IMAGE"), _slot("recognition", "STRING"), _slot("pe", "STRING"),
            {"name": "filename_prefix", "label": "输出目录", "type": "STRING",
             "value": filename_prefix}],
           [(f"prompt_{i}", "STRING") for i in range(1, 6)] +
           [(f"target_{i}", "STRING") for i in range(1, 6)] + [("plan", "STRING")],
           ("plan",))
    connect(1, "IMAGE", 2, "image")
    connect(1, "IMAGE", 3, "image")
    connect(2, "text", 3, "text")
    connect(1, "IMAGE", 4, "reference")
    connect(2, "text", 4, "recognition")
    connect(3, "text", 4, "pe")
    instruction = (
        "严格使用本路连接的完整PE，从当前原图动态局部参考提取同一id的整个自然组。"
        "至少三处锚点、真实连接段、轮廓节奏和尖钝保持原图，不拔尖、不拆组。"
        "白底，完整居中，四侧15%空白，保持比例；输出一张2048×2048 PNG。")
    for i in range(1, 6):
        y = (i-1)*650
        ref, image, gate, retry = 9+i, 14+i, 19+i, 24+i
        depth = "近景" if i <= 3 else "远景"
        custom(ref, "BAMidFarFiveReference", f"当前原图定位{i} · {depth}动态裁图",
               1900, y, [_slot("reference", "IMAGE"), _slot("target", "STRING")],
               [("crop", "IMAGE"), ("marked_preview", "IMAGE"), ("source_record", "STRING")],
               ("marked_preview", "source_record"))
        native("image", image, f"首轮生成{i} · {depth}独立提取", 2380, y, instruction)
        custom(gate, "BAMidFarActivePrompt", f"重试开关{i} · 合格路跳过",
               3900, y, [_slot("prompt", "STRING")], [("prompt", "STRING")])
        native("image", retry, f"失败路重试{i} · {depth}同一目标最多一次",
               4380, y, instruction)
        connect(1, "IMAGE", ref, "reference")
        connect(4, f"target_{i}", ref, "target")
        connect(ref, "crop", image, "image")
        connect(4, f"prompt_{i}", image, "prompt")
        connect(ref, "crop", retry, "image")
        connect(gate, "prompt", retry, "prompt")

    inspect_inputs = [_slot("reference", "IMAGE"), _slot("plan", "STRING")] + [
        _slot(f"image_{i}", "IMAGE") for i in range(1, 6)]
    inspect_outputs = [("review_sheet", "IMAGE"), ("review_prompt", "STRING"),
                       ("inspection", "STRING")]
    custom(30, "BAMidFarFiveInspect", "⑤ 首轮数值检查 · 十对查重与原图对照",
           2860, 1100, copy.deepcopy(inspect_inputs), inspect_outputs,
           ("review_sheet", "inspection"))
    native("pe", 31, "⑥ 首轮视觉验收 · 五图造型与十对身份核验",
           3340, 1100, "按连接的本轮检查提示词和原图对照表逐项验收，只输出完整JSON。")
    custom(32, "BAMidFarFiveRetry", "⑦ 验收反馈 · 仅失败路产生重试提示词",
           3820, 3400, [_slot(k, "STRING") for k in ("plan", "inspection", "review")],
           [(f"retry_{i}", "STRING") for i in range(1, 6)] + [("state", "STRING")],
           ("state",))
    custom(33, "BAMidFarFiveSelect", "⑧ 条件选图 · 保留合格路/只取失败路重试",
           4860, 1100, [_slot("state", "STRING")] + [
               _slot(f"{stem}_{i}", "IMAGE") for stem in ("image", "retry") for i in range(1, 6)],
           [(f"image_{i}", "IMAGE") for i in range(1, 6)])
    custom(34, "BAMidFarFiveInspect", "⑨ 有重试才运行 · 最终五图数值检查",
           5340, 2100, copy.deepcopy(inspect_inputs), inspect_outputs,
           ("review_sheet", "inspection"))
    native("pe", 35, "⑩ 有重试才运行 · 第二轮完整视觉验收",
           5820, 2100, "按连接的本轮检查提示词和原图对照表逐项验收，只输出完整JSON。")
    custom(36, "BAMidFarFiveFinal", "⑪ 最终门禁 · 全部合格才输出三近两远五张PNG",
           6300, 1100,
           [_slot(k, "STRING") for k in ("plan", "first_inspection", "first_review", "state",
                                        "final_inspection", "final_review")] +
           [_slot(f"image_{i}", "IMAGE") for i in range(1, 6)],
           [(f"image_{i}", "IMAGE") for i in range(1, 6)] + [("quality_record", "STRING")],
           tuple(f"image_{i}" for i in range(1, 6)) + ("quality_record",))

    for inspector, reviewer in ((30, 31), (34, 35)):
        connect(1, "IMAGE", inspector, "reference")
        connect(4, "plan", inspector, "plan")
        connect(inspector, "review_sheet", reviewer, "image")
        connect(inspector, "review_prompt", reviewer, "text")
    connect(4, "plan", 32, "plan")
    connect(30, "inspection", 32, "inspection")
    connect(31, "text", 32, "review")
    connect(32, "state", 33, "state")
    connect(4, "plan", 36, "plan")
    connect(30, "inspection", 36, "first_inspection")
    connect(31, "text", 36, "first_review")
    connect(32, "state", 36, "state")
    connect(34, "inspection", 36, "final_inspection")
    connect(35, "text", 36, "final_review")
    for i in range(1, 6):
        connect(14+i, "image", 30, f"image_{i}")
        connect(32, f"retry_{i}", 19+i, "prompt")
        connect(14+i, "image", 33, f"image_{i}")
        connect(24+i, "image", 33, f"retry_{i}")
        connect(33, f"image_{i}", 34, f"image_{i}")
        connect(33, f"image_{i}", 36, f"image_{i}")
    return {"name": "ba-clipboard", "version": 1, "nodes": nodes, "edges": edges}
