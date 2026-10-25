"""Playwright-based captcha solving for 95598 Tencent captcha.

Key differences from Selenium version:
- Uses Playwright locators instead of Selenium find_element
- Page.wait_for_selector() instead of WebDriverWait
- Mouse actions via page.mouse instead of ActionChains
- No CDP stealth needed (handled at context level in data_fetcher)
"""

import io
import logging
import os
import random
import re
import time
from typing import List, Optional, Tuple

import requests
from PIL import Image
from playwright.sync_api import Page

from click_captcha_solver import ClickCaptchaSolver

logger = logging.getLogger(__name__)

TENCENT_SELECTORS = {
    "content": "#tCaptchaDyContent",
    "header_answer_img": ".tencent-captcha-dy__header-answer img",
    "point_area": ".tencent-captcha-dy__point-area",
    "click_type_wrap": ".tencent-captcha-dy__click-type-wrap",
    "image_area": ".tencent-captcha-dy__verify-bg-img",
    "verify_bg_img": ".tencent-captcha-dy__verify-bg-img",
    "verify_bg": ".tencent-captcha-dy__verify-bg",
    "refresh_btn": ".tencent-captcha-dy__footer-icon--refresh",
    "confirm_btn": ".tencent-captcha-dy__verify-confirm-btn",
    "slider_area": ".tencent-captcha-dy__verify-slider-area",
    "slider_groove": ".tencent-captcha-dy__slider-groove",
    "slider_block": ".tencent-captcha-dy__slider-block",
    "slider_bg_img": ".tencent-captcha-dy__slider-bg-img",
}

_WIDGET_SELECTORS = [
    ".tencent-captcha-dy__warp",
    ".tencent-captcha-dy__wrapper",
    ".tencent-captcha__wrapper",
    ".tencent-captcha-dy__body-wrap",
    "#tCaptchaDyContent",
]


def solve_captcha_in_browser(page: Page,
                             timeout: int = 15,
                             max_retries: int = 3,
                             selectors: dict = None,
                             solver: ClickCaptchaSolver = None) -> bool:
    """Handle captcha in Playwright browser. Returns True on success."""
    selectors = selectors or TENCENT_SELECTORS
    solver = solver or ClickCaptchaSolver()

    for attempt in range(max_retries):
        logger.info(f"Captcha attempt {attempt + 1}/{max_retries}")

        if _has_rk001(page):
            logger.error("Detected RK001 risk-control response; stop captcha handling.")
            return False

        if not _wait_for_captcha(page, timeout):
            logger.warning("Captcha did not appear")
            continue

        captcha_type = _detect_captcha_type_js(page)
        logger.info(f"Captcha type: {captcha_type}")

        if captcha_type == "slider":
            if _solve_slider(page, selectors):
                logger.info("Slider solved!")
                return True
            _refresh_captcha(page, selectors)
            time.sleep(2)
            continue

        if captcha_type != "click":
            for refresh_i in range(5):
                logger.info(f"Got {captcha_type}, refreshing ({refresh_i + 1}/5)...")
                _refresh_captcha(page, selectors)
                time.sleep(2)
                captcha_type = _detect_captcha_type_js(page)
                logger.info(f"After refresh: {captcha_type}")
                if captcha_type == "click":
                    break
                if captcha_type == "slider":
                    if _solve_slider(page, selectors):
                        return True
            if captcha_type != "click":
                continue

        ref_url = _extract_ref_url(page, selectors)
        main_url, main_size = _extract_main_url(page, selectors)

        if not ref_url or not main_url or not main_size:
            logger.warning("Failed to extract captcha image URLs, refreshing...")
            _refresh_captcha(page, selectors)
            time.sleep(1)
            continue

        logger.info(f"Main image size={main_size}")

        # Call LLM solver
        coords = solver.solve(ref_url, main_url, main_size[0], main_size[1])
        if not coords or len(coords) < 2:
            logger.warning(f"LLM returned only {len(coords)} coords, refreshing...")
            _refresh_captcha(page, selectors)
            time.sleep(1)
            continue
        logger.info(f"LLM coords: {coords}")

        # Find main image element for coordinate conversion
        expected_aspect = main_size[0] / main_size[1] if main_size[1] > 0 else 1.0
        image_el = _find_main_image_element(page, selectors, expected_aspect)
        if image_el is None:
            logger.error("Cannot find main image element")
            continue

        box = image_el.bounding_box()
        if box is None:
            continue
        rect = {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}

        scale_x = rect["w"] / main_size[0]
        scale_y = rect["h"] / main_size[1]
        logger.info(f"Image region: rect={rect}, scale=({scale_x:.3f}, {scale_y:.3f})")

        # Click in order
        for i, (cx, cy) in enumerate(coords[:3]):
            px = rect["x"] + cx * scale_x
            py = rect["y"] + cy * scale_y
            logger.info(f"Click #{i + 1}: pixel=({cx},{cy}) -> screen=({px:.1f},{py:.1f})")
            page.mouse.click(px, py)
            time.sleep(random.uniform(0.25, 0.55))

        time.sleep(1)

        # Wait for confirm button and click
        confirm_sel = selectors.get("confirm_btn")
        if confirm_sel:
            try:
                cfm = page.wait_for_selector(confirm_sel, state="visible", timeout=3000)
                # Wait for disabled class to be removed
                time.sleep(0.5)
                cfm.click()
                logger.info("Clicked confirm button")
                time.sleep(2)
            except Exception:
                logger.info("Confirm button still disabled after clicks")

        time.sleep(2)
        if _check_passed(page):
            logger.info("Captcha passed!")
            return True

        logger.info("Not passed, refreshing...")
        if _has_rk001(page):
            logger.error("Detected RK001 after captcha attempt; stop.")
            return False
        _refresh_captcha(page, selectors)
        time.sleep(1)

    logger.error("All captcha retries failed")
    return False


