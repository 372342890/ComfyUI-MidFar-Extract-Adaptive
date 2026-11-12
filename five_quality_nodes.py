"""ComfyUI adapters; native visible model nodes perform all generation/review."""
import json
import os
import base64
import hashlib
import io
from pathlib import Path
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image
import torch

from .five_quality import (
    compile_plan, decode, evaluate_review, finalize, inspect_images,
    prepare_reference, repair_prompts,
    fit_canvas, image_hash,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _array(tensor):
    if tensor is None or not hasattr(tensor, "detach"):
        raise ValueError("本轮实际图片尚未生成，不能用空图补位。")
    return tensor.detach().cpu().numpy()


def _state(value):
    state = decode(value)
    branches = state.get("failed_branches")
    if (not isinstance(branches, list) or
            any(type(i) is not int or i not in range(1, 6) for i in branches) or
            branches != sorted(set(branches)) or
            state.get("status") != ("retry_required" if branches else "accepted")):
        raise ValueError("本轮失败路状态无效。")
    return state


class BAMidFarFivePlan:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "reference": ("IMAGE",),
            "recognition": ("STRING", {"forceInput": True}),
            "pe": ("STRING", {"forceInput": True}),
            "filename_prefix": ("STRING", {"default": "midfar-five-quality"}),
        }}

    RETURN_TYPES = ("STRING",)*11
    RETURN_NAMES = tuple(f"prompt_{i}" for i in range(1, 6)) + tuple(
        f"target_{i}" for i in range(1, 6)) + ("plan",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, reference, recognition, pe, filename_prefix):
        plan = compile_plan(recognition, pe, _array(reference), filename_prefix)
        targets = [_json({"target": t, "source_hash": plan["source_hash"]})
                   for t in plan["recognized_targets"]]
        return (*plan["execution_prompts"], *targets, _json(plan))


class BAMidFarFiveReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reference": ("IMAGE",),
                             "target": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("crop", "marked_preview", "source_record")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, reference, target):
        record = decode(target)
        crop, marked, info = prepare_reference(
            _array(reference), record["target"], record["source_hash"])
        return torch.from_numpy(crop), torch.from_numpy(marked), _json(info)


class BAMidFarFiveInspect:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "reference": ("IMAGE",), "plan": ("STRING", {"forceInput": True}),
            **{f"image_{i}": ("IMAGE",) for i in range(1, 6)},
        }}

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("review_sheet", "review_prompt", "inspection")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, reference, plan, **images):
        sheet, prompt, inspection = inspect_images(
            decode(plan), _array(reference), [_array(images[f"image_{i}"]) for i in range(1, 6)])
        return torch.from_numpy(sheet), prompt, _json(inspection)


class BAMidFarJSONValue:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": ("STRING", {"default": "{}", "multiline": True})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("value",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, value):
        return (_json(decode(value)),)


class BAMidFarCanvasFit:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "fit_record")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, image):
        array = _array(image)
        result, record = fit_canvas(array, array.shape[2])
        return torch.from_numpy(result), _json(record)


