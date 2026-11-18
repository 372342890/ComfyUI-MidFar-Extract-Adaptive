"""Shared lazy ComfyUI node for three-route model acceptance and bottom origin."""
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode, urlparse
import uuid

from .glb_audit import process_glb
from .model_scale import parse_size_plan, verify_size_plan_item, verify_final_size


def _blocker():
    from comfy_execution.graph import ExecutionBlocker
    return ExecutionBlocker(None)


def _active_routes(route_report, models, prompt=None, unique_id=None):
    if route_report is not None and (not isinstance(route_report, str) or route_report.strip()):
        if not isinstance(route_report, str):
            raise ValueError("route_report 必须是 #8 的 JSON 文本。")
        try:
            report = json.loads(route_report)
        except (ValueError, TypeError) as exc:
            raise ValueError("route_report 不是合法 JSON。") from exc
        if not isinstance(report, dict) or report.get("status") in ("failed", "failure", "error"):
            raise ValueError("图片路由报告无效或上游验收已失败。")
        active = report.get("active_branches")
        if active is None:
            count = report.get("target_count")
            if type(count) is not int or not 0 <= count <= 3:
                raise ValueError("路由报告缺少有效的 active_branches/target_count。")
            active = list(range(1, count + 1))
        if (not isinstance(active, list)
                or any(type(i) is not int or not 1 <= i <= 3 for i in active)
                or active != sorted(set(active))):
            raise ValueError("active_branches 必须是 1–3 内升序且无重复的实际路编号。")
        count = report.get("target_count", len(active))
        if type(count) is not int or count != len(active):
            raise ValueError("target_count 与 active_branches 不一致。")
        if ("skipped_branches" in report
                and report["skipped_branches"] != [i for i in (1, 2, 3) if i not in active]):
            raise ValueError("skipped_branches 与实际有效路不一致。")
        return active
    # PROMPT can contain provider credentials: inspect only this node's links.
    if isinstance(prompt, dict) and unique_id is not None:
        entry = prompt.get(str(unique_id), prompt.get(unique_id, {}))
        inputs = entry.get("inputs", {}) if isinstance(entry, dict) else {}
        active = [i for i in (1, 2, 3)
                  if isinstance(inputs.get(f"glb_{i}"), list)
                  and len(inputs[f"glb_{i}"]) == 2]
        if active:
            return active
    active = [i for i, model in enumerate(models, 1) if model is not None]
    if not active:
        raise ValueError("请连接 #8 的 report 和实际模型路径；零路请明确提供空路报告。")
    return active


def _number(value, name, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or (integer and type(value) is not int)):
        raise ValueError(f"{name} 必须是有限的非负{'整数' if integer else '数值'}，0 表示关闭限制。")
    return value


def _output_prefix(value, directory):
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise ValueError("filename_prefix 必须是安全相对路径。")
    parts = value.split("/")
    if any(part in ("", ".", "..") or any(ord(c) < 32 for c in part) for part in parts):
        raise ValueError("filename_prefix 不允许绝对路径、空段或上级目录。")
    base = Path(directory).resolve()
    prefix = base.joinpath(*parts).resolve()
    if not prefix.is_relative_to(base) or prefix == base:
        raise ValueError("filename_prefix 超出输出目录。")
    return prefix, base


