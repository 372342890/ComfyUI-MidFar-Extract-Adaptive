# 三路模型验收与底部原点归位

节点键：`BAMidFarModelAudit`
显示名称：**三路模型验收 · 倍数缩放与底部原点归位**

一个节点接收实际 0–3 路 GLB，输出每路验收后的模型路径、下载链接和统一 JSON 报告。使用缩放倍数整组等比缩放，随后归位到底部中心。默认手动倍数 `1`；接入新增的自动尺寸节点时，分别使用三路计算出的倍率。不会拆分连体山群或重写几何、UV、法线及贴图。

按最终尺寸自动判断倍数的参数、接线和安装方式见 [model-size-plan.md](model-size-plan.md)。该模式需同时更新 `model_scale.py`、`model_scale_nodes.py` 和本验收模块。

## 安装

交付包提供两种安装方式，选择一种：

1. **独立安装**：将 `ComfyUI-MidFar-Model-Audit/` 放入宿主的 `ComfyUI/custom_nodes/`，重启服务。这个独立节点只使用 Python 标准库，无新增 pip 依赖；宿主须支持 ComfyUI 的 lazy 输入和 `ExecutionBlocker`。
2. **更新原插件**：将 `原插件增量更新/` 中四个模块 `glb_audit.py`、`model_audit_nodes.py`、`model_scale.py`、`model_scale_nodes.py` 复制到 `ComfyUI-MidFar-Extract-Adaptive/`，按 `model-size-plan.md` 注册尺寸判断节点，重启服务。保持现有 `__init__.py` 的其他注册项。如果尚未注册验收节点，再加入下列内容。

```python
# 顶部导入
from .model_audit_nodes import BAMidFarModelAudit

# NODE_CLASS_MAPPINGS 内加入
"BAMidFarModelAudit": BAMidFarModelAudit,

# NODE_DISPLAY_NAME_MAPPINGS 内加入
"BAMidFarModelAudit": "三路模型验收 · 倍数缩放与底部原点归位",
```

同一宿主只注册一次这个节点键。导入 JSON 只会建立画布节点，必须先安装 Python 插件。

本次提供 `尺寸判断与验收-双节点.byteartist.json` 和 `尺寸判断与验收-双节点.comfyui.json`。两个节点间的倍率及报告已连接；导入后仍需接入原模型路径与图片检查报告。双节点模板不含上游生成任务或认证信息。

## 调整模型大小

在 `scale_factor`（缩放倍数）填入正数：`2` 放大两倍，`0.5` 缩小一半，`1` 保持原尺寸。三路共用这个倍数，各自按原模型尺寸整体同比例缩放，再执行底部原点归位。

```text
width_after  = width_before  * scale_factor
height_after = height_before * scale_factor
depth_after  = depth_before  * scale_factor
longest_edge_after = source_longest_edge * scale_factor
```

倍数大于 `1` 会放大，小于 `1` 会缩小。宽高深使用同一个正缩放系数，峰形、横向山肩与天然连接的相对比例保留。三路原尺寸的相对大小也保留，例如最长边分别为 `4、6、8`，倍数 `2` 后分别为 `8、12、16`。这里控制的是模型空间尺寸；`max_file_mib` 控制文件体积验收，等比缩放不会减少三角面或贴图体积。

倍数版交付包提供默认 `1` 的完整工作流及 ByteArtist/ComfyUI 单节点模板。旧版的 `size_mode`、`target_longest_edge` 控件已移除；加载旧工作流时需迁移节点输入，不能把旧目标最长边 `10` 当作缩放倍数。其他参数顺序与七个输出槽保持一致。

## 原三路房间接线

位置：**3D 生成后，旋转 GIF 预览前**。

| 原节点输出 | 新节点输入 |
| --- | --- |
| #11 `saved_glb_path` | `glb_1` |
| #17 `saved_glb_path` | `glb_2` |
| #23 `saved_glb_path` | `glb_3` |
| #8 图片检查 JSON 报告 | `route_report` |

| 新节点输出 | 下游输入 |
| --- | --- |
| `glb_1` | #12 `model_path` |
| `glb_2` | #18 `model_path` |
| `glb_3` | #24 `model_path` |
| `download_url_1` | #14 原模型下载链接输入 |
| `download_url_2` | #20 原模型下载链接输入 |
| `download_url_3` | #26 原模型下载链接输入 |
| `report` | 文本预览或后续验收节点 |

移除生成节点直接通往这些预览/下载节点的旧线，改接验收输出，才能让下游使用归位后的文件。图像、识别和 PE 节点继续使用当前配置。

输出槽按路排列，顺序固定为：

```text
0 glb_1
1 download_url_1
2 glb_2
3 download_url_2
4 glb_3
5 download_url_3
6 report
```

`route_report` 使用 #8 报告的 `active_branches` 和 `target_count`，仅请求有效路模型。空路输出 `ExecutionBlocker(None)`，不生成占位文件。明确零路可输入 `{"active_branches":[],"target_count":0}`。