class BAMidFarFiveArchive:
    """Clipboard transport for real PNGs; no model or external upload."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            **{f"image_{i}": ("IMAGE",) for i in range(1, 6)},
            "quality_record": ("STRING", {"default": "", "multiline": True}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("archive",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"
    OUTPUT_NODE = True

    def run(self, quality_record, **images):
        arrays = [_array(images[f"image_{i}"]) for i in range(1, 6)]
        hashes = [image_hash(image) for image in arrays]
        accepted = bool(quality_record.strip())
        record = decode(quality_record) if accepted else None
        if accepted and (
                record.get("status") != "accepted" or record.get("image_hashes") != hashes or
                [record.get(k) for k in ("png_count", "near_count", "far_count", "pair_count")]
                != [5, 3, 2, 10]):
            raise ValueError("导出验收记录与五张实际像素不一致，禁止标记通过。")
        manifest = {"accepted": accepted, "image_hashes": hashes, "files": [],
                    "quality_record": record, "hidden_model_calls": 0}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for i, array in enumerate(arrays, 1):
                if array.shape[0] != 1:
                    raise ValueError("每路导出必须恰好一张图。")
                png = io.BytesIO()
                pixels = np.round(np.clip(array[0], 0, 1)*255).astype(np.uint8)
                Image.fromarray(pixels).save(png, format="PNG")
                content = png.getvalue()
                name = f"{'accepted' if accepted else 'diagnostic'}_{i}.png"
                archive.writestr(name, content)
                manifest["files"].append({"name": name, "sha256": hashlib.sha256(content).hexdigest()})
            archive.writestr("manifest.json", _json(manifest))
        content = buffer.getvalue()
        text = json.dumps({"accepted": accepted, "zip_sha256": hashlib.sha256(content).hexdigest(),
                           "zip_base64": base64.b64encode(content).decode("ascii")})
        return {"ui": {"text": [text]}, "result": (text,)}


class BAMidFarFiveRetry:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {key: ("STRING", {"forceInput": True})
                             for key in ("plan", "inspection", "review")}}

    RETURN_TYPES = ("STRING",)*6
    RETURN_NAMES = tuple(f"retry_{i}" for i in range(1, 6)) + ("state",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def run(self, plan, inspection, review):
        state = evaluate_review(plan, inspection, review)
        return (*repair_prompts(plan, state), _json(state))


class BAMidFarFiveSelect:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "state": ("STRING", {"forceInput": True}),
            **{f"{stem}_{i}": ("IMAGE", {"lazy": True})
               for stem in ("image", "retry") for i in range(1, 6)},
        }}

    RETURN_TYPES = ("IMAGE",)*5
    RETURN_NAMES = tuple(f"image_{i}" for i in range(1, 6))
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"

    def check_lazy_status(self, state, **images):
        failed = _state(state)["failed_branches"]
        required = [f"{'retry' if i in failed else 'image'}_{i}" for i in range(1, 6)]
        return [key for key in required if images.get(key) is None]

    def run(self, state, **images):
        failed = _state(state)["failed_branches"]
        selected = tuple(images.get(f"{'retry' if i in failed else 'image'}_{i}")
                         for i in range(1, 6))
        for image in selected:
            _array(image)
        return selected


def _save_five(plan, record, images):
    """Publish the whole validated run atomically, with no partial final PNG set."""
    import folder_paths
    output = Path(folder_paths.get_output_directory()).resolve()
    prefixes = plan["prefixes"]
    paths = [(output/(prefix+".png")).resolve() for prefix in prefixes]
    if len(set(paths)) != 5 or any(not path.is_relative_to(output) for path in paths):
        raise ValueError("五路输出路径越界或重复。")
    directory = Path(os.path.commonpath([str(path.parent) for path in paths]))
    if directory.name != "run_"+plan["run_id"]:
        raise ValueError("五路必须位于本轮独立run目录。")
    record["png_paths"] = [str(path) for path in paths]
    record_path = directory/"五路验收记录.json"
    if directory.exists():
        if not record_path.exists() or decode(record_path.read_text()) != record:
            raise ValueError("本轮输出目录已存在不同结果，不能覆盖。")
        return
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".five_quality_", dir=directory.parent))
    try:
        for image, path in zip(images, paths):
            stage_path = staging/path.relative_to(directory)
            stage_path.parent.mkdir(parents=True, exist_ok=True)
            pixels = np.round(np.clip(image[0], 0, 1)*255).astype(np.uint8)
            Image.fromarray(pixels).save(stage_path, format="PNG")
        (staging/"五路验收记录.json").write_text(_json(record), encoding="utf8")
        staging.rename(directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class BAMidFarFiveFinal:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            **{key: ("STRING", {"forceInput": True}) for key in
               ("plan", "first_inspection", "first_review", "state")},
            "final_inspection": ("STRING", {"forceInput": True, "lazy": True}),
            "final_review": ("STRING", {"forceInput": True, "lazy": True}),
            **{f"image_{i}": ("IMAGE", {"lazy": True}) for i in range(1, 6)},
        }}

    RETURN_TYPES = ("IMAGE",)*5 + ("STRING",)
    RETURN_NAMES = tuple(f"image_{i}" for i in range(1, 6)) + ("quality_record",)
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Five Quality"
    OUTPUT_NODE = True

    def _verified_state(self, plan, first_inspection, first_review, state):
        actual = evaluate_review(plan, first_inspection, first_review)
        if _state(state) != actual:
            raise ValueError("失败路状态与实际首轮检查/视觉报告不一致。")
        return actual

    def check_lazy_status(self, plan, first_inspection, first_review, state,
                          final_inspection=None, final_review=None, **images):
        actual = self._verified_state(plan, first_inspection, first_review, state)
        missing = [f"image_{i}" for i in range(1, 6) if images.get(f"image_{i}") is None]
        if actual["failed_branches"]:
            if final_inspection is None:
                missing.append("final_inspection")
            if final_review is None:
                missing.append("final_review")
        return missing

    def run(self, plan, first_inspection, first_review, state,
            final_inspection=None, final_review=None, **images):
        actual = self._verified_state(plan, first_inspection, first_review, state)
        plan = decode(plan)
        inspection, review = first_inspection, first_review
        if actual["failed_branches"]:
            if final_inspection is None or final_review is None:
                raise ValueError("失败路重试后必须进行第二轮完整验收。")
            inspection, review = final_inspection, final_review
            if decode(inspection)["review_id"] == decode(first_inspection)["review_id"]:
                raise ValueError("第二轮不能复用首轮视觉报告或检查。")
        tensors = tuple(images.get(f"image_{i}") for i in range(1, 6))
        arrays = [_array(tensor) for tensor in tensors]
        record = finalize(plan, inspection, review, arrays)
        # A second round cannot quietly regenerate accepted routes.
        selected_hashes = decode(inspection)["image_hashes"]
        first_hashes = decode(first_inspection)["image_hashes"]
        for i in range(5):
            if i+1 not in actual["failed_branches"] and selected_hashes[i] != first_hashes[i]:
                raise ValueError("重试改变了首轮合格路，禁止换图。")
        record.update(retried_branches=actual["failed_branches"],
                      retry_count=int(bool(actual["failed_branches"])),
                      first_inspection=decode(first_inspection), first_review=decode(first_review))
        _save_five(plan, record, arrays)
        return (*tensors, _json(record))
