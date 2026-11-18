"""Safe face-only conversion for the existing image analysis carriers."""
from .model_parameters import parse_face_count


class BAMidFarFaceCountFromAnalysis:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"default": "4000", "multiline": True}),
            "default_faces": ("INT", {"default": 4000, "min": 3000, "max": 5000, "step": 100}),
        }}

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("faces",)
    FUNCTION = "convert"
    CATEGORY = "MidFar Extract/Adaptive"
    DESCRIPTION = "只读取分析 JSON 的 faces 字段，限制到3000–5000；不把倍数或说明中的数字当作面数。"

    def convert(self, text, default_faces=4000):
        return (parse_face_count(text, default_faces),)