def _url_config(base_url, endpoint):
    if not isinstance(base_url, str) or not isinstance(endpoint, str):
        raise ValueError("下载链接设置必须是文本。")
    base_url = base_url.rstrip("/")
    if base_url:
        parsed = urlparse(base_url)
        if (parsed.scheme not in ("https", "http") or not parsed.netloc
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("base_url 必须是不含凭据或查询参数的 HTTP(S) 地址。")
    if not re.fullmatch(r"/[A-Za-z0-9_/-]+", endpoint) or "//" in endpoint:
        raise ValueError("view_endpoint 必须是本服务路径，例如 /api/view 或 /view。")
    return base_url, endpoint


def _warnings(report, min_edge, max_edge, max_mib, min_triangles, max_triangles):
    warnings = []
    edge, triangles = report["longest_edge"], report["triangles"]
    if min_edge and edge < min_edge:
        warnings.append(f"最长边 {edge:.6g} 小于下限 {min_edge:.6g}（模型坐标单位）。")
    if max_edge and edge > max_edge:
        warnings.append(f"最长边 {edge:.6g} 超过上限 {max_edge:.6g}（模型坐标单位）。")
    if max_mib and report["file_mib"] > max_mib:
        warnings.append(f"导出文件 {report['file_mib']:.6g} MiB 超过上限 {max_mib:.6g} MiB。")
    if min_triangles and triangles < min_triangles:
        warnings.append(f"实际三角面 {triangles} 少于下限 {min_triangles}。")
    if max_triangles and triangles > max_triangles:
        warnings.append(f"实际三角面 {triangles} 超过上限 {max_triangles}。")
    return warnings


class BAMidFarModelAudit:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_prefix": ("STRING", {"default": "midfar-model-audit/asset"}),
                "up_axis": (["Y", "Z"], {"default": "Y"}),
                "bottom_origin": ("BOOLEAN", {"default": True}),
                "min_longest_edge": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1e9}),
                "max_longest_edge": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1e9}),
                "max_file_mib": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1e6}),
                "min_triangles": ("INT", {"default": 3000, "min": 0, "max": 100000000}),
                "max_triangles": ("INT", {"default": 5000, "min": 0, "max": 100000000}),
                "strict_limits": ("BOOLEAN", {"default": False}),
                "base_url": ("STRING", {"default": ""}),
                "view_endpoint": ("STRING", {"default": "/api/view"}),
                "scale_factor": ("FLOAT", {"default": 1.0, "min": 0.000001,
                                          "max": 1e6, "step": 0.1,
                                          "tooltip": "缩放倍数：2 放大两倍，0.5 缩小一半，1 保持尺寸。"}),
            },
            "optional": {
                "glb_1": ("STRING", {"forceInput": True, "lazy": True}),
                "glb_2": ("STRING", {"forceInput": True, "lazy": True}),
                "glb_3": ("STRING", {"forceInput": True, "lazy": True}),
                "route_report": ("STRING", {"forceInput": True}),
                "scale_factor_1": ("FLOAT", {"forceInput": True, "lazy": True}),
                "scale_factor_2": ("FLOAT", {"forceInput": True, "lazy": True}),
                "scale_factor_3": ("FLOAT", {"forceInput": True, "lazy": True}),
                "scale_plan": ("STRING", {"forceInput": True, "lazy": True}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",) * 7
    RETURN_NAMES = ("glb_1", "download_url_1", "glb_2", "download_url_2",
                    "glb_3", "download_url_3", "report")
    FUNCTION = "run"
    CATEGORY = "MidFar Extract/Adaptive"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "三路共用模型验收：测量尺寸/文件大小/实际三角面，整组原点归位到底部中心，"
        "接入自动尺寸节点时逐路核验倍率并等比缩放；无接入时使用手动倍数，空路跳过；"
        "先验收全部有效路再输出，保留贴图UV和天然连接。")

    def check_lazy_status(self, glb_1=None, glb_2=None, glb_3=None, route_report=None,
                          prompt=None, unique_id=None, **kwargs):
        models = (glb_1, glb_2, glb_3)
        active = _active_routes(route_report, models, prompt, unique_id)
        missing = [f"glb_{i}" for i in active if models[i - 1] is None]
        if isinstance(prompt, dict) and unique_id is not None:
            entry = prompt.get(str(unique_id), prompt.get(unique_id, {}))
            inputs = entry.get("inputs", {}) if isinstance(entry, dict) else {}
            for name in [f"scale_factor_{i}" for i in active] + ["scale_plan"]:
                link = inputs.get(name)
                if isinstance(link, list) and len(link) == 2 and kwargs.get(name) is None:
                    missing.append(name)
        return missing

    @classmethod
    def IS_CHANGED(cls, glb_1=None, glb_2=None, glb_3=None, route_report=None,
                   prompt=None, unique_id=None, **kwargs):
        models = (glb_1, glb_2, glb_3)
        try:
            active = _active_routes(route_report, models, prompt, unique_id)
            fingerprint = hashlib.sha256()
            for i in active:
                path = models[i - 1]
                if not isinstance(path, str) or not path.strip():
                    return float("nan")
                with Path(path).open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        fingerprint.update(chunk)
                fingerprint.update(str(i).encode())
            return fingerprint.hexdigest()
        except (ValueError, OSError):
            return float("nan")

    def run(self, glb_1=None, glb_2=None, glb_3=None, route_report=None,
            filename_prefix="midfar-model-audit/asset", up_axis="Y", bottom_origin=True,
            min_longest_edge=0.0, max_longest_edge=0.0, max_file_mib=0.0,
            min_triangles=3000, max_triangles=5000, strict_limits=False,
            base_url="", view_endpoint="/api/view", prompt=None, unique_id=None,
            scale_factor=1.0, scale_factor_1=None, scale_factor_2=None, scale_factor_3=None,
            scale_plan=None):
        import folder_paths
        prefix, output_directory = _output_prefix(filename_prefix, folder_paths.get_output_directory())
        base_url, view_endpoint = _url_config(base_url, view_endpoint)
        for name, value in (("min_longest_edge", min_longest_edge),
                            ("max_longest_edge", max_longest_edge), ("max_file_mib", max_file_mib)):
            _number(value, name)
        _number(min_triangles, "min_triangles", integer=True)
        _number(max_triangles, "max_triangles", integer=True)
        if ((min_longest_edge and max_longest_edge and min_longest_edge > max_longest_edge)
                or (min_triangles and max_triangles and min_triangles > max_triangles)):
            raise ValueError("下限不能大于上限。")
        if up_axis not in ("Y", "Z") or type(bottom_origin) is not bool or type(strict_limits) is not bool:
            raise ValueError("up_axis/开关设置无效。")
        if (type(scale_factor) not in (int, float) or not math.isfinite(scale_factor)
                or scale_factor <= 0):
            raise ValueError("scale_factor（缩放倍数）必须是有限正数；1 保持尺寸，0 无效。")
        models = (glb_1, glb_2, glb_3)
        factors = (scale_factor_1, scale_factor_2, scale_factor_3)
        active = _active_routes(route_report, models, prompt, unique_id)
        token = uuid.uuid4().hex[:16]
        stem = prefix.with_name(prefix.name + "_" + token)
        record = {
            "node": "BAMidFarModelAudit", "status": "success",
            "active_branches": active, "skipped_branches": [i for i in (1, 2, 3) if i not in active],
            "processed_count": 0, "up_axis": up_axis, "strict_limits": strict_limits,
            "limits": {"min_longest_edge": min_longest_edge, "max_longest_edge": max_longest_edge,
                       "max_file_mib": max_file_mib, "min_triangles": min_triangles,
                       "max_triangles": max_triangles},
            "units": "model_units", "scale_factor": scale_factor,
            "automatic_scaling": False,
            "scale_mode": "target_size" if scale_plan is not None else
                          ("per_route_multiplier" if any(factors[i-1] is not None for i in active)
                           else "shared_multiplier"),
            "outputs": [],
        }
        prepared, created, sources = [], [], set()
        failed_branch = None
        try:
            targets, plan_items = parse_size_plan(scale_plan, active, up_axis) if scale_plan is not None else (None, {})
            if targets is not None:
                record["target_sizes"] = targets
            for branch in active:
                failed_branch = branch
                source = models[branch - 1]
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("有效路缺少真实 saved_glb_path，不能静默跳过。")
                path = Path(source).expanduser().resolve()
                if path.suffix.lower() != ".glb" or not path.is_file():
                    raise ValueError("模型路径必须指向现有本地 GLB 文件。")
                if path in sources:
                    raise ValueError("多路连接了同一模型文件，请检查接线。")
                sources.add(path)
                source_data = path.read_bytes()
                item = plan_items.get(branch)
                factor = factors[branch - 1]
                if factor is None:
                    factor = item.get("scale_factor") if item is not None else scale_factor
                if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0:
                    raise ValueError(f"路 {branch} 的缩放倍数必须是有限正数。")
                decision = None
                if item is not None:
                    if item.get("source_glb") != str(path):
                        raise ValueError("自动尺寸计划的源模型路径与本路不一致。")
                    decision = verify_size_plan_item(source_data, item, targets, up_axis, factor)
                data, audit = process_glb(source_data, up_axis=up_axis, bottom_origin=bottom_origin,
                                          scale_factor=factor)
                if decision is not None:
                    audit["target_size_verified"] = verify_final_size(audit, decision, targets)
                    audit["size_decision"] = decision
                    audit["target_sizes"] = targets
                warnings = _warnings(audit, min_longest_edge, max_longest_edge, max_file_mib,
                                     min_triangles, max_triangles)
                audit.update(branch=branch, source_glb=str(path), warnings=warnings,
                             status="warning" if warnings else "success")
                record["outputs"].append(audit)
                if warnings and strict_limits:
                    raise ValueError("严格验收未通过：" + "；".join(warnings))
                output = stem.with_name(stem.name + f"_route{branch}.glb")
                if output in sources or output.exists():
                    raise ValueError("输出文件冲突，拒绝覆盖输入或已存在文件。")
                prepared.append((branch, output, data, audit))
            # All active geometry has passed before any GLB is written.
            prefix.parent.mkdir(parents=True, exist_ok=True)
            slots = [None] * 6
            for branch, output, data, audit in prepared:
                failed_branch = branch
                created.append(output)
                output.write_bytes(data)
                url = base_url + view_endpoint + "?" + urlencode({
                    "filename": output.name,
                    "subfolder": output.parent.relative_to(output_directory).as_posix()
                    if output.parent != output_directory else "",
                    "type": "output",
                })
                audit.update(output_glb=str(output), download_url=url)
                slots[2 * (branch - 1):2 * branch] = [str(output), url]
            failed_branch = None
            record["processed_count"] = len(prepared)
            record["automatic_scaling"] = any(audit["scaling_applied"] for _, _, _, audit in prepared)
            if any(audit["warnings"] for _, _, _, audit in prepared):
                record["status"] = "warning"
            report_path = stem.with_name(stem.name + "_模型验收.json")
            record["report_path"] = str(report_path)
            text = json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2)
            report_path.write_text(text, encoding="utf8")
            result = tuple(value if value is not None else _blocker() for value in slots) + (text,)
            return {"ui": {"text": [text]}, "result": result}
        except (ValueError, OSError, OverflowError, TypeError, KeyError) as exc:
            for output in created:
                output.unlink(missing_ok=True)
            record.update(status="failed", processed_count=0, failed_branch=failed_branch,
                          error=str(exc), model_outputs_published=False)
            for audit in record["outputs"]:
                audit.pop("output_glb", None)
                audit.pop("download_url", None)
            failure_path = stem.with_name(stem.name + "_验收失败.json")
            try:
                prefix.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2),
                                        encoding="utf8")
            except OSError:
                pass
            raise ValueError(f"模型验收失败（路 {failed_branch}）：{exc}；诊断：{failure_path}") from exc