# ======================================================================
# Slider captcha
# ======================================================================

def _solve_slider(page: Page, selectors: dict) -> bool:
    """Solve slider captcha using LLM."""
    bg_el = page.query_selector(selectors["slider_bg_img"])
    if bg_el is None:
        bg_el = page.query_selector(selectors["verify_bg_img"])
    if bg_el is None:
        return False

    bg_url = None
    tag = (bg_el.evaluate("el => el.tagName") or "").lower() if bg_el else ""
    if tag == "img":
        bg_url = bg_el.get_attribute("src") or ""
    if not bg_url or not bg_url.startswith("http"):
        style = (bg_el.get_attribute("style") or "") if bg_el else ""
        m = re.search(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
        if m:
            bg_url = m.group(1)

    if not bg_url:
        try:
            bg_bytes = bg_el.screenshot()
            import base64
            bg_url = "data:image/png;base64," + base64.b64encode(bg_bytes).decode()
        except Exception:
            return False

    groove = page.query_selector(selectors["slider_groove"])
    slider_block = page.query_selector(selectors["slider_block"])
    if groove is None or slider_block is None:
        return False

    groove_box = groove.bounding_box()
    if groove_box is None:
        return False
    groove_width = groove_box["width"]

    block_box = slider_block.bounding_box()
    if block_box is None:
        return False

    try:
        import base64
        from openai import OpenAI
        import const

        if bg_url.startswith("http"):
            resp = requests.get(bg_url, timeout=15)
            bg_data = resp.content
        elif bg_url.startswith("data:"):
            _, encoded = bg_url.split(",", 1)
            bg_data = base64.b64decode(encoded)
        else:
            return False

        bg_uri = "data:image/png;base64," + base64.b64encode(bg_data).decode()
        img = Image.open(io.BytesIO(bg_data))
        bg_w, bg_h = img.size

        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": bg_uri}},
                {"type": "text", "text": (
                    f"This is a slider captcha background ({bg_w}x{bg_h} px).\n"
                    "Find the gap and return the X ratio (0~1) of its left edge.\n"
                    "Output format (single number): 0.XX"
                )},
            ],
        }]

        # Endpoint chain: primary endpoint (const) plus optional
        # LLM_FALLBACK_MODELS entries (same syntax as click_captcha_solver.py)
        endpoints = [(const.LLM_BASE_URL, const.LLM_API_KEY, const.LLM_MODEL)]
        for item in os.getenv("LLM_FALLBACK_MODELS", "").split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split("::")
            if len(parts) == 3:
                endpoints.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
            elif len(parts) == 1:
                endpoints.append((const.LLM_BASE_URL, const.LLM_API_KEY, item))

        output = ""
        for i, (ep_url, ep_key, model_name) in enumerate(endpoints):
            try:
                client = OpenAI(base_url=ep_url, api_key=ep_key, timeout=120.0)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    max_tokens=1024,
                )
                output = response.choices[0].message.content or ""
                if not output.strip():
                    logger.warning(f"Slider model {model_name} returned empty content")
                    continue
                if i > 0:
                    logger.info(f"Slider fallback endpoint {ep_url} / {model_name} succeeded")
                break
            except Exception as e:
                logger.warning(f"Slider model {model_name} failed: {e}")
                output = ""
        if not output:
            return False

        logger.info(f"Slider LLM response: {output[:100]}")
        nums = re.findall(r'(\d+\.?\d*)', output)
        if not nums:
            return False
        ratio = float(nums[0])
        if ratio > 1.5:
            ratio = ratio / bg_w
        ratio = max(0.0, min(1.0, ratio))

        track_width = groove_width - block_box["width"]
        drag_distance = int(ratio * track_width)
        drag_distance = max(10, min(drag_distance, track_width))
        logger.info(f"Drag distance: {drag_distance}px")

        # Simulate drag with mouse
        start_x = block_box["x"] + block_box["width"] / 2
        start_y = block_box["y"] + block_box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()

        # Multi-segment drag
        segments = random.randint(3, 5)
        remaining = drag_distance
        for _ in range(segments - 1):
            step = random.randint(int(remaining * 0.2), int(remaining * 0.5))
            remaining -= step
            page.mouse.move(start_x + drag_distance - remaining, start_y + random.randint(-1, 1), steps=1)
            time.sleep(random.uniform(0.02, 0.08))

        page.mouse.move(start_x + drag_distance, start_y + random.randint(-1, 1), steps=1)
        time.sleep(random.uniform(0.1, 0.2))
        page.mouse.up()

        time.sleep(2)
        return _check_passed(page)

    except Exception as e:
        logger.error(f"Slider solve error: {e}")
        raise RuntimeError(f"LLM 调用失败: {e}") from e