没有报告时，节点仅检查自身连接以发现有效路；连接了三条线但只生成一条时，必须连接报告，以免请求被阻断的空路。

## 参数与大小判断

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `filename_prefix` | `midfar-model-audit/asset` | 宿主 output 目录内的相对前缀，带随机批次号，不覆盖原模型 |
| `up_axis` | `Y` | GLB 常用 Y 向上；已确认是 Z 向上的模型可选择 Z |
| `bottom_origin` | 开启 | 将整个自然单元的底部包围盒中心放到原点 |
| `min_longest_edge` | 0 | 最长边尺寸下限，0 关闭 |
| `max_longest_edge` | 0 | 最长边尺寸上限，0 关闭 |
| `max_file_mib` | 0 | 导出文件体积上限，0 关闭 |
| `min_triangles` | 3000 | 实际三角面下限，0 关闭 |
| `max_triangles` | 5000 | 实际三角面上限，0 关闭 |
| `strict_limits` | 关闭 | 关闭时超限报告 warning；开启时超限停止并阻断全部模型输出 |
| `base_url` | 空 | 空值产生当前服务相对链接；也可填服务域名 |
| `view_endpoint` | `/api/view` | ByteArtist 用 `/api/view`，标准 ComfyUI 改为 `/view` |
| `scale_factor` | 1 | 三路共用的正数缩放倍数：2 放大两倍，0.5 缩小一半，1 保持尺寸 |

宽、高、深和比例始终测量。尺寸单位是**模型坐标单位**，未推断为米或厘米。缩放倍数必须是有限正数，`0`、负数、布尔值或非数值输入无效；大小验收阈值仍用 `0` 关闭。大小上下限检查**导出后的最终尺寸**，阈值使用同一坐标单位。文件大小使用 MiB，1 MiB = 1,048,576 bytes；面数指渲染三角面，不是请求面数或唯一顶点数。

Y 向上时：宽 = X 范围、高 = Y 范围、深 = Z 范围。Z 向上时：宽 = X 范围、高 = Z 范围、深 = Y 范围。

## 底部原点与旋转预览

原点计算以**全部有效场景节点变换后的实际渲染顶点**为准，不采用可能过期的 accessor min/max，也不计算未使用顶点。多个山峰、山肩、连接山脊与共享岩基作为整组处理。

Y 向上时，底部中心为：

```text
((xmin+xmax)/2, ymin, (zmin+zmax)/2)
```

导出增加一个变换为恒等、位于 `(0,0,0)` 的资产根节点，其子节点执行整组等比缩放与平移。若缩放系数为 `s`，子节点的 scale 为 `(s,s,s)`，底部归位 translation 为 `-s × 原底部中心`。原场景节点与各部分相对位置保留；只修改 JSON 场景层级与变换，BIN 和其他未知数据块逐字节保留。原输入文件不覆盖。关闭底部归位时仅围绕世界原点缩放，不改变原点位置。

底部原点与预览相机注视点是两个位置。报告的 `camera_target` 为归位后包围盒中心，Y 向上通常是 `(0, height/2, 0)`。旋转可绕底部 Y 轴进行，镜头应仍以包围盒中心取景，避免山体偏下或裁切。外部 `GLBTurntableGIF` 若自行重置模型中心，需要单独核对其实现；本次没有验证云端 GIF 相机行为。

## 验收报告与失败行为

报告包含每路：

- 原始包围盒、`source_dimensions`、`source_longest_edge`；
- 最终 `dimensions`、`longest_edge`、宽高/宽深/高深比例；
- `scale_factor` 与 `scaling_applied`；
- 实际三角面、非退化三角面、场景节点与 mesh 实例数量；
- 原始和导出文件字节数、导出 MiB；
- 整组平移量、归位后包围盒、实测底部中心及相机 target；
- `binary_payload_preserved`、`export_verified`、warnings 和输出链接。

先处理并验收全部有效路，再保存 GLB。任一路模型缺失、损坏或严格超限，会保存 `_验收失败.json` 并抛出节点错误；本批次不会发布成功模型。成功报告保存在 `_模型验收.json`，也通过 `report` 输出。

支持 GLB v2 内嵌数据的静态 TRIANGLES 场景，包括层级变换、matrix/TRS、负缩放、多实例、交错与稀疏 accessor，以及 `KHR_mesh_quantization` 的整数位置。拒绝 Draco/meshopt/GPU 实例压缩、蒙皮、动画、morph、外部 buffer/贴图、无效索引、循环/共享节点、非有限顶点及全部退化面。对不支持的格式先解压或烘焙，再验收。

这是几何数值验收，不能自动判断模型是否忠实保留原图山形，不能替代原图与模型的视觉复核。

## 本轮验证范围

倍数版测试覆盖 `2`、`0.5`、`1`，多根节点/层级变换、三路相对尺寸、底部归位、最终尺寸限制、无效倍数，以及原有空路和失败阻断行为。具体测试数量和实际模型验证见本次交付记录。本次修改不调用图片或 3D 生成接口；云端是否已更新以本次同步记录为准。
