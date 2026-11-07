"""Text sequence captcha solver for DEBUG_MODE SMS login.

Unlike the click/slider captcha (captcha_playwright.py + click_captcha_solver.py),
SMS login triggers a "text sequence" captcha where the user must click Chinese
characters in a specific order based on a prompt.

This module provides the solve_text_sequence_captcha() function and its helpers,
moved from data_fetcher.py to keep captcha logic in one place.
"""

import base64
import json
import logging
import os
import re
import time
import random

logger = logging.getLogger(__name__)

_WIDGET_SELECTORS = [
    ".tencent-captcha-dy__warp",
    "#tCaptchaDyContent",
    ".tencent-captcha-dy__wrapper",
    ".tencent-captcha__wrapper",
]


def _is_captcha_widget_visible(el) -> bool:
    """Check if captcha widget is truly visible (not a hidden pre-loaded DOM)."""
    try:
        if el is None or not el.is_visible():
            return False
        box = el.bounding_box()
        if not box:
            return False
        if box["width"] < 80 or box["height"] < 80:
            return False
        if box["y"] < -500 or box["x"] < -500:
            return False
        return True
    except Exception:
        return False


def _locate_captcha_widget(page, context):
    """Find visible Tencent captcha across pages/iframes.

    Returns (page_containing_captcha, captcha_element) or (None, None).
    """
    pages = [page] + [p for p in context.pages if p != page]
    for pg in pages:
        try:
            if pg is None or pg.is_closed():
                continue
        except Exception:
            continue
        try:
            frames = list(pg.frames)
        except Exception:
            frames = []
        for frame in frames:
            for sel in _WIDGET_SELECTORS:
                try:
                    el = frame.query_selector(sel)
                    if _is_captcha_widget_visible(el):
                        logger.info(f"已定位验证码控件：selector={sel}")
                        return pg, el
                except Exception:
                    continue
    return None, None


def _query_across_frames(page, selectors):
    """Find first visible element across all frames."""
    if isinstance(selectors, str):
        selectors = [selectors]
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for sel in selectors:
        for frame in frames:
            try:
                el = frame.query_selector(sel)
                if el and el.is_visible():
                    return el
            except Exception:
                continue
    return None


def _click_confirm_in_captcha(page) -> bool:
    """Click confirm button inside captcha widget."""
    btn = _query_across_frames(page, [
        ".tencent-captcha-dy__verify-confirm-btn",
        ".tencent-captcha-dy__confirm-btn",
        "button:has-text('确定')",
    ])
    if btn is None:
        logger.info("未找到显式确认按钮")
        return False
    try:
        for _ in range(10):
            disabled = btn.get_attribute("disabled")
            cls = btn.get_attribute("class") or ""
            if disabled is None and "disabled" not in cls:
                break
            time.sleep(0.3)
        btn.click()
        logger.info("已点击验证码确认按钮")
        return True
    except Exception as e:
        logger.warning(f"点击确认按钮失败: {e}")
        return False


def _click_refresh_in_captcha(page):
    """Click refresh button inside captcha widget."""
    btn = _query_across_frames(page, [
        "#tCaptchaDyMainWrap > div:nth-child(3) > div:nth-child(2) > div:first-child > img",
        ".tencent-captcha-dy__footer-icon--refresh",
        "[class*='refresh']",
    ])
    if btn is not None:
        try:
            btn.click()
            return
        except Exception:
            pass
    try:
        page.evaluate("""() => {
            var els = document.querySelectorAll('[class*="refresh"]');
            for (var i = 0; i < els.length; i++) {
                if (els[i].offsetParent !== null && els[i].getBoundingClientRect().width > 5) {
                    els[i].click(); return;
                }
            }
        }""")
    except Exception:
        pass


def _parse_text_captcha_result(text, img_w, img_h):
    """Parse LLM response for sequence and coordinates (0~1 ratio)."""
    seq, coords = [], []
    try:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            seq = data.get("sequence", []) or data.get("chars", []) or []
            raw = data.get("coords", []) or []
            for item in raw[:3]:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    coords.append((float(item[0]), float(item[1])))
    except Exception:
        pass
    if not coords:
        for x, y in re.findall(r'\[\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\]', text):
            coords.append((float(x), float(y)))
    normalized = []
    for (x, y) in coords[:3]:
        if max(x, y) <= 1.5:
            normalized.append((x, y))
        elif img_w and img_h:
            normalized.append((x / img_w, y / img_h))
        else:
            logger.warning(f"坐标缺少尺寸信息: ({x},{y})")
            return seq, []
    return seq, normalized


def _has_captcha_timeout(page) -> bool:
    """Check if captcha shows a timeout message."""
    try:
        text = (page.text_content("body") or "").lower()
        return any(kw in text for kw in ["超时", "timeout", "已过期", "expired"])
    except Exception:
        return False


def _extract_prompt_chars(page):
    """从验证码 DOM 中直接读取提示文字（如 '请依次点击：撤 奥 脖'），返回字符列表。"""
    try:
        el = page.query_selector(".tencent-captcha-dy__header-text")
        if el and el.is_visible():
            text = (el.get_attribute("aria-label") or el.inner_text() or "").strip()
            # 解析 "请依次点击：撤 奥 脖" → ["撤", "奥", "脖"]
            if "：" in text:
                text = text.split("：", 1)[1]
            elif ":" in text:
                text = text.split(":", 1)[1]
            chars = [c.strip() for c in text.split() if c.strip() and len(c.strip()) == 1]
            if len(chars) >= 2:
                return chars
    except Exception:
        pass
    return []