# ======================================================================
# Image URL extraction
# ======================================================================

def _extract_ref_url(page: Page, selectors: dict) -> Optional[str]:
    el = page.query_selector(selectors["header_answer_img"])
    if el is None:
        return None
    return el.get_attribute("src") or None


def _extract_main_url(page: Page, selectors: dict) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    for sel in ["verify_bg_img", "point_area", "click_type_wrap", "verify_bg"]:
        el = page.query_selector(selectors[sel])
        if el is None:
            continue

        tag = (el.evaluate("el => el.tagName") or "").lower()
        if tag == "img":
            src = el.get_attribute("src") or ""
            if src:
                size = _get_image_size_from_url(src)
                return src, size

        style = el.get_attribute("style") or ""
        url_match = re.search(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
        if url_match:
            url = url_match.group(1)
            size = _get_image_size_from_url(url)
            return url, size

    return None, None


def _get_image_size_from_url(url: str) -> Optional[Tuple[int, int]]:
    if not url or not url.startswith("http"):
        return None
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            img = Image.open(io.BytesIO(resp.content))
            return img.size
    except Exception:
        pass
    return None


# ======================================================================
# Captcha type detection (Playwright version)
# ======================================================================

def _detect_captcha_type_js(page: Page) -> str:
    """Detect only visible Tencent captcha widgets using Playwright evaluate.

    Key: strict visibility filtering excludes hidden pre-loaded DOMs
    (e.g. elements at top: -1000000px or with zero-size images).
    """
    try:
        result = page.evaluate("""() => {
            function isVisible(el) {
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity) === 0) return false;
                var r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 20) return false;
                if (r.bottom <= 0 || r.right <= 0) return false;
                // Exclude elements far off-screen (hidden DOMs use top: -1000000px)
                if (r.top < -500 || r.left < -500) return false;
                if (r.top > window.innerHeight + 500) return false;
                return true;
            }
            function visibleEls(sel) {
                return Array.from(document.querySelectorAll(sel)).filter(isVisible);
            }
            function textOf(sel) {
                var els = visibleEls(sel);
                for (var i = 0; i < els.length; i++) {
                    var text = (els[i].textContent || els[i].innerText || '').trim();
                    if (text) return text;
                }
                return '';
            }
            function exists(sel) {
                return visibleEls(sel).length > 0;
            }
            function hasRenderableImage(sel) {
                var els = visibleEls(sel);
                for (var i = 0; i < els.length; i++) {
                    var r = els[i].getBoundingClientRect();
                    var src = els[i].getAttribute('src') || '';
                    var style = els[i].getAttribute('style') || '';
                    if (r.width >= 80 && r.height >= 60 && (src || /url\\(/.test(style))) {
                        // For IMG tags, verify the image actually loaded
                        if (els[i].tagName === 'IMG') {
                            if (els[i].naturalWidth > 0 && els[i].naturalHeight > 0) return true;
                            if (src && src.startsWith('http')) return true;
                        } else {
                            return true;
                        }
                    }
                }
                return false;
            }

            var prompt = textOf('.tencent-captcha-dy__header-text') ||
                         textOf('.tencent-captcha-dy__question') ||
                         textOf('.tencent-captcha-dy__title') || '';

            // Click-type: Chinese + English prompt patterns
            var hasClickImage = hasRenderableImage('.tencent-captcha-dy__verify-bg-img') ||
                                hasRenderableImage('.tencent-captcha-dy__point-area') ||
                                hasRenderableImage('.tencent-captcha-dy__click-type-wrap') ||
                                exists('.tencent-captcha-dy__header-answer img');
            var hasClickPrompt = /依次点击|顺序点击|点击下图|文字点选|请点击|click/i.test(prompt);
            if (hasClickImage && (hasClickPrompt ||
                exists('.tencent-captcha-dy__click-word') ||
                exists('.tencent-captcha-dy__point-area') ||
                exists('.tencent-captcha-dy__header-answer'))) {
                return 'click';
            }

            // Slider-type: Chinese + English prompt patterns
            var hasSlider = exists('.tencent-captcha-dy__slider-groove') &&
                            exists('.tencent-captcha-dy__slider-block') &&
                            (hasRenderableImage('.tencent-captcha-dy__slider-bg-img') ||
                             hasRenderableImage('.tencent-captcha-dy__verify-bg-img'));
            if (hasSlider && /拖动|拼图|滑块|slide/i.test(prompt)) return 'slider';

            // Fallback: if renderable images exist but prompt is unclear, default to click
            if (hasClickImage) return 'click';
            if (hasSlider) return 'slider';

            return 'unknown';
        }""")
        return result or "unknown"
    except Exception:
        pass
    return "unknown"


def _wait_for_captcha(page: Page, timeout: int) -> bool:
    """Wait for a VISIBLE captcha widget (not just DOM-attached).

    Uses polling to handle cases where the captcha DOM is pre-loaded but hidden.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _has_visible_captcha_widget(page):
            return True
        time.sleep(0.5)
    return False


def _has_visible_captcha_widget(page: Page) -> bool:
    """Check if any captcha widget is truly visible (not off-screen or hidden)."""
    try:
        return page.evaluate("""(selectors) => {
            function isVisible(el) {
                if (!el) return false;
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity) === 0) return false;
                var r = el.getBoundingClientRect();
                // Must be reasonably sized and within viewport
                if (r.width < 80 || r.height < 80) return false;
                if (r.bottom <= 0 || r.right <= 0) return false;
                // Critical: exclude hidden pre-loaded DOMs (top: -1000000px etc.)
                if (r.top < -500 || r.left < -500) return false;
                if (r.top > window.innerHeight + 500) return false;
                return true;
            }
            for (var i = 0; i < selectors.length; i++) {
                var els = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < els.length; j++) {
                    if (isVisible(els[j])) return true;
                }
            }
            return false;
        }""", _WIDGET_SELECTORS)
    except Exception:
        return False


def _has_rk001(page: Page) -> bool:
    """Check for RK001 risk-control response in page content."""
    try:
        body_text = page.text_content("body") or ""
        page_source = page.content() or ""
        for keyword in ["RK001", "risk_control", "riskControl"]:
            if keyword in body_text or keyword in page_source:
                return True
    except Exception:
        pass
    return False


def _find_main_image_element(page: Page, selectors: dict,
                             expected_aspect: float = None):
    """Find visible image element for coordinate conversion."""
    best = None
    best_aspect_diff = float('inf')
    for sel in ["verify_bg_img", "image_area", "point_area", "click_type_wrap", "verify_bg"]:
        sel_name = selectors.get(sel)
        if not sel_name:
            continue
        els = page.query_selector_all(sel_name)
        for el in els:
            try:
                box = el.bounding_box()
                if box is None or box["width"] < 80 or box["height"] < 80:
                    continue
                if expected_aspect:
                    aspect = box["width"] / box["height"]
                    diff = abs(aspect - expected_aspect) / expected_aspect
                    if diff < best_aspect_diff:
                        best_aspect_diff = diff
                        best = el
                else:
                    best = el
                    break
            except Exception:
                pass
        if best and not expected_aspect:
            break
    return best


def _check_passed(page: Page) -> bool:
    """Check if captcha has been passed (page navigated away from login)."""
    from urllib.parse import urlparse
    if "/login" not in urlparse(page.url).path:
        return True
    try:
        body_text = page.text_content("body") or ""
        if any(kw in body_text for kw in ["登录成功", "验证成功", "success"]):
            return True
    except Exception:
        pass
    # Check if captcha container still visible
    el = page.query_selector("#tCaptchaDyContent")
    if el is None or not el.is_visible():
        time.sleep(1)
        el2 = page.query_selector("#tCaptchaDyContent")
        if el2 is None or not el2.is_visible():
            return True
    return False


def _refresh_captcha(page: Page, selectors: dict):
    """Refresh captcha using multiple strategies."""
    btn = page.query_selector(selectors["refresh_btn"])
    if btn and btn.is_visible():
        btn.click()
        return

    # JS fallback
    try:
        page.evaluate("""() => {
            var els = document.querySelectorAll('[class*="refresh"], [class*="footer-icon"]');
            for (var i = 0; i < els.length; i++) {
                if (els[i].offsetParent !== null && els[i].getBoundingClientRect().width > 5) {
                    els[i].click();
                    return;
                }
            }
        }""")
    except Exception:
        pass
