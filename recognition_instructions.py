"""Shared native recognition instructions; independent of the ComfyUI host."""
GROUP_RULES = """
【自然组契约 natural-units-v1】
只输出一个完整JSON对象，不写思考过程、Markdown、注释、算式或重复字段。
id使用英文字符串，每个unit_id唯一；坐标统一coordinate_space="normalized"，所有bbox、
target_point和landmarks.point均为完整原图0..1数值，先计算数值，不输出除法。
grouping_contract="natural-units-v1"。每个objects目标新增component_ids英文字符串数组，
同一实际岩峰、岩肩、岩基组成部分只能属于一个自然组；不把连体双峰按左右拆路。
is_single=true代表整个自然单元，即使它有多个相连峰。不同深度投影重叠不是连接。
group_relations完整列出五个目标全部十对，每项：
{"ids":["实际id1","实际id2"],"relation":"separated或separate_depth","evidence":"当前图可见分界"}
只有确实不同深度才用separate_depth；同深度用可见雪谷/间隙等岩土分离证据。
若存在真实连接，先合并该自然单元、重新选其他独立目标；不得把connected改成
separated凑数。无完整五组时输出实际数量与shortfall，不能臆造补位。
close_visible_rock_edge仅可用于kind=rock、role=mountain且boundary_cut=true的近景岩台末端；
完整山峰/远景山脉不得使用该策略。其余目标completion_policy="short_occluded_foot"。
各目标仍完整提供name、role、kind、layer、location、appearance、occlusion、
connected_parts、excluded_objects、is_single、suitable、boundary_cut、completion_policy、
bbox、target_point、至少3个landmarks{name,shape,point}、至少4项contour_fingerprint和rhythm。
style与shortfall仍保留。描述具体简洁，每条证据30字左右；优先完整输出全部字段。
"""
