"""Adaptive extraction nodes: block absent prompts and collect real images lazily."""
import json

import torch

from .image_handoff import _diagnostic_path
from .visible_batch import process_images
from .visible_nodes import BAMidFarVisiblePE, BAMidFarVisibleThreePNG, _preview


def _blocker():
    from comfy_execution.graph import ExecutionBlocker
    return ExecutionBlocker(None)


def _active_prefixes(prefixes):
    if not all(isinstance(prefix, str) for prefix in prefixes):
        raise ValueError("输出路径必须是文本，缺少的尾部路使用空字符串。")
    active = [prefix for prefix in prefixes if prefix.strip()]
    if (not active or list(prefixes) != active + [""] * (3 - len(active))):
        raise ValueError("实际连接路必须从第1路连续排列，缺少的尾部路留空。")
    if len(set(active)) != len(active):
        raise ValueError("实际输出路径重复，不能覆盖同一素材。")
    return active


class BAMidFarAdaptivePE(BAMidFarVisiblePE):
    CATEGORY = "MidFar Extract/Adaptive"
    DESCRIPTION = "Validates 1–3 real targets; absent trailing prompts and paths are empty."


class BAMidFarActivePrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"prompt": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Adaptive"
    DESCRIPTION = "Empty target prompts block downstream generation without a placeholder."

    def run(self, prompt):
        if not isinstance(prompt, str):
            raise ValueError("Prompt must be text.")
        return (prompt,) if prompt.strip() else (_blocker(),)


class BAMidFarAdaptivePNG(BAMidFarVisibleThreePNG):
    @classmethod
    def INPUT_TYPES(cls):
        inputs = super().INPUT_TYPES()
        for i in (1, 2, 3):
            inputs["required"][f"image_{i}"] = ("IMAGE", {"lazy": True})
        return inputs

    CATEGORY = "MidFar Extract/Adaptive"
    DESCRIPTION = "Requests and saves only 1–3 active images; missing outputs are blockers."

    def check_lazy_status(self, image_1=None, image_2=None, image_3=None, key_color="any",
                          prefix_1="", prefix_2="", prefix_3=""):
        prefixes = _active_prefixes((prefix_1, prefix_2, prefix_3))
        images = (image_1, image_2, image_3)
        return [f"image_{i+1}" for i in range(len(prefixes)) if images[i] is None]

    def run(self, image_1=None, image_2=None, image_3=None, key_color="any",
            prefix_1="", prefix_2="", prefix_3=""):
        prefixes = _active_prefixes((prefix_1, prefix_2, prefix_3))
        active = (image_1, image_2, image_3)[:len(prefixes)]
        if any(item is None or not hasattr(item, "detach") for item in active):
            raise ValueError("实际目标路缺少生成图片，不能静默跳过。")
        sources = [item.detach().cpu().numpy() for item in active]
        outputs, qualities = process_images(sources, key_color)
        import folder_paths
        directory = folder_paths.get_output_directory()
        # Validate all paths before the first image is saved.
        for prefix in prefixes:
            _diagnostic_path(prefix, ".png", directory)
        for output, quality, prefix in zip(outputs, qualities, prefixes):
            quality.update(png=_preview(output, prefix, ".png"), filename_prefix=prefix)
            _diagnostic_path(prefix, "_内容检查.json", directory).write_text(
                json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf8")
        count = len(outputs)
        record = json.dumps({
            "status": "warning" if any(q["status"] == "warning" for q in qualities) else "success",
            "target_count": count, "png_count": count,
            "active_branches": list(range(1, count + 1)),
            "skipped_branches": list(range(count + 1, 4)),
            "duplicate_check": "review_required"
            if any(q.get("similarity_warnings") for q in qualities) else "passed",
            "hidden_model_calls": 0, "outputs": qualities,
        }, ensure_ascii=False, indent=2)
        _diagnostic_path(prefixes[0], "_实际图片记录.json", directory).write_text(record, encoding="utf8")
        result = [torch.from_numpy(output) for output in outputs]
        result += [_blocker() for _ in range(3 - count)]
        return (*result, record)
