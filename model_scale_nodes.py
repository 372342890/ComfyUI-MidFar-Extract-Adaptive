"""Measure each active original GLB and choose its final uniform multiplier."""
import hashlib
import json

from .glb_audit import inspect_glb
from .model_audit_nodes import BAMidFarModelAudit, _active_routes, _blocker, _model_path
from .model_scale import calculate_size_scale, normalize_targets


class BAMidFarModelSizePlan:
    @classmethod
    def INPUT_TYPES(cls):
        def size(default, tooltip):
            return ("FLOAT", {"default": default, "min": 0.0, "max": 1e9, "step": 0.1,
                              "tooltip": tooltip + "；单位为模型坐标单位，0 表示关闭。"})
        return {
            "required": {
                "up_axis": (["Y", "Z"], {"default": "Y"}),
                "target_longest_edge": size(10.0, "指定最终最长边"),
                "target_width": size(0.0, "指定最终宽度上限"),
                "target_height": size(0.0, "指定最终高度上限"),
                "target_depth": size(0.0, "指定最终深度上限"),
            },
            "optional": {
                **{f"glb_{i}": ("STRING", {"forceInput": True, "lazy": True}) for i in (1, 2, 3)},
                "route_report": ("STRING", {"forceInput": True}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("FLOAT", "FLOAT", "FLOAT", "STRING")
    RETURN_NAMES = ("scale_factor_1", "scale_factor_2", "scale_factor_3", "report")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Adaptive"
    DESCRIPTION = (
        "读取各路真实GLB尺寸，按指定最终尺寸自动计算等比缩放倍数。"
        "只填一个尺寸时达到该尺寸；同时填写多个尺寸时等比适配全部上限。"
        "0关闭对应尺寸，至少一个正数；空路跳过，报告接入模型验收。")

    def check_lazy_status(self, glb_1=None, glb_2=None, glb_3=None, route_report=None,
                          prompt=None, unique_id=None, **kwargs):
        models = (glb_1, glb_2, glb_3)
        active = _active_routes(route_report, models, prompt, unique_id)
        return [f"glb_{i}" for i in active if models[i - 1] is None]

    IS_CHANGED = classmethod(BAMidFarModelAudit.IS_CHANGED.__func__)

    def run(self, glb_1=None, glb_2=None, glb_3=None, route_report=None, up_axis="Y",
            target_longest_edge=10.0, target_width=0.0, target_height=0.0, target_depth=0.0,
            prompt=None, unique_id=None):
        if up_axis not in ("Y", "Z"):
            raise ValueError("up_axis 必须是 Y 或 Z。")
        targets = normalize_targets({"longest_edge": target_longest_edge, "width": target_width,
                                     "height": target_height, "depth": target_depth})
        models = (glb_1, glb_2, glb_3)
        active = _active_routes(route_report, models, prompt, unique_id)
        slots, outputs, sources = [None] * 3, [], set()
        for branch in active:
            try:
                path = _model_path(models[branch - 1])
            except ValueError as exc:
                raise ValueError(f"路 {branch}：{exc}") from exc
            if path in sources:
                raise ValueError(f"路 {branch} 重复接线：多路连接了同一模型文件。")
            sources.add(path)
            data = path.read_bytes()
            decision = calculate_size_scale(inspect_glb(data, up_axis), targets)
            decision.update(branch=branch, source_glb=str(path),
                            source_sha256=hashlib.sha256(data).hexdigest())
            outputs.append(decision)
            slots[branch - 1] = decision["scale_factor"]
        report = {
            "node": "BAMidFarModelSizePlan", "schema_version": 1, "status": "success",
            "up_axis": up_axis, "units": "model_units", "targets": targets,
            "active_branches": active, "skipped_branches": [i for i in (1, 2, 3) if i not in active],
            "outputs": outputs,
        }
        text = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
        result = tuple(v if v is not None else _blocker() for v in slots) + (text,)
        return {"ui": {"text": [text]}, "result": result}
