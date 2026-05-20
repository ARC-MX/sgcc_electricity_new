"""
点击验证码 LLM 解算器

使用火山引擎豆包大模型识别验证码中的图标位置，按参考图标顺序返回点击坐标。

策略:
1. 下载参考图标条，按三等分裁剪为3个独立图标
2. 下载主图，将所有图片以 data URI 发送（确保 LLM 能访问）
3. 单次 API 调用，一次找到所有3个图标（避免重复匹配同一位置）
"""

import base64
import io
import logging
import os
import re
from typing import List, Optional, Tuple

import requests
from PIL import Image
from openai import OpenAI

logger = logging.getLogger(__name__)


class ClickCaptchaSolver:
    """基于大模型的点击验证码解算器。"""

    def __init__(self,
                 api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 base_url: Optional[str] = None):
        self.api_key = (api_key or os.getenv('ARK_API_KEY', '').strip())
        self.model = model or "doubao-seed-2-0-pro-260215"
        self.base_url = base_url or "https://ark.cn-beijing.volces.com/api/v3"
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("ARK_API_KEY 未设置，验证码解算将失败")
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def solve(self, ref_url: str, main_url: str,
              main_width: int, main_height: int) -> List[Tuple[int, int]]:
        """下载图片 → 拆分参考条 → 单次LLM调用找到所有3个图标。"""
        # 1. 下载参考图标条并拆分为3个独立图标
        ref_raw = self._download(ref_url)
        if not ref_raw:
            return []
        icon_uris = self._split_strip(ref_raw)
        if len(icon_uris) < 3:
            return []

        # 2. 下载主图并转为 data URI（确保LLM能访问）
        main_raw = self._download(main_url)
        if not main_raw:
            return []
        main_uri = "data:image/png;base64," + base64.b64encode(main_raw).decode("ascii")

        # 3. 单次调用找到所有图标
        coords = self._find_all_icons(icon_uris, main_uri, main_width, main_height)
        if len(coords) < 2:
            return []

        # 钳制到主图范围内
        return [
            (max(0, min(x, main_width - 1)), max(0, min(y, main_height - 1)))
            for x, y in coords
        ]

    def _download(self, url: str) -> Optional[bytes]:
        """下载图片，支持 http 和 data URI。"""
        try:
            if url.startswith("data:"):
                _, encoded = url.split(",", 1)
                try:
                    return base64.b64decode(encoded)
                except Exception:
                    return base64.b64decode(__import__('urllib.parse').unquote(encoded))
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                return resp.content
            logger.error(f"下载失败: HTTP {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"下载错误: {e}")
            return None

    def _split_strip(self, raw: bytes) -> List[str]:
        """将参考图标条三等分为独立图标的 data URI。"""
        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            logger.info(f"参考图标条: {w}x{h}")

            part_w = w // 3
            uris = []
            for i in range(3):
                left = i * part_w
                right = (i + 1) * part_w if i < 2 else w
                icon = img.crop((left, 0, right, h))
                # 放大图标以便LLM看清细节
                icon = icon.resize((icon.width * 3, icon.height * 3), Image.LANCZOS)
                buf = io.BytesIO()
                icon.save(buf, format="PNG")
                uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                uris.append(uri)
                logger.info(f"图标 #{i + 1}: {icon.width}x{icon.height}")
            return uris
        except Exception as e:
            logger.error(f"拆分错误: {e}")
            return []

    def _find_all_icons(self, icon_uris: List[str], main_uri: str,
                        main_width: int, main_height: int) -> List[Tuple[int, int]]:
        """单次API调用，带链式思考提示，让LLM找到所有3个图标。"""
        prompt = (
            "你的任务是找图：3个参考图标分别在网格大图中的位置。\n\n"
            "## 第一步：观察参考图标\n"
            "仔细看3个参考图标，在脑中记住每个图标的：形状轮廓、主要颜色、内部细节、旋转方向。\n"
            "注意3个图标是不同的，它们的颜色和/或形状一定有区别。\n\n"
            "## 第二步：扫描大图网格\n"
            f"大图（{main_width}×{main_height}像素）是一个图标网格（约4×4或3×3排列）。\n"
            "逐行逐列扫描每个网格中的图标，找到与3个参考图标匹配的。\n\n"
            "## 匹配规则\n"
            "- 匹配图标与参考图标的形状和颜色必须一致（允许旋转，但旋转角度不影响匹配）\n"
            "- 网格中可能有多个颜色相同的图标，但形状细节不同——请仔细辨别\n"
            "- 空心/实心、线条粗细、是否有圆点等细节是关键区分点\n"
            "- 3个目标图标在网格中的位置各不相同\n\n"
            "## 输出\n"
            "先简要说明每个参考图标的特征和匹配位置，然后输出一行坐标：\n"
            "(x1, y1), (x2, y2), (x3, y3)\n"
            "其中x、y为图标中心的比例坐标（0~1），分别对应图标A、B、C。"
        )

        content = []
        labels = ["A", "B", "C"]
        for i, uri in enumerate(icon_uris[:3]):
            content.append({"type": "image_url", "image_url": {"url": uri}})
            content.append({"type": "text", "text": f"参考图标{labels[i]}"})

        content.append({"type": "image_url", "image_url": {"url": main_uri}})
        content.append({"type": "text", "text": prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=500,
            )
            output = response.choices[0].message.content or ""
            logger.info(f"大模型响应: {output[:400]}")
            return self._parse_coordinates(output, main_width, main_height)
        except Exception as e:
            logger.error(f"大模型错误: {e}")
            return []

    def _parse_coordinates(self, text: str,
                           main_width: int, main_height: int) -> List[Tuple[int, int]]:
        """从LLM返回文本中解析坐标并转为像素。"""
        coords = []
        paren_pairs = re.findall(r'\(\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\)', text)
        for x_str, y_str in paren_pairs:
            x, y = float(x_str), float(y_str)
            coords.append((x, y))

        if not coords:
            # 宽松匹配
            nums = re.findall(r'(\d+\.?\d+)', text)
            for i in range(0, len(nums) - 1, 2):
                coords.append((float(nums[i]), float(nums[i + 1])))

        result = []
        for x, y in coords[:3]:
            max_val = max(x, y)
            if max_val <= 1.5:
                result.append((round(x * main_width), round(y * main_height)))
            else:
                result.append((round(x), round(y)))
        return result
