"""
点击验证码 LLM 解算器

使用硅基流动 Qwen 多模态大模型识别验证码中的图标位置，按参考图标顺序返回点击坐标。

策略:
1. 下载参考图标条，按三等分裁剪为3个独立图标
2. 下载主图，将所有图片以 data URI 发送（确保 LLM 能访问）
3. 单次 API 调用，一次找到所有3个图标（避免重复匹配同一位置）
"""

import base64
import io
import json
import logging
import os
import re
from typing import List, Optional, Tuple

import requests
from PIL import Image
from openai import OpenAI

import const

logger = logging.getLogger(__name__)


class ClickCaptchaSolver:
    """基于大模型的点击验证码解算器。"""

    def __init__(self,
                 api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv('LLM_API_KEY') or const.LLM_API_KEY
        self.model = model or os.getenv('LLM_MODEL') or const.LLM_MODEL
        self.base_url = base_url or os.getenv('LLM_BASE_URL') or const.LLM_BASE_URL
        # Fallback endpoint chain: LLM_FALLBACK_MODELS is a comma-separated
        # list. Each entry is either
        #   model                     (reuse the primary base_url/api_key)
        #   base_url::api_key::model  (cross-provider, independent endpoint and key)
        self.endpoints: List[Tuple[str, str, str]] = [(self.base_url, self.api_key, self.model)]
        for item in os.getenv('LLM_FALLBACK_MODELS', '').split(','):
            item = item.strip()
            if not item:
                continue
            parts = item.split('::')
            if len(parts) == 3:
                self.endpoints.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
            elif len(parts) == 1:
                self.endpoints.append((self.base_url, self.api_key, item))
            else:
                logger.warning(f"Ignoring malformed fallback entry: {item}")
        self._clients: dict = {}

    def _client_for(self, base_url: str, api_key: str) -> OpenAI:
        """Lazily create (and cache) an OpenAI client per endpoint."""
        cache_key = (base_url, api_key)
        if cache_key not in self._clients:
            self._clients[cache_key] = OpenAI(base_url=base_url, api_key=api_key,
                                              timeout=240.0, max_retries=2)
        return self._clients[cache_key]

    @property
    def client(self) -> OpenAI:
        return self._client_for(self.endpoints[0][0], self.endpoints[0][1])

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
        """单次API调用，让LLM找到所有3个图标。"""
        prompt = (
            f"大图（{main_width}×{main_height}像素）是一个图标网格。\n"
            "找到3个参考图标(A, B, C)各自在大图网格中的位置。\n"
            "匹配规则：形状和颜色必须一致，空心/实心、线条粗细是关键区分点，允许旋转。\n\n"
            '输出JSON：{"coords":[[xA,yA],[xB,yB],[xC,yC]]}\n'
            "其中x、y为图标中心的比例坐标（0~1）。"
        )

        content = []
        labels = ["A", "B", "C"]
        for i, uri in enumerate(icon_uris[:3]):
            content.append({"type": "image_url", "image_url": {"url": uri}})
            content.append({"type": "text", "text": f"参考图标{labels[i]}"})

        content.append({"type": "image_url", "image_url": {"url": main_uri}})
        content.append({"type": "text", "text": "Use only the visible captcha image above. Return center coordinates for A, B, C as JSON. Do not use page or hidden DOM coordinates."})
        content.append({"type": "text", "text": prompt})

        last_err: Optional[Exception] = None
        for i, (ep_url, ep_key, model_name) in enumerate(self.endpoints):
            # Some reasoning gateways return an empty content when
            # response_format=json_object is set. Only enable it on the
            # primary endpoint, and retry once without it if the body is empty.
            attempts = [True, False] if i == 0 else [False]
            for use_rf in attempts:
                try:
                    client = self._client_for(ep_url, ep_key)
                    extra = {"response_format": {"type": "json_object"}} if use_rf else {}
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "Output valid JSON only. No markdown, no explanation."},
                            {"role": "user", "content": content},
                        ],
                        max_tokens=4096,
                        **extra,
                    )
                    output = response.choices[0].message.content or ""
                    if not output.strip():
                        logger.warning(f"Model {model_name} returned empty content (response_format={use_rf})")
                        continue
                    if i > 0:
                        logger.info(f"Fallback endpoint {ep_url} / {model_name} succeeded")
                    logger.info(f"大模型响应: {output[:400]}")
                    coords = self._parse_coordinates(output, main_width, main_height)
                    if coords:
                        return coords
                    logger.warning(f"Model {model_name} returned unparsable coords: {output[:200]}")
                except Exception as e:
                    last_err = e
                    logger.warning(f"Model {model_name} failed: {e}")
                    break
        raise RuntimeError(f"LLM 调用失败: {last_err}")

    def _parse_coordinates(self, text: str,
                           main_width: int, main_height: int) -> List[Tuple[int, int]]:
        """从LLM返回文本中提取JSON坐标并转为像素。"""
        # 优先尝试JSON解析
        match = re.search(r'\{.*"coords"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                result = []
                for x, y in data["coords"]:
                    x, y = float(x), float(y)
                    if max(x, y) <= 1.5:
                        result.append((round(x * main_width), round(y * main_height)))
                    else:
                        result.append((round(x), round(y)))
                return result
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        # 回退：正则解析
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