def solve_text_sequence_captcha(page, login_url, retry_limit=5) -> bool:
    """DEBUG_MODE text sequence captcha: screenshot -> LLM coordinates -> click.

    Args:
        page: Playwright Page (login page).
        login_url: Login page URL, used to detect redirect.
        retry_limit: Max attempts.
    """
    from openai import OpenAI

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        logger.error("LLM_API_KEY 未设置")
        return False
    base_url = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1").strip()
    model = os.getenv("LLM_MODEL", "Qwen/Qwen3.5-35B-A3B").strip()

    # Endpoint chain: primary endpoint (env) plus optional
    # LLM_FALLBACK_MODELS entries (same syntax as click_captcha_solver.py)
    endpoints = [(base_url, api_key, model)]
    for item in os.getenv("LLM_FALLBACK_MODELS", "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split("::")
        if len(parts) == 3:
            endpoints.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
        elif len(parts) == 1:
            endpoints.append((base_url, api_key, item))

    for attempt in range(retry_limit):
        if page.url != login_url:
            logger.info("页面已跳转，验证码通过")
            return True
        time.sleep(0.5)

        cap_page, cap_el = _locate_captcha_widget(page, page.context)
        if cap_page is None or cap_el is None:
            time.sleep(1)
            if page.url != login_url:
                return True
            logger.warning("未检测到验证码控件")
            continue

        # ── 仅在第一次尝试时调用 LLM ──
        if attempt == 0:
            coords_ok = False
            target_chars = _extract_prompt_chars(cap_page)
            if target_chars:
                logger.info(f"DOM 提取到目标文字: {target_chars}")
                char_list = "、".join(target_chars)
                prompt = (
                    f"在图片中找到这 {len(target_chars)} 个汉字：{char_list}，"
                    f"按此顺序返回每个字中心的比例坐标(0~1)。"
                    f'输出JSON：{{"coords":[[x1,y1],[x2,y2],[x3,y3]]}}'
                )
            else:
                prompt = (
                    "中文文字顺序验证码。读提示→找候选汉字→按顺序返回比例坐标。"
                    '输出JSON：{"sequence":["字1","字2","字3"],"coords":[[x1,y1],[x2,y2],[x3,y3]]}'
                    "，x,y为0~1比例坐标。"
                )

            try:
                img_bytes = cap_el.screenshot()
                box = cap_el.bounding_box()
                if box and box["width"] >= 10:
                    img_w, img_h = box["width"], box["height"]
                    img_b64 = base64.b64encode(img_bytes).decode()
                    result_text = ""
                    for mi, (ep_url, ep_key, model_name) in enumerate(endpoints):
                        try:
                            ep_client = OpenAI(base_url=ep_url, api_key=ep_key, timeout=120.0)
                            resp = ep_client.chat.completions.create(
                                model=model_name,
                                messages=[
                                    {"role": "system", "content": "只输出 JSON。"},
                                    {"role": "user", "content": [
                                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                                        {"type": "text", "text": prompt},
                                    ]},
                                ],
                                max_tokens=1024,
                            )
                            result_text = resp.choices[0].message.content or ""
                            if not result_text.strip():
                                logger.warning(f"Model {model_name} returned empty content")
                                continue
                            if mi > 0:
                                logger.info(f"Fallback endpoint {ep_url} / {model_name} succeeded")
                            break
                        except Exception as e:
                            logger.warning(f"Model {model_name} failed: {e}")
                    if not result_text:
                        raise RuntimeError(f"LLM 调用失败（已尝试 {len(endpoints)} 个端点）")
                    logger.info(f"LLM: {result_text[:200]}")

                    sequence, coords = _parse_text_captcha_result(result_text, img_w, img_h)
                    if target_chars and len(target_chars) >= len(coords):
                        sequence = target_chars

                    if len(coords) >= 2:
                        logger.info(f"点击顺序: {sequence}")
                        for i, (cx, cy) in enumerate(coords[:3]):
                            px = box["x"] + cx * box["width"]
                            py = box["y"] + cy * box["height"]
                            try:
                                cap_page.mouse.click(px, py)
                                logger.info(f"点击#{i + 1}: ({px:.0f},{py:.0f})")
                                time.sleep(random.uniform(0.15, 0.3))
                            except Exception:
                                break
                        _click_confirm_in_captcha(cap_page)
                        time.sleep(1)
                        if page.url != login_url:
                            return True
                        coords_ok = True
            except Exception as e:
                logger.error(f"LLM 失败: {e}")
                raise RuntimeError(f"LLM 调用失败: {e}") from e

        # ── LLM 失败或后续尝试：直接刷新验证码，等待跳转 ──
        logger.info("刷新验证码并等待跳转...")
        for _ in range(6):
            _click_refresh_in_captcha(cap_page)
            time.sleep(3)
            if page.url != login_url:
                logger.info("刷新后页面跳转，登录成功")
                return True

        # 刷新 6 次仍未跳转，重新定位控件进入下一轮
        logger.info("刷新后仍未跳转，进入下一轮")

    return False
