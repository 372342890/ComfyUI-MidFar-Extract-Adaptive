# 三路模型验收与底部原点归位

节点键：`BAMidFarModelAudit`

显示名称：**三路模型验收 · 尺寸检测与底部原点归位**

一个节点接收实际 0–3 路 GLB，输出每路验收后的模型路径、下载链接和统一 JSON 报告。支持按目标最长边整组等比缩放，随后归位到底部中心。默认保持原尺寸；不拆分连体山群、不压平峰顶、不增加底座，也不重写几何、UV、法线或贴图。

## 安装

交付包提供两种安装方式，选择一种：

1. **独立安装**：将 `ComfyUI-MidFar-Model-Audit/` 放入宿主的 `ComfyUI/custom_nodes/`，重启服务。这个独立节点只使用 Python 标准库，无新增 pip 依赖；宿主须支持 ComfyUI 的 lazy 输入和 `ExecutionBlocker`。
2. **更新本次对应的原插件**：将 `原插件增量更新/` 中的 `glb_audit.py`、`model_audit_nodes.py` 和 `__init__.py` 覆盖到 `ComfyUI-MidFar-Extract-Adaptive/`，重启服务。该 `__init__.py` 对应本次本地插件副本；其他版本可只复制两个新模块，然后手动注册下列内容。

```python
# 顶部导入
from .model_audit_nodes import BAMidFarModelAudit

# NODE_CLASS_MAPPINGS 内加入
"BAMidFarModelAudit": BAMidFarModelAudit,

# NODE_DISPLAY_NAME_MAPPINGS 内加入
"BAMidFarModelAudit": "三路模型验收 · 尺寸检测与底部原点归位",
```

同一宿主只注册一次这个节点键。导入 JSON 只会建立画布节点，必须先安装 Python 插件。

`三路模型验收-单节点.byteartist.json` 为 ByteArtist 单节点模板；`三路模型验收-单节点.comfyui.json` 为标准 ComfyUI 单节点模板。将节点复制进现有画布再接线。单节点模板没有模型路径，也没有生成任务或认证信息。

## 调整模型大小

1. 将 `size_mode` 从 `keep`（保持原尺寸）改为 `longest_edge`（统一最长边）。
2. 在 `target_longest_edge` 填入目标值，例如 `10`。
3. 三路分别按自己的实际最长边计算缩放系数，整体同比例缩放，再执行底部原点归位。

```text
scale_factor = target_longest_edge / source_longest_edge
width_after  = width_before  * scale_factor
height_after = height_before * scale_factor
depth_after  = depth_before  * scale_factor
```

目标大于当前最长边会放大，小于当前最长边会缩小。宽高深使用同一个正缩放系数，峰形、横向山肩与天然连接的相对比例保留。这里控制的是模型空间尺寸；`max_file_mib` 控制文件体积验收，等比缩放不会减少三角面或贴图体积。

交付包还提供 `三路模型验收-最长边10示例.byteartist.json`，已选择 `longest_edge`、目标 `10`。普通单节点模板仍默认 `keep`。新增两个控件附加在原有控件后，原有参数顺序与七个输出槽保持一致。

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
| `size_mode` | `keep` | `keep` 保持尺寸；`longest_edge` 整组等比缩放至目标最长边 |
| `target_longest_edge` | 10 | 正数目标最长边，使用模型坐标单位，仅在缩放模式应用 |

宽、高、深和比例始终测量。尺寸单位是**模型坐标单位**，未推断为米或厘米。只有选择 `longest_edge` 才缩放。目标最长边必须是有限正数，`0` 不是有效缩放目标；大小验收阈值仍用 `0` 关闭。大小上下限检查**导出后的最终尺寸**，阈值使用同一坐标单位。文件大小使用 MiB，1 MiB = 1,048,576 bytes；面数指渲染三角面，不是请求面数或唯一顶点数。

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
- `size_mode`、`target_longest_edge`、`scale_factor` 与 `scaling_applied`；
- 实际三角面、非退化三角面、场景节点与 mesh 实例数量；
- 原始和导出文件字节数、导出 MiB；
- 整组平移量、归位后包围盒、实测底部中心及相机 target；
- `binary_payload_preserved`、`export_verified`、warnings 和输出链接。

先处理并验收全部有效路，再保存 GLB。任一路模型缺失、损坏或严格超限，会保存 `_验收失败.json` 并抛出节点错误；本批次不会发布成功模型。成功报告保存在 `_模型验收.json`，也通过 `report` 输出。

支持 GLB v2 内嵌数据的静态 TRIANGLES 场景，包括层级变换、matrix/TRS、负缩放、多实例、交错与稀疏 accessor，以及 `KHR_mesh_quantization` 的整数位置。拒绝 Draco/meshopt/GPU 实例压缩、蒙皮、动画、morph、外部 buffer/贴图、无效索引、循环/共享节点、非有限顶点及全部退化面。对不支持的格式先解压或烘焙，再验收。

这是几何数值验收，不能自动判断模型是否忠实保留原图山形，不能替代原图与模型的视觉复核。

## 本轮验证范围

本地 28 项测试通过，另含多个损坏格式和 0–3 路子用例。三份现有贴图 GLB 在目标最长边 `10` 下分别整体等比缩放，最终最长边均为 `10`、底部中心归位为 `(0,0,0)`，面数保留为 3828、3804、3890，宽高深比例、材质、UV 和内嵌贴图所在二进制数据保持一致。保持原尺寸模式继续兼容原有验证。

交付包包含最长边 `10` 的三份缩放归位模型和详细 JSON。云端插件未安装，原房间未接线，本轮未调用图片/3D 生成接口，也未重跑云端旋转 GIF。
