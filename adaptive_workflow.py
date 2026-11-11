"""Standalone extraction graph with an explicit blocker for each absent branch."""
import json
from pathlib import Path

try:
    from .visible_workflow import build_workflow as build_visible, _node, _slot
except ImportError:
    from visible_workflow import build_workflow as build_visible, _node, _slot


ROOT = Path(__file__).parent
RECOGNITION = (ROOT / "adaptive-recognition.txt").read_text(encoding="utf8")
PE = (ROOT / "adaptive-pe.txt").read_text(encoding="utf8")
IMAGE_INSTRUCTION = (ROOT / "adaptive-image.txt").read_text(encoding="utf8")


def _instruction(node, instruction):
    for slot in node["data"]["inputs"]:
        if slot["name"] == "user_prompt":
            slot["value"] = instruction
    node["data"]["extra"]["user_prompt"] = [
        {"type": "paragraph", "children": [{"text": line}]}
        for line in instruction.splitlines()]


def build_workflow(input_filename="026.png", filename_prefix="midfar-adaptive/asset",
                   background_mode="any"):
    graph = build_visible(input_filename, filename_prefix, background_mode)
    nodes, edges = graph["nodes"], graph["edges"]
    lookup = {node["id"]: node for node in nodes}
    background = {
        "any": "底色不限，白底、其他颜色或透明均可，不要求透明通道。",
        "white": "空白区域使用纯白背景，保留岩面自身浅色，不要求透明通道。",
        "key": "空白区域使用均匀纯色{KEY_COLOR}背景，占位符由分路节点替换；主体不能染上键色。",
    }[background_mode]
    _instruction(lookup["2"], RECOGNITION)
    _instruction(lookup["3"], PE + "\n" + background)
    lookup["2"]["data"]["title"] = "② 原图识别 · 实际1–3个完整自然组"
    lookup["3"]["data"]["title"] = "③ 独立PE · 原图峰形与真实连接"
    lookup["4"]["data"].update(type="BAMidFarAdaptivePE", title="④ 实际分路 · 空缺路留空")
    lookup["8"]["data"].update(type="BAMidFarAdaptivePNG", title="⑥ 实际图片查重 · 跳过空缺路")
    lookup["8"]["position"]["x"] = 3000
    for i in (1, 2, 3):
        model = lookup[str(i + 4)]
        model["position"]["x"] = 2400
        model["data"]["extra"]["modelConfig"]["size"] = {
            "switch": True, "value": "2016x1344"}
        _instruction(model, f"第{i}路：只执行连接的prompt_{i}。\n" +
                     IMAGE_INSTRUCTION + "\n" + background)
        gate = _node(i + 8, "BAMidFarActivePrompt", f"空路跳过 · 第{i}路", 1960,
                     (i - 1) * 760, [_slot("prompt", "STRING")],
                     [("prompt", "STRING")], ["prompt"], 350)
        nodes.append(gate)
        # Preserve native carrier metadata; only replace the prompt's source.
        edge = next(e for e in edges if e["target"] == model["id"]
                    and e["targetHandle"] == "prompt")
        edge.update(source=gate["id"], sourceHandle="prompt")
        edges.append({
            "id": f"adaptive-gate-{i}", "type": "baEdge",
            "source": "4", "sourceHandle": f"prompt_{i}",
            "target": gate["id"], "targetHandle": "prompt",
            "data": {"type": "STRING", "sourceType": "STRING", "targetType": "STRING",
                     "extra": {"type": "STRING"}},
        })
    return graph


if __name__ == "__main__":
    path = ROOT / "midfar-adaptive.byteartist.json"
    path.write_text(json.dumps(build_workflow(), ensure_ascii=False, indent=2), encoding="utf8")
