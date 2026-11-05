import logging
import os
import re
import time
import json
import shutil
import subprocess

import random
import base64
import sqlite3
import requests
import traceback
from datetime import datetime
from io import BytesIO
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PltTimeout
from sensor_updator import SensorUpdator
from error_watcher import ErrorWatcher
from typing import Optional, Tuple

from const import *

# Cookie 持久化文件路径（已禁用）
_COOKIE_FILE = os.path.join(get_data_dir(), "sgcc_cookies.json")

import numpy as np
from captcha_playwright import solve_captcha_in_browser
from captcha_text_sequence import solve_text_sequence_captcha
import vue_state

# CloakBrowser: C++ 源码级隐身 Chromium，Playwright 的 drop-in 替代
# 延迟导入，避免 DEBUG_MODE 下无意义的联网更新检查
_cloak_launch = None
_HAS_CLOAKBROWSER = None

def _get_cloak_launch():
    global _cloak_launch, _HAS_CLOAKBROWSER
    if _HAS_CLOAKBROWSER is None:
        try:
            from cloakbrowser import launch as _cloak_launch
            _HAS_CLOAKBROWSER = True
        except ImportError:
            _HAS_CLOAKBROWSER = False
    return _cloak_launch if _HAS_CLOAKBROWSER else None

class DataFetcher:

    def __init__(self, username: str, password: str, shrq_username: str = None, shrq_password: str = None, pdwly_id: str = None):
        if 'PYTHON_IN_DOCKER' not in os.environ:
            import dotenv
            dotenv.load_dotenv(verbose=True)
        self._username = username
        self._password = password
        # 二开新增：上海燃气 / 浦东威立雅数据源
        self.shrq_username = shrq_username
        self.shrq_password = shrq_password
        self.pdwly_id = pdwly_id
        self.shrq_token = None
        self.shrq_userid = None
        self.connect = None
        self.db_name_pdwly_bill = None
        self.db_name_shrq_bill = None
        self.header = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,sq;q=0.7',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Origin': 'https://www.shgas.com.cn',
            'Pragma': 'no-cache',
            'Referer': 'https://www.shgas.com.cn/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'token': '',
            'Content-Type': 'application/json;charset=UTF-8'
        }

        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 60))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
        self.IGNORE_USER_ID = os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",")
        self._user_name_map = {}
        raw_names = os.getenv("USER_NAMES", "")
        if raw_names:
            for pair in raw_names.split(","):
                if ":" in pair:
                    uid, name = pair.split(":", 1)
                    self._user_name_map[uid.strip()] = name.strip()
        self._init_db()

    def _init_db(self):
        self.db_type = os.getenv("DB_TYPE", "None").lower()
        if self.db_type in ("sqlite", "mysql"):
            from db import create_db
            self.db = create_db(self.db_type)
            logging.info(f"使用 {self.db_type.upper()} 数据库存储数据。")
        else:
            self.db = None
            logging.info("不使用数据库存储数据。")

    def _click_button(self, page, selector: str):
        page.wait_for_selector(selector, state="visible", timeout=self.DRIVER_IMPLICITY_WAIT_TIME * 1000)
        page.click(selector)
        self._random_delay(0.1, 0.5)


    def insert_expand_data(self, data:dict):
        self.db.insert_expand_data(data)

    def _setup_browser(self):
        """Launch or connect to browser with full stealth.

        Supports two modes:
        1. CDP connect mode (CHROME_CDP_URL set): connect to a real Chrome
           running with --remote-debugging-port. Best anti-detection.
        2. Launch mode (default): launch Playwright Chromium with stealth.
        """
        # 先清理旧 Playwright 实例，防止重复调用时残留浏览器窗口
        if getattr(self, '_pw', None) is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None

        browser_lang = os.getenv("BROWSER_LANGUAGE", "zh-CN,zh,en-US,en")
        browser_ua = os.getenv("BROWSER_USER_AGENT", "")
        device_scale = float(os.getenv("BROWSER_DEVICE_SCALE_FACTOR", "1"))
        ws = os.getenv("BROWSER_WINDOW_SIZE", "1920,1080").split(",")
        vw, vh = int(ws[0]), int(ws[1])
        primary_lang = browser_lang.split(",")[0] if browser_lang else "zh-CN"
        langs = [x.strip() for x in browser_lang.split(",") if x.strip()] or ["zh-CN","zh","en-US","en"]

        cdp_mode = False

        # ── CDP 接管模式：连接宿主机上运行的真实 Chrome/Edge ──
        cdp_url = os.getenv("CHROME_CDP_URL", "").strip()
        if cdp_url:
            if cdp_url.startswith("ws://"):
                cdp_url = cdp_url.replace("ws://", "http://", 1)
            elif cdp_url.startswith("wss://"):
                cdp_url = cdp_url.replace("wss://", "https://", 1)
            logging.info(f"CDP 接管模式: 连接真实浏览器 {cdp_url}")
            self._pw = sync_playwright().start()
            try:
                self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                logging.error(f"CDP 连接失败: {e}")
                try:
                    self._pw.stop()
                except Exception:
                    pass
                raise
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                self._page = pages[0] if pages else self._context.new_page()
            else:
                self._context = self._browser.new_context()
                self._page = self._context.new_page()
            # CDP 模式：保留真实浏览器的 Cookie（避免触发 RK001）
            logging.info("CDP 模式: 保留真实浏览器 Cookie")
            # CDP 模式：读取真实浏览器的 UA
            if not browser_ua:
                try:
                    browser_ua = self._page.evaluate("() => navigator.userAgent")
                except Exception:
                    pass
                if not browser_ua:
                    browser_ua = self._get_chrome_user_agent()
            cdp_mode = True
            logging.info(f"CDP 连接成功，真实 UA: {browser_ua[:60]}...")

        # ── 标准启动模式 ──
        cloak_mode = False
        if not cdp_mode:
            # headless 逻辑：无显示器时自动 headless（兼容 xvfb 和无 xvfb 两种 Docker 环境）
            headless = False
            if os.environ.get("DISPLAY") is None:
                headless = True
            # DEBUG_MODE 本机调试时强制显示浏览器窗口
            if os.environ.get("PYTHON_IN_DOCKER") is None and os.getenv("DEBUG_MODE", "false").lower() == "true":
                headless = False
                logging.info("DEBUG_MODE: 显示浏览器窗口，可观察完整操作过程")

            # ── 优先使用 CloakBrowser（C++ 源码级隐身 Chromium） ──
            # DEBUG_MODE 本机调试时跳过 CloakBrowser，使用标准 Playwright 更稳定
            use_cloak = (
                os.environ.get("PYTHON_IN_DOCKER") is None
                and os.getenv("DEBUG_MODE", "false").lower() != "true"
            )
            if use_cloak:
                cloak_fn = _get_cloak_launch()
                if cloak_fn:
                    try:
                        logging.info("使用 CloakBrowser 启动隐身浏览器...")
                        self._browser = cloak_fn(
                            headless=headless,
                            timezone="Asia/Shanghai",
                            locale=primary_lang,
                            humanize=False,
                        )
                        self._context = self._browser.new_context(
                            viewport={"width": vw, "height": vh},
                            locale=primary_lang.replace("-", "_"),
                            timezone_id="Asia/Shanghai",
                            device_scale_factor=device_scale,
                            is_mobile=False,
                            has_touch=False,
                            extra_http_headers={"Accept-Language": browser_lang},
                        )
                        # 不设置 user_agent —— CloakBrowser 的 C++ 级指纹已覆盖
                        if not browser_ua:
                            _tmp_page = self._browser.new_page()
                            try:
                                browser_ua = _tmp_page.evaluate("() => navigator.userAgent")
                                logging.info("CloakBrowser UA: " + browser_ua[:60] + "...")
                            finally:
                                _tmp_page.close()
                        cloak_mode = True
                        # CloakBrowser 内部管理 Playwright 实例，外部不需要 stop()
                        self._pw = None
                        logging.info("CloakBrowser 启动成功")
                    except Exception as e:
                        logging.warning(f"CloakBrowser 启动失败，回退到 Playwright: {e}")
                        try:
                            if hasattr(self, '_browser') and self._browser is not None:
                                self._browser.close()
                        except Exception:
                            pass
                        self._browser = None
                        self._context = None
                        self._pw = sync_playwright().start()

            # ── 回退：标准 Playwright Chromium ──
            if not cloak_mode:
                if self._pw is None:
                    self._pw = sync_playwright().start()
                if not browser_ua:
                    browser_ua = self._get_chrome_user_agent()
                    logging.info("Playwright UA: " + browser_ua[:50] + "...")

                args = [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--disable-client-side-phishing-detection",
                    "--disable-crash-reporter",
                    "--disable-domain-reliability",
                    "--disable-component-update",
                    f"--window-size={vw},{vh}",
                ]
                if os.environ.get("PYTHON_IN_DOCKER") is not None:
                    args.extend([
                        "--disable-gpu",
                        "--use-gl=swiftshader",
                        "--disable-software-rasterizer",
                        "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
                        "--lang=zh-CN",
                    ])
                if headless:
                    args.append("--headless=new")

                # 使用 launch_persistent_context 确保只有一个浏览器窗口
                # （launch + new_context 在非 headless 模式下会产生额外窗口）
                self._context = self._pw.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=headless,
                    args=args,
                    viewport={"width": vw, "height": vh},
                    user_agent=browser_ua,
                    locale=primary_lang.replace("-", "_"),
                    timezone_id="Asia/Shanghai",
                    device_scale_factor=device_scale,
                    no_viewport=False,
                )
                self._browser = self._context.browser

        # ── 以下为两种模式共享的反检测设置 ──

        ua_u = browser_ua.upper()
        if "WINDOWS" in ua_u or "WIN64" in ua_u:
            ua_platform = "Win32"
            ua_os = "Windows"
            ua_os_lc = "win"
            ua_platform_version = "10.0"
            ua_wow64 = True
        else:
            ua_platform = "Linux x86_64"
            ua_os = "Linux"
            ua_os_lc = "linux"
            ua_platform_version = "6.8.0"
            ua_wow64 = False

        cr = re.search(r"Chrome/(\d+)", browser_ua)
        cm = cr.group(1) if cr else "120"
        L = json.dumps(langs)
        PL = json.dumps(primary_lang)
        UP = json.dumps(ua_platform)
        OL = json.dumps(ua_os_lc)
        OS = json.dumps(ua_os)
        CM = json.dumps(cm)
        VW = json.dumps(vw)
        VH = json.dumps(vh)
        FPV = json.dumps(cm + ".0.0.0")
        FPVV = json.dumps(ua_platform_version)

        # init_script: runs BEFORE any page JS — zero detection window
        if cdp_mode:
            # CDP 模式：真实浏览器指纹已经很可信，只隐藏自动化痕迹
            init_js = (
                "(() => {"
                # --- 关键：隐藏 navigator.webdriver ---
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                # --- 隐藏 Playwright/CDP 自动化痕迹 ---
                "delete window.__playwright;"
                "delete window.__pw_manual;"
                # --- Permissions API 修复 ---
                "const oq=(navigator.permissions||{}).query;"
                "if(oq)navigator.permissions.query=function(p){"
                "if(p.name==='notifications')return Promise.resolve({state:'prompt',onchange:null});"
                "if(p.name==='geolocation')return Promise.resolve({state:'prompt',onchange:null});"
                "return oq.call(navigator.permissions,p);};"
                "})();"
            )
            logging.info("CDP 模式: 使用精简反检测脚本 (仅隐藏自动化痕迹)")
        elif cloak_mode:
            # CloakBrowser 模式：C++ 级指纹已覆盖，不要覆盖任何指纹属性！
            # CloakBrowser 原生 webdriver=false (boolean)，不要改成 undefined
            # 只需隐藏 Playwright 运行时痕迹
            init_js = (
                "(() => {"
                # --- 隐藏 Playwright 运行时痕迹（不碰 navigator 属性） ---
                "delete window.__playwright;"
                "delete window.__pw_manual;"
                "})();"
            )
            logging.info("CloakBrowser 模式: 使用最小化脚本 (仅清理 Playwright 痕迹)")
        else:
            # 标准模式：完整的指纹伪装
            init_js = (
            "(() => {"
            # --- navigator.webdriver ---
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            # --- WebGL renderer spoof ---
            "const patchWebGL = (proto) => {if(!proto||!proto.getParameter)return;"
            "const orig=proto.getParameter;proto.getParameter=function(p){"
            "if(p===37445)return'Intel Inc.';if(p===37446)return'Intel(R) UHD Graphics 620';"
            "return orig.apply(this,arguments);};};"
            "if(typeof WebGLRenderingContext!=='undefined')"
            "patchWebGL(WebGLRenderingContext.prototype);"
            "if(typeof WebGL2RenderingContext!=='undefined')"
            "patchWebGL(WebGL2RenderingContext.prototype);"
            # --- language / platform / hardware ---
            "Object.defineProperty(navigator,'language',{get:()=>"+PL+"});"
            "Object.defineProperty(navigator,'languages',{get:()=>Object.freeze("+L+")});"
            "Object.defineProperty(navigator,'platform',{get:()=>"+UP+"});"
            "Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});"
            "Object.defineProperty(navigator,'deviceMemory',{get:()=>8});"
            "Object.defineProperty(navigator,'maxTouchPoints',{get:()=>0});"
            "Object.defineProperty(navigator,'doNotTrack',{get:()=>null});"
            "Object.defineProperty(navigator,'connection',{get:()=>({"
            "effectiveType:'4g',rtt:50,downlink:10,saveData:false})});"
            # --- realistic PDF plugins ---
            "const _pdfMimes=[{type:'application/pdf',suffixes:'pdf'},"
            "{type:'application/x-nacl',suffixes:''}];"
            "const _pdfNames=["
            "{name:'PDF Viewer',filename:'internal-pdf-viewer',description:'Portable Document Format'},"
            "{name:'Chrome PDF Viewer',filename:'internal-pdf-viewer',description:'Portable Document Format'},"
            "{name:'Chromium PDF Viewer',filename:'internal-pdf-viewer',description:'Portable Document Format'},"
            "{name:'Microsoft Edge PDF Viewer',filename:'internal-pdf-viewer',description:'Portable Document Format'},"
            "{name:'WebKit built-in PDF',filename:'internal-pdf-viewer',description:'Portable Document Format'}];"
            "const _plugins=_pdfNames.map((n,i)=>{"
            "const p=Object.create(Plugin.prototype);"
            "Object.defineProperties(p,{name:{value:n.name,enumerable:true},"
            "filename:{value:n.filename,enumerable:true},"
            "description:{value:n.description,enumerable:true},"
            "length:{value:_pdfMimes.length,enumerable:true}});"
            "_pdfMimes.forEach((m,mi)=>{const mt=Object.create(MimeType.prototype);"
            "Object.defineProperties(mt,{type:{value:m.type,enumerable:true},"
            "suffixes:{value:m.suffixes,enumerable:true},"
            "description:{value:m.type,enumerable:true},"
            "enabledPlugin:{value:p,enumerable:true}});p[mi]=mt;});return p;});"
            "Object.defineProperty(navigator,'plugins',{get:()=>{"
            "const list=_plugins.slice();"
            "list.item=function(i){return this[i]||null;};"
            "list.namedItem=function(n){return this.find(p=>p.name===n)||null;};"
            "list.refresh=function(){};return list;}});"
            "Object.defineProperty(navigator,'mimeTypes',{get:()=>_pdfMimes});"
            # --- navigator.userAgentData ---
            "const _uaData={brands:["
            "{brand:'Chromium',version:"+CM+"},"
            "{brand:'Google Chrome',version:"+CM+"},"
            "{brand:'Not A;Brand',version:'99'}],"
            "mobile:false,platform:"+OS+"};"
            "const _highEntropy={brands:_uaData.brands,mobile:false,"
            "platform:"+OS+",platformVersion:"+FPVV+","
            "architecture:'x86_64',model:'',uaFullVersion:"+FPV+","
            "fullVersionList:["
            "{brand:'Chromium',version:"+FPV+"},"
            "{brand:'Google Chrome',version:"+FPV+"},"
            "{brand:'Not A;Brand',version:'99.0.0.0'}],"
            "bitness:'64',wow64:false};"
            "const _uaProxy=new Proxy(_uaData,{"
            "get(t,p){if(p==='getHighEntropyValues')return function(){"
            "return Promise.resolve(Object.assign({},_highEntropy));};"
            "if(p==='toJSON')return function(){return{brands:t.brands,mobile:t.mobile,platform:t.platform};};"
            "return t[p];}});"
            "Object.defineProperty(navigator,'userAgentData',{get:()=>_uaProxy});"
            # --- window.chrome ---
            "Object.defineProperty(window,'chrome',{get:()=>({"
            "app:{isInstalled:false,InstallState:{DISABLED:'disabled',INSTALLED:'installed',NOT_INSTALLED:'not_installed'},"
            "RunningState:{CANNOT_RUN:'cannot_run',READY_TO_RUN:'ready_to_run',RUNNING:'running'}},"
            "runtime:{"
            "PlatformOs:"+OL+",PlatformArch:'x86-64',"
            "PlatformNaclArch:'x86-64',"
            "OnInstalledReason:{CHROME_UPDATE:'chrome_update',INSTALL:'install',"
            "SHARED_MODULE_UPDATE:'shared_module_update',UPDATE:'update'},"
            "OnRestartRequiredReason:{APP_UPDATE:'app_update',OS_UPDATE:'os_update',PERIODIC:'periodic'},"
            "RequestUpdateCheckStatus:{NO_UPDATE:'no_update',THROTTLED:'throttled',UPDATE_AVAILABLE:'update_available'},"
            "connect:function(){return{onDisconnect:{addListener:function(){}},onMessage:{addListener:function(){}},postMessage:function(){}};}"
            ",sendMessage:function(cb){if(cb)cb();}"
            "}})});"
            # --- window / screen dimensions ---
            "Object.defineProperty(window,'outerWidth',{get:()=>"+VW+"});"
            "Object.defineProperty(window,'outerHeight',{get:()=>"+VH+"});"
            "Object.defineProperty(window,'innerWidth',{get:()=>"+VW+"});"
            "Object.defineProperty(window,'innerHeight',{get:()=>"+VH+"});"
            "Object.defineProperty(screen,'width',{get:()=>"+VW+"});"
            "Object.defineProperty(screen,'height',{get:()=>"+VH+"});"
            "Object.defineProperty(screen,'availWidth',{get:()=>"+VW+"});"
            "Object.defineProperty(screen,'availHeight',{get:()=>"+VH+"});"
            "Object.defineProperty(screen,'colorDepth',{get:()=>24});"
            "Object.defineProperty(screen,'pixelDepth',{get:()=>24});"
            "Object.defineProperty(screen,'orientation',{get:()=>({type:'landscape-primary',angle:0})});"
            # --- Permissions API ---
            "const oq=(navigator.permissions||{}).query;"
            "if(oq)navigator.permissions.query=function(p){"
            "if(p.name==='notifications')return Promise.resolve({state:'prompt',onchange:null});"
            "if(p.name==='geolocation')return Promise.resolve({state:'prompt',onchange:null});"
            "return oq.call(navigator.permissions,p);};"
            # --- Notification ---
            "if(typeof Notification!=='undefined'){"
            "Object.defineProperty(Notification,'permission',{get:()=>'default'});}"
            "})();"
        )
        self._context.add_init_script(init_js)
        mode_name = "CDP" if cdp_mode else ("CloakBrowser" if cloak_mode else "标准")
        logging.info(f"反检测 init_script 已注入 ({mode_name}模式)")

        # CDP-level UserAgentMetadata — keeps Sec-CH-UA-* headers consistent
        # CDP connect 模式：browser-level CDP session 可能不支持
        # CloakBrowser 模式：C++ 级指纹已覆盖，不需要额外设置
        if not cdp_mode and not cloak_mode:
            try:
                cdp = self._browser.new_browser_cdp_session()
                cdp.send("Network.setUserAgentOverride", {
                    "userAgent": browser_ua,
                    "acceptLanguage": browser_lang,
                    "platform": ua_platform,
                    "userAgentMetadata": {
                        "brands": [
                            {"brand": "Chromium", "version": cm},
                            {"brand": "Google Chrome", "version": cm},
                            {"brand": "Not A;Brand", "version": "99"},
                        ],
                        "fullVersionList": [
                            {"brand": "Chromium", "version": cm + ".0.0.0"},
                            {"brand": "Google Chrome", "version": cm + ".0.0.0"},
                            {"brand": "Not A;Brand", "version": "99.0.0.0"},
                        ],
                        "fullVersion": cm + ".0.0.0",
                        "platform": ua_os,
                        "platformVersion": ua_platform_version,
                        "architecture": "x86_64",
                        "model": "",
                        "mobile": False,
                        "bitness": "64",
                        "wow64": False,  # 64-bit Chrome: wow64=false (仅32位Chrome在64位Windows上为true)
                    },
                })
                logging.info("CDP UserAgentMetadata set (Chrome/" + cm + ", platform=" + ua_os + ")")
            except Exception as e:
                logging.warning(f"CDP UserAgentMetadata failed (non-fatal): {e}")

        if cdp_mode:
            # CDP 模式：复用已有 page，但需要导航一次以触发 init_script
            self._page.set_default_timeout(self.DRIVER_IMPLICITY_WAIT_TIME * 1000)
            logging.info("Playwright browser ready (CDP + full stealth)")
        else:
            # launch_persistent_context 已自动创建首个页面，直接复用它，
            # 不要再 new_page()，否则会多开一个浏览器窗口/标签页。
            _pages = self._context.pages
            self._page = _pages[0] if _pages else self._context.new_page()
            self._page.set_default_timeout(self.DRIVER_IMPLICITY_WAIT_TIME * 1000)
            mode_label = "CloakBrowser" if cloak_mode else "standard"
            logging.info(f"Browser ready ({mode_label} + stealth)")
            # 非 CDP 模式：尝试加载已保存的 Cookie（已禁用）
            # self._load_cookies(self._context)
        return (self._browser, self._context, self._page)

    @staticmethod
    def _detect_chrome_major_version():
        import glob as _g
        patterns = [
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        for p in patterns:
            binaries = _g.glob(p) if "*" in p else ([p] if os.path.isfile(p) else [])
            for b in binaries:
                try:
                    o = subprocess.check_output([b, "--version"], stderr=subprocess.STDOUT, text=True, timeout=3)
                    m = re.search(r"(\d+)\.", o)
                    if m:
                        return m.group(1)
                except Exception:
                    continue
        return None

    def _get_chrome_user_agent(self):
        v = self._detect_chrome_major_version()
        in_docker = os.environ.get("PYTHON_IN_DOCKER") is not None
        if in_docker:
            # Docker 中伪装为 Windows 用户（国网网站主要用户群体）
            if v:
                return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + v + ".0.0.0 Safari/537.36"
            return self._get_random_user_agent()
        # 非 Docker 保持 Linux UA（与真实运行环境一致）
        if v:
            return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + v + ".0.0.0 Safari/537.36"
        return self._get_random_user_agent()

    def _get_random_user_agent(self):
        ua = random.randint(128, 136)
        in_docker = os.environ.get("PYTHON_IN_DOCKER") is not None
        if in_docker:
            return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + str(ua) + ".0.0.0 Safari/537.36"
        return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" + str(ua) + ".0.0.0 Safari/537.36"

    def _wait_for_captcha_sdk(self, page, timeout: int = 20):
        """等待腾讯 captcha SDK 完全初始化。

        95598 登录页会异步加载腾讯验证码 SDK（captcha.js）。
        SDK 加载完成后才会注册验证码回调和 DOM 事件。
        如果 SDK 未就绪就提交登录，服务端无法验证 captcha token，
        会直接返回 RK001 风控错误。
        """
        logging.info("等待腾讯 captcha SDK 加载...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                sdk_ready = page.evaluate("""() => {
                    // 检查腾讯 captcha 全局对象
                    if (typeof window.TencentCaptcha !== 'undefined') return 'TencentCaptcha';
                    if (typeof window.tc_captcha !== 'undefined') return 'tc_captcha';
                    // 检查 captcha 相关 script 是否已加载
                    var scripts = document.querySelectorAll('script[src*="captcha"], script[src*="tencent"]');
                    var loaded = 0;
                    for (var i = 0; i < scripts.length; i++) {
                        if (scripts[i].getAttribute('src')) loaded++;
                    }
                    if (loaded > 0) return 'scripts:' + loaded;
                    // 检查预加载的 captcha DOM 容器是否已创建
                    var container = document.querySelector('#tCaptchaDyContent, .tencent-captcha-dy__warp');
                    if (container) return 'dom_ready';
                    return '';
                }""")
                if sdk_ready:
                    logging.info(f"腾讯 captcha SDK 已就绪: {sdk_ready}")
                    # SDK 就绪后再额外等待一小段时间让内部初始化完成
                    self._random_delay(1.5, 3.0)
                    return True
            except Exception:
                pass
            time.sleep(1)

        logging.warning(f"腾讯 captcha SDK 等待超时 ({timeout}s)，继续尝试登录")
        # 即使超时也继续，不阻断流程
        return False

    @ErrorWatcher.watch
    def _login(self, page, phone_code=False):
        # 设置 RK001 网络响应拦截器 — 在 API 层面捕获风控
        self._rk001_detected = False

        def _on_response(response):
            try:
                url = response.url
                # 只记录真正的登录/认证 API（JSON 响应），排除页面 HTML 加载
                is_api = any(kw in url for kw in [
                    "/api/login", "/user/login", "/oauth/login",
                    "/login.action", "/doLogin", "/auth/login",
                    "riskControl", "risk_control"
                ])
                if is_api:
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            body = response.text()
                            logging.info(f"[网络] 登录API响应: url={url[:100]}, status={response.status}, body={body[:300]}")
                            if "RK001" in body:
                                self._rk001_detected = True
                                logging.error("[RK001拦截] 登录API返回风控码")
                        else:
                            logging.debug(f"[网络] 非JSON响应: url={url[:100]}, ct={ct}")
                    except Exception:
                        pass
                # 检查专门的风控 API 端点
                if "riskControl" in url or "risk_control" in url:
                    self._rk001_detected = True
                    logging.error(f"[RK001拦截] 风控端点响应: {url[:120]}")
            except Exception:
                pass

        page.on("response", _on_response)
        try:

            # 打开登录页 — 让页面完整加载所有资源（背景图、验证码脚本、登录表单等）
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded",
                          timeout=self.DRIVER_IMPLICITY_WAIT_TIME * 3 * 1000)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
                page.wait_for_selector('.user', state='visible',
                                       timeout=self.DRIVER_IMPLICITY_WAIT_TIME * 3 * 1000)
                self._wait_for_document_ready(page, timeout=self.DRIVER_IMPLICITY_WAIT_TIME)
            except Exception as e:
                logging.error(f"登录页面加载失败: {LOGIN_URL}")
                logging.error(f"异常信息: {e}")
                page.remove_listener("response", _on_response)
                return False

            logging.info(f"打开登录页面: {LOGIN_URL}。\r")

            # 等待腾讯 captcha SDK 完全初始化（关键！SDK 未就绪时提交登录会触发 RK001）
            self._wait_for_captcha_sdk(page)

            # 页面加载完成后，先检查是否已触发 RK001
            if self._rk001_detected or self._is_rk001_blocked(page):
                logging.error("页面加载阶段即检测到 RK001，中止本轮登录以保护账号。")
                page.remove_listener("response", _on_response)
                return False

            # 模拟真实用户：等待 loading 消失后短暂观察页面
            self._random_delay(1.5, 3.0)
            try:
                page.wait_for_selector('.el-loading-mask', state='hidden', timeout=10 * 1000)
            except Exception:
                pass
            element = page.wait_for_selector('.user', state='visible', timeout=self.DRIVER_IMPLICITY_WAIT_TIME * 1000)
            self._random_delay(0.5, 1.5)
            element.click()
            logging.info("已找到 user 元素。\r")

            # 点击账号密码登录 tab
            self._random_delay(1.0, 2.5)
            self._click_button(page, "xpath=" + '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')

            # 点击同意选项
            self._random_delay(0.8, 2.0)
            self._click_button(page, "xpath=" + '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
            logging.info("已点击同意选项。\r")

            # 同意后短暂停顿（模拟用户阅读）
            self._random_delay(1.0, 2.5)

            # 同意选项后再次检查 RK001
            if self._rk001_detected or self._is_rk001_blocked(page):
                logging.error("同意选项后检测到 RK001，中止登录。")
                page.remove_listener("response", _on_response)
                return False

            if phone_code:
                # ── 短信验证码登录流程 ──
                self._click_button(page, "xpath=" + '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
                els = page.query_selector_all('.el-input__inner')
                els[2].fill(self._username)
                logging.info(f"已输入用户名: {self._username}\r")

                # ── 步骤1：点击「发送验证码」──
                self._click_button(page, "xpath=" + '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
                self._random_delay(0.5, 1.5)
                logging.info("已点击发送验证码，等待短信...\r")

                # ── 步骤2：弹出 GUI 对话框输入短信验证码（必须6位数字）──
                import tkinter as tk
                from tkinter import simpledialog, messagebox
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                code = None
                while True:
                    code = simpledialog.askstring("短信验证码", "请输入6位手机短信验证码：", parent=root)
                    if code is None:  # 用户点取消
                        root.destroy()
                        logging.error("未输入验证码，登录取消")
                        page.remove_listener("response", _on_response)
                        return False
                    code = code.strip()
                    if len(code) == 6 and code.isdigit():
                        break
                    messagebox.showwarning("格式错误", f"验证码必须为6位数字，当前输入 {len(code)} 位", parent=root)
                root.destroy()
                els[3].fill(code)
                logging.info(f"已输入验证码: {code}。\r")

                # ── 步骤3：点击登录按钮 ──
                self._random_delay(0.5, 1.5)
                self._click_button(page, "xpath=" + '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
                logging.info("已点击登录按钮。\r")

                # ── 步骤5：文字顺序验证码（DEBUG_MODE 专属，区别于非 debug 的图标点击）──
                # SMS 登录后弹出的是「文字顺序验证码」：需按提示顺序依次点击文字
                # 非 debug 模式（密码登录）弹出的是图标点击验证码，两者 LLM prompt 不同
                logging.info("弹出文字顺序验证码，使用 DEBUG_MODE 专用方案...")
                captcha_passed = solve_text_sequence_captcha(page, LOGIN_URL, self.RETRY_TIMES_LIMIT)
                if captcha_passed:
                    logging.info("验证码已通过，等待页面跳转...")
                    for _ in range(5):
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                        if page.url != LOGIN_URL:
                            logging.info("短信验证码登录成功。\r")
                            page.remove_listener("response", _on_response)
                            return True
                    error = self._get_error_message(page, "//div[@class='errmsg-tip']//span")
                    if error:
                        logging.info(f"验证码通过但登录失败: [{error}]\r")
                    else:
                        try:
                            page.screenshot(path="/data/debug_after_captcha.png")
                            logging.info("已保存诊断截图到 /data/debug_after_captcha.png")
                        except Exception:
                            pass
                        logging.error("验证码已通过但仍停留在登录页面。")
                else:
                    logging.error("文字顺序验证码识别失败。")

                page.remove_listener("response", _on_response)
                return False
            elif self._password is not None and len(self._password) > 0:
                els = page.query_selector_all('.el-input__inner')
                # 模拟真实用户：先点击输入框，再逐个输入
                self._random_delay(0.5, 1.0)
                els[0].click()
                self._random_delay(0.3, 0.8)
                els[0].fill(self._username)
                logging.info(f"已输入用户名: {self._username}\r")
                self._random_delay(0.5, 1.2)
                els[1].click()
                self._random_delay(0.3, 0.8)
                els[1].fill(self._password)
                logging.info("已输入密码。\r")
                # 输入完成后短暂停顿再点击登录
                self._random_delay(1.0, 2.5)
                self._click_button(page, '.el-button.el-button--primary')
                self._random_delay(2.0, 4.0)
                logging.info("已点击登录按钮。\r")

                # 登录按钮点击后：先检查网络拦截，再检查页面内容
                if self._rk001_detected:
                    logging.error("[RK001] 网络拦截器检测到风控，立即中止，不尝试验证码。")
                    page.remove_listener("response", _on_response)
                    return False
                if self._is_rk001_blocked(page):
                    logging.error("[RK001] 页面内容检测到风控码，立即中止。")
                    page.remove_listener("response", _on_response)
                    return False

                if page.url != LOGIN_URL:
                    logging.info("无需验证码登录成功 (已被重定向)。\r")
                    page.remove_listener("response", _on_response)
                    # self._save_cookies(page)
                    return True

                # 再次确认网络层没有 RK001
                if self._rk001_detected:
                    logging.error("[RK001] 验证码前网络拦截器检测到风控，跳过验证码。")
                    page.remove_listener("response", _on_response)
                    return False

                captcha_passed = solve_captcha_in_browser(page, max_retries=self.RETRY_TIMES_LIMIT)
                if captcha_passed:
                    logging.info("验证码已通过，等待页面跳转...")
                    # 等待更长时间让登录 API 完成
                    for wait_i in range(5):
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                        if page.url != LOGIN_URL:
                            logging.info("通过点击验证码登录成功。\r")
                            page.remove_listener("response", _on_response)
                            # self._save_cookies(page)
                            return True
                        # 每次等待时检查是否有错误提示
                        try:
                            diag = page.evaluate("""() => {
                                var info = {errors: [], visible: []};
                                // 收集所有可见的错误消息
                                var errSels = ['.errmsg-tip', '.error-msg', '.el-message--error',
                                    '.el-message__content', '.el-notification__content',
                                    '[class*="error"]', '[class*="errmsg"]', '.msg-text'];
                                for (var i = 0; i < errSels.length; i++) {
                                    var els = document.querySelectorAll(errSels[i]);
                                    for (var j = 0; j < els.length; j++) {
                                        if (els[j].offsetParent !== null) {
                                            var t = (els[j].textContent || '').trim();
                                            if (t) info.errors.push(els[i] + ': ' + t);
                                        }
                                    }
                                }
                                // 检查登录按钮状态
                                var btn = document.querySelector('.el-button.el-button--primary');
                                if (btn) {
                                    info.visible.push('login_btn: text="' + btn.textContent.trim() +
                                        '" disabled=' + btn.disabled +
                                        ' loading=' + btn.classList.contains('is-loading'));
                                }
                                // 检查 loading 遮罩
                                var mask = document.querySelector('.el-loading-mask');
                                if (mask && mask.style.display !== 'none') {
                                    info.visible.push('loading_mask: visible');
                                }
                                return info;
                            }""")
                            if diag.get('errors'):
                                logging.warning(f"页面错误信息: {diag['errors']}")
                            if diag.get('visible'):
                                logging.info(f"页面状态: {diag['visible']}")
                        except Exception as e:
                            logging.debug(f"诊断异常: {e}")
                
                    # 最终检查
                    if page.url != LOGIN_URL:
                        logging.info("通过点击验证码登录成功（延迟跳转）。\r")
                        page.remove_listener("response", _on_response)
                        # self._save_cookies(page)
                        return True
                    else:
                        error = self._get_error_message(page, "//div[@class='errmsg-tip']//span")
                        if error:
                            logging.info(f"验证码通过但登录失败: [{error}]\r")
                        else:
                            # 保存页面截图帮助诊断
                            try:
                                page.screenshot(path="/data/debug_after_captcha.png")
                                logging.info("已保存诊断截图到 /data/debug_after_captcha.png")
                            except Exception:
                                pass
                            logging.error("验证码已通过但仍停留在登录页面。")
                else:
                    logging.error("点击验证码识别在所有重试后均失败。")

        finally:
            page.remove_listener("response", _on_response)

    def _wait_for_document_ready(self, page, timeout: int = 30) -> None:
        page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        time.sleep(1)

    def _get_error_message(self, page, path) -> Optional[str]:
        try:
            el = page.query_selector("xpath=" + path)
            return el.inner_text() if el else None
        except Exception:
            return None

    def _is_rk001_blocked(self, page) -> bool:
        """Check page for RK001 risk control response.

        Uses a tiered approach to avoid false positives:
        1. Network interceptor flag (most reliable)
        2. JS check for visible RK001 text in error containers
        3. Fallback: check visible page text only (not HTML source)
        """
        # Tier 1: network interceptor flag
        if getattr(self, '_rk001_detected', False):
            return True
        try:
            # Tier 2: check if RK001 is visible in error/message containers
            result = page.evaluate("""() => {
                // Check common error message containers
                var errorSelectors = [
                    '.errmsg-tip', '.error-msg', '.err-msg',
                    '.el-message--error', '.el-notification__content',
                    '.tencent-captcha-dy__header-text',
                    '[class*="error"]', '[class*="errmsg"]'
                ];
                for (var i = 0; i < errorSelectors.length; i++) {
                    var els = document.querySelectorAll(errorSelectors[i]);
                    for (var j = 0; j < els.length; j++) {
                        var text = (els[j].textContent || '').trim();
                        if (text && /RK001|risk.?control/i.test(text)) return text;
                    }
                }
                // Check if body visible text contains RK001
                // (only rendered text, not script/style content)
                var walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT,
                    {acceptNode: function(n) {
                        var p = n.parentElement;
                        if (!p) return NodeFilter.FILTER_REJECT;
                        var tag = p.tagName;
                        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT')
                            return NodeFilter.FILTER_REJECT;
                        if (p.offsetParent === null && p.tagName !== 'BODY')
                            return NodeFilter.FILTER_REJECT;
                        return NodeFilter.FILTER_ACCEPT;
                    }}
                );
                var node;
                while (node = walker.nextNode()) {
                    if (/RK001/.test(node.textContent)) return node.textContent.trim();
                }
                return '';
            }""")
            if result:
                logging.error(f"[RK001] 页面可见文本中检测到风控码: {result[:80]}")
                return True
        except Exception as e:
            logging.debug(f"RK001 JS检测异常: {e}")
            # Tier 3: fallback to body text only
            try:
                body_text = page.text_content("body") or ""
                if "RK001" in body_text:
                    logging.error("[RK001] 页面 body 文本中包含风控码")
                    return True
            except Exception:
                pass
        return False

    def _random_delay(self, min_seconds=0.5, max_seconds=3.0):
        """添加随机延迟，使自动化操作更难被检测。"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    # ──────────────────────────────────────────────────────────────
    # DEBUG_MODE 专用：文字顺序验证码
    # 与“非 debug”的图标点击验证码（ClickCaptchaSolver：按参考图标形状/颜色
    # 匹配）完全不同——文字顺序验证码没有参考图标，而是【顶部提示文字】指定
    # 要按什么顺序点击哪些汉字，下方是一组打乱的汉字候选。因此这里采用
    # “整张验证码截图 + 坐标点击”方案，且使用专门面向文字顺序任务的提示词。
    # ──────────────────────────────────────────────────────────────

    # ==================== 二开新增：上海燃气 / 浦东威立雅数据源 ====================
# ddddocr 仅在上海燃气登录验证码识别时使用，延迟导入
    def connect_user_db_pdwly_bill(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            self.connect = sqlite3.connect(os.getenv("DB_NAME"))
            self.connect.cursor()
            logging.info(f"Database of {os.getenv('DB_NAME')} created successfully.")
            try:
                # 创建表名
                self.db_name_pdwly_bill = f"pdwly_bill{user_id}"
                sql = f"CREATE TABLE {self.db_name_pdwly_bill} (date DATE PRIMARY KEY NOT NULL, sumpay REAL NOT NULL, copymeterstatus CHAR, prevmeterdata REAL, meterdata REAL, waternum REAL, dirtynum REAL, waterfee REAL, waterprice REAL, dirtyprice REAL, dirtyfee REAL, paytime CHAR, billstatus CHAR, copymeterdate CHAR, nextdate CHAR);"
                self.connect.execute(sql)
                logging.info(f"Table {self.db_name_pdwly_bill} created successfully")
            except BaseException as e:
                logging.debug(f"Table {self.db_name_pdwly_bill}  already exists: {e}")
        # 如果表已存在，则不会创建
        except BaseException as e:
            logging.debug(f"Table: {self.db_name_pdwly_bill}  already exists:{e}")
        finally:
            return self.connect

    def insert_data_pdwly_bill(self, data: dict):
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.db_name_pdwly_bill} VALUES('{data['date']}',{data['sumpay']},'{data['copymeterstatus']}','{data['prevmeterdata']}','{data['meterdata']}','{data['waternum']}','{data['dirtynum']}',{data['waterfee']},{data['waterprice']},{data['dirtyprice']},{data['dirtyfee']},'{data['paytime']}','{data['billstatus']}','{data['copymeterdate']}','{data['nextdate']}');"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"PDWLY Data update failed: {e}")

    def connect_user_db_shrq_bill(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            self.connect = sqlite3.connect(os.getenv("DB_NAME"))
            self.connect.cursor()
            logging.info(f"Database of {os.getenv('DB_NAME')} created successfully.")
            try:
                # 创建表名
                self.db_name_shrq_bill = f"shrq_bill{user_id}"
                sql = f"CREATE TABLE {self.db_name_shrq_bill} (date DATE PRIMARY KEY NOT NULL, money REAL NOT NULL, paymentStatus CHAR, billingDate CHAR, billingType CHAR, readDate CHAR, nextReadDate CHAR, lastReading REAL, currentReading REAL, consumption REAL, yearConsumption REAL, price REAL, lastPayday CHAR, billYM CHAR, firstGear REAL, secondGear REAL, yearCount REAL, firstCount REAL, secondCount REAL, thirdCount REAL, firstMoney REAL, secondMoney REAL, thirdMoney REAL );"
                self.connect.execute(sql)
                logging.info(f"Table {self.db_name_shrq_bill} created successfully")
            except BaseException as e:
                logging.debug(f"Table {self.db_name_shrq_bill}  already exists: {e}")
        # 如果表已存在，则不会创建
        except BaseException as e:
            logging.debug(f"Table: {self.db_name_shrq_bill}  already exists:{e}")
        finally:
            return self.connect

    def insert_data_shrq_bill(self, data: dict):
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.db_name_shrq_bill} VALUES('{data['date']}',{data['money']},'{data['paymentStatus']}','{data['billingDate']}','{data['billingType']}','{data['readDate']}','{data['nextReadDate']}',{data['lastReading']},{data['currentReading']},{data['consumption']},{data['yearConsumption']},{data['price']},'{data['lastPayday']}',{data['billYM']},{data['firstGear']},{data['secondGear']},{data['yearCount']},{data['firstCount']},{data['secondCount']},{data['thirdCount']},{data['firstMoney']},{data['secondMoney']},{data['thirdMoney']});"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"Data update failed: {e}")

    def fetch_pdwly(self):
        """the entry, only retry logic here """
        try:
            return self._fetch_pdwly()
        except Exception as e:
            traceback.print_exc()
            logging.error(
                f"Webdriver quit abnormly, reason: {e}. {self.RETRY_TIMES_LIMIT} retry times left.")

    def _fetch_pdwly(self):
        """main logic here"""

        try:
            # 获取上海燃气数据
            user_id_list, month_usage_total_list, month_usage_water_list, month_usage_dirty_list, month_charge_total_list, month_charge_water_list, month_charge_dirty_list, month_list = self._get_pdwly_data()
            return user_id_list, month_usage_total_list, month_usage_water_list, month_usage_dirty_list, month_charge_total_list, month_charge_water_list, month_charge_dirty_list, month_list
        finally:
            logging.info("Fetching pdwly data successfully.")

    def fetch_shrq(self):
        """the entry, only retry logic here """
        try:
            return self._fetch_shrq()
        except Exception as e:
            traceback.print_exc()
            logging.error(
                f"Webdriver quit abnormly, reason: {e}. {self.RETRY_TIMES_LIMIT} retry times left.")

    def _fetch_shrq(self):
        """main logic here"""

        try:
            # 获取上海燃气数据
            user_id_list, yearly_usage_list, month_usage_list, month_charge_list, month_list = self._get_shrq_data()
            return user_id_list, yearly_usage_list, month_usage_list, month_charge_list, month_list
        finally:
            logging.info("Fetching shrq data successfully.")

    def _get_pdwly_data(self):
        # 当前月份
        from datetime import datetime
        current_month = datetime.now().month
        year = datetime.now().year - 1
        if current_month < 10:
            current_month = "0" + str(current_month)
        start_time = str(year) + '-' + str(current_month)
        month_usage_total_list = []
        month_usage_water_list = []
        month_usage_dirty_list = []
        month_charge_total_list = []
        month_charge_water_list = []
        month_charge_dirty_list = []
        month_list = []
        for user in eval(self.pdwly_id):
            pdwly_url = 'https://mtu.pudongwater.com/wkf/WX/GetBill?cid=' + str(
                user) + '&kaishisj=' + start_time + '&jieshusj='
            header = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 5.0; SM-N9100 Build/LRX21V) > AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 > Chrome/37.0.0.0 Mobile Safari/537.36 > MicroMessenger/6.0.2.56_r958800.520 NetType/WIFI'
            }
            attempts = 0
            max_retries = 5
            while attempts < max_retries:
                try:
                    rsp = requests.get(pdwly_url, headers=header)
                    rsp_data = json.loads(rsp.text)
                    if rsp_data["code"] == "1":
                        self.connect_user_db_pdwly_bill(user)
                        month_usage_total_list_tmp = []
                        month_usage_water_list_tmp = []
                        month_usage_dirty_list_tmp = []
                        month_charge_total_list_tmp = []
                        month_charge_water_list_tmp = []
                        month_charge_dirty_list_tmp = []
                        month_list_tmp = []
                        for bill in json.loads(rsp_data['data']):
                            if int(bill.get('MONTH', '')) < 10:
                                month = '0' + str(bill.get('MONTH', ''))
                            else:
                                month = str(bill.get('MONTH', ''))
                            dic = {
                                'date': bill.get('YEAR', '') + '-' + month,
                                'sumpay': float(bill.get('SUMPAY', '')),
                                'copymeterstatus': str(bill.get('COPYMETERSTATUS', '')),
                                'prevmeterdata': str(bill.get('PREVMETERDATA', '')),
                                'meterdata': bill.get('METERDATA', ''),
                                'waternum': str(bill.get('WATERNUM', '')),
                                'dirtynum': str(bill.get('DIRTYNUM', '')),
                                'waterfee': bill.get('WATERFEE', ''),
                                'waterprice': bill.get('WATERPRICE', ''),
                                'dirtyprice': bill.get('DIRTYPRICE', ''),
                                'dirtyfee': bill.get('DIRTYFEE', ''),
                                'paytime': bill.get('PAYTIME', ''),
                                'billstatus': str(bill.get('BILLSTATUS', '')),
                                'copymeterdate': bill.get('COPYMETERDATE', ''),
                                'nextdate': bill.get('NEXTDATE', '')
                            }
                            month_usage_total_list_tmp.append(str(float(bill.get('WATERNUM', '0')) + float(bill.get('DIRTYNUM', '0'))))
                            month_usage_water_list_tmp.append(str(bill.get('WATERNUM', '')))
                            month_usage_dirty_list_tmp.append(str(bill.get('DIRTYNUM', '')))
                            month_charge_total_list_tmp.append(str(float(bill.get('WATERFEE', '0')) + float(bill.get('DIRTYFEE', '0'))))
                            month_charge_water_list_tmp.append(str(bill.get('WATERFEE', '')))
                            month_charge_dirty_list_tmp.append(str(bill.get('DIRTYFEE', '')))
                            month_list_tmp.append(bill.get('YEAR', '') + '-' + month)
                            # 插入到数据库
                            try:
                                self.insert_data_pdwly_bill(dic)
                                logging.info(
                                    f"水费数据插入成功！")
                            except Exception as e:
                                logging.debug(
                                    f"水费数据插入失败: {str(e)}")

                        self.connect.close()
                        month_usage_total_list.append(month_usage_total_list_tmp[-1])
                        month_usage_water_list.append(month_usage_water_list_tmp[-1])
                        month_usage_dirty_list.append(month_usage_dirty_list_tmp[-1])
                        month_charge_total_list.append(month_charge_total_list_tmp[-1])
                        month_charge_water_list.append(month_charge_water_list_tmp[-1])
                        month_charge_dirty_list.append(month_charge_dirty_list_tmp[-1])
                        month_list.append(month_list_tmp[-1])
                        logging.info(
                            f"获取用户 {user} 水费账单数据成功.")
                        attempts = 6
                    else:
                        logging.info(
                            f"获取用户 {user} 水费账单数据不成功，重试中。。。")

                except Exception as e:
                    logging.error(f"获取用户 {user} 水费账单数据失败，超过最大重试次数: {str(e)}.")
                attempts += 1
                time.sleep(5)  # 等待指定的时间后重试

        user_id_list = eval(self.pdwly_id)
        return user_id_list, month_usage_total_list, month_usage_water_list, month_usage_dirty_list, month_charge_total_list, month_charge_water_list, month_charge_dirty_list, month_list

    def _get_shrq_data(self):

        login_url = 'https://web-api.shgas.com.cn/v1/user/common/doLogin'
        img_url = 'https://web-api.shgas.com.cn/v1/thirdparty/common/img/getImgAuthCode?timestamp='
        timestamp = int(time.time())
        attempts = 0
        max_retries = 5
        while attempts < max_retries:
            try:
                get_img = requests.get(img_url + str(timestamp))
                if get_img.status_code == 200:
                    img_data = json.loads(get_img.text)
                    try:
                        if img_data['resultCode'] == '0000':
                            # print(img_data)
                            imgid = img_data['imgid']
                            base64_str = img_data['base64Image']
                            # 解码 Base64 字符串
                            image_data = base64.b64decode(base64_str)
                            # 将二进制数据转换为图像
                            image = Image.open(BytesIO(image_data))
                            import ddddocr
                            ocr = ddddocr.DdddOcr(show_ad=False)
                            result = ocr.classification(image)
                            """
                            取消显示图片
        
                            # 将图像转换为灰度图
                            gray_image = image.convert('L')
                            # 将图像缩小，便于打印
                            width, height = gray_image.size
                            aspect_ratio = height / float(width)
                            new_width = 100  # 设置新的宽度
                            new_height = int(aspect_ratio * new_width * 0.55)  # 调整高度以保持比例
                            resized_image = gray_image.resize((new_width, new_height))
                            # 将图像转换为 numpy 数组
                            image_array = np.array(resized_image)
                            # 定义 ASCII 字符集
                            ascii_chars = '@%#*+=-:. '
                            # 打印 ASCII 图像
                            for row in image_array:
                                line = ''.join(ascii_chars[pixel // 32] for pixel in row)  # 256/8 = 32
                                print(line)
        
                            """
                            img_code = result.splitlines()[-1]
                            logging.info(f"上海燃气登录验证码：{img_code}")

                            time.sleep(2)
                            timestamp = int(time.time())
                            post_data = {
                                "imgAuthCode": img_code,
                                "imgid": imgid,
                                "method": "PWD",
                                "mobile": self.shrq_username,
                                "origin": "PC",
                                "pwd": self.shrq_password,
                                "qrCode": "",
                                "smsAuthCode": "",
                                "timestamp": timestamp * 1000
                            }
                            try:
                                login = requests.post(login_url, data=json.dumps(post_data), headers=self.header)
                                login_data = json.loads(login.text)
                                token = login_data['token']
                                if login_data['resultCode'] != '0000':
                                    logging.info(f"上海燃气登录失败，将重试！")
                                else:
                                    logging.info(f"上海燃气登录成功！")
                                    #
                                    self.shrq_token = token

                                    url = 'https://web-api.shgas.com.cn/v1/user/queryAccountListByUserCode'
                                    query_data = {"timestamp": int(time.time()) * 1000}
                                    self.header['token'] = token
                                    rsp = requests.post(url, data=json.dumps(query_data), headers=self.header)
                                    # 获取用户id
                                    self.shrq_userid = json.loads(rsp.text)['accountList']
                                    yearly_usage_list = []
                                    month_usage_list = []
                                    month_charge_list = []
                                    month_list = []
                                    for user in self.shrq_userid:
                                        customerId = user['accountId']
                                        logging.info(f"上海燃气用户ID： {customerId}")
                                        time.sleep(15)
                                        shrq_bill_url = 'https://web-api.shgas.com.cn/v1/accountingService/queryBills'

                                        query_data = {"customerId": customerId, "companyCode": "PD", "origin": "PC",
                                                      "timestamp": int(time.time()) * 1000}
                                        try:
                                            rsp = requests.post(shrq_bill_url, data=json.dumps(query_data), headers=self.header)
                                            rsp_data = json.loads(rsp.text)
                                            if rsp_data["resultCode"] == "0000":
                                                self.connect_user_db_shrq_bill(customerId)
                                                yearly_usage_list_tmp = []
                                                month_usage_list_tmp = []
                                                month_charge_list_tmp = []
                                                month_list_tmp = []
                                                for bill in rsp_data['bills']:
                                                    dic = {
                                                        'date': bill.get('billYM', ''),
                                                        'money': float(bill.get('money', '')),
                                                        'paymentStatus': str(bill.get('paymentStatus', '')),
                                                        'billingDate': str(bill.get('billingDate', '')),
                                                        'billingType': bill.get('billingType', ''),
                                                        'readDate': str(bill.get('readDate', '')),
                                                        'nextReadDate': str(bill.get('nextReadDate', '')),
                                                        'lastReading': bill.get('lastReading', ''),
                                                        'currentReading': bill.get('currentReading', ''),
                                                        'consumption': bill.get('consumption', ''),
                                                        'yearConsumption': bill.get('yearConsumption', ''),
                                                        'price': bill.get('price', ''),
                                                        'lastPayday': str(bill.get('lastPayday', '')),
                                                        'billYM': bill.get('billYM', ''),
                                                        'firstGear': bill.get('firstGear', ''),
                                                        'secondGear': bill.get('secondGear', ''),
                                                        'yearCount': bill.get('yearCount', ''),
                                                        'firstCount': bill.get('firstCount', ''),
                                                        'secondCount': bill.get('secondCount', ''),
                                                        'thirdCount': bill.get('thirdCount', ''),
                                                        'firstMoney': bill.get('firstMoney', ''),
                                                        'secondMoney': bill.get('secondMoney', ''),
                                                        'thirdMoney': bill.get('thirdMoney', '')
                                                    }
                                                    yearly_usage_list_tmp.append(bill.get('yearConsumption', ''))
                                                    month_usage_list_tmp.append(bill.get('consumption', ''))
                                                    month_charge_list_tmp.append(bill.get('money', ''))
                                                    month_list_tmp.append(bill.get('billYM', ''))
                                                    # 插入到数据库
                                                    try:
                                                        self.insert_data_shrq_bill(dic)
                                                        logging.info(
                                                            f"燃气数据插入成功！")
                                                    except Exception as e:
                                                        logging.debug(
                                                            f"燃气数据插入失败: {str(e)}")

                                                self.connect.close()
                                                yearly_usage_list.append(yearly_usage_list_tmp[0])
                                                month_usage_list.append(month_usage_list_tmp[0])
                                                month_charge_list.append(month_charge_list_tmp[0])
                                                month_list.append(month_list_tmp[0])
                                                logging.info(
                                                    f"获取用户 {customerId} 账单数据成功.")
                                            else:
                                                logging.error(f"获取用户 {customerId} 账单数据失败,稍后将继续重试")
                                        except Exception as e:
                                            logging.error(f"获取用户 {customerId} 账单数据失败: {str(e)}.稍后将继续重试")
                                    user_id_list = self.shrq_userid
                                    return user_id_list, yearly_usage_list, month_usage_list, month_charge_list, month_list
                            except Exception as e:
                                logging.debug(f"登录失败！: {str(e)},将继续重试")
                        else:
                            logging.debug(f"获取登录验证码异常,将继续重试. ")
                        # logging.info(f"获取登录验证码：{img_code}")
                    except Exception as e:
                        logging.debug(f"获取登录验证码异常,将继续重试. {str(e)}")
                else:
                    logging.debug(f"获取登录验证码网络异常,将继续重试.")
            except Exception as e:
                logging.debug(f"获取登录验证码失败，将继续重试: {str(e)}")

            attempts += 1
            time.sleep(5)  # 等待指定的时间后重试

        logging.debug(f"超过最大重试次数，明日将继续重试。")

    def fetch(self):
        try:
            self._browser, self._context, self._page = self._setup_browser()
        except Exception as e:
            logging.error(f"浏览器启动/连接失败: {e}")
            raise
        ErrorWatcher.instance().set_page(self._page)
        self._random_delay(1, 3)
        updator = SensorUpdator()

        # Cookie 验证已禁用，每次均走完整登录流程
        cookie_valid = False
        # if self._validate_cookies(self._page):
        #     logging.info("Cookie 有效，跳过登录")
        #     cookie_valid = True
        # else:
        logging.info("Cookie 功能已禁用，开始登录流程")

        if not cookie_valid:
            try:
                if os.getenv("DEBUG_MODE", "false").lower() == "true":
                    if not self._login(self._page, phone_code=True):
                        raise Exception("login failed")
                else:
                    if not self._login(self._page):
                        raise Exception("login failed")
            except Exception as e:
                logging.error(f"Login error: {e}")
                self._browser.close()
                if getattr(self, '_pw', None):
                    self._pw.stop()
                raise
            logging.info("Logged in")
            # 登录成功后保存 Cookie
            # self._save_cookies(self._page)
        # 登录后等待页面完全加载（防止导航未完成就查询 DOM）
        try:
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        user_id_list = self._get_user_ids()
        for userid_index, user_id in enumerate(user_id_list):
            try:
                self._random_delay(1, 3)
                self._page.goto(BALANCE_URL)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                self._choose_current_userid(userid_index)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                if self._get_current_userid() in self.IGNORE_USER_ID:
                    continue
                balance, ldd, ldu, yc, yu, mc, mu, td, eb, bt = self._get_all_data(
                    user_id, userid_index)
                updator.update_one_userid(
                    user_id, balance, ldd, ldu, yc, yu, mc, mu,
                    tou_data=td, enhanced_balance=eb, bill_tou_data=bt)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            except Exception as e:
                continue
        self._browser.close()
        if getattr(self, '_pw', None):
            self._pw.stop()
    def _get_current_userid(self) -> str:
        """读取当前页面的用户户号（兼容多种页面布局）"""
        # 方式一：从"用电户号"标签中读取
        try:
            label = self._page.query_selector("xpath=" +  "//*[contains(normalize-space(.), '用电户号')]").inner_text() or ""
            matches = re.findall(r"\b\d{13}\b", label)
            if matches:
                return matches[-1]
        except Exception:
            pass
        # 方式二：从页面源码中正则匹配
        try:
            page_source = self._page.content() or ""
            match = re.search(r"用电户号[:：\s]*([0-9]{13})", page_source)
            if match:
                return match.group(1)
        except Exception:
            pass
        # 方式三：从下拉框中读取当前选中项
        try:
            dropdown = self._page.query_selector(".el-dropdown")
            text = dropdown.inner_text() or ""
            matches = re.findall(r"\b\d{13}\b", text)
            if matches:
                return matches[-1]
        except Exception:
            pass
        logging.warning("无法读取当前户号")
        return ""

    def _choose_current_userid(self, userid_index):
        """切换到指定索引的用户户号"""
        # 关闭确认弹窗（如果有）
        elements = self._page.query_selector_all(".button_confirm")
        if elements:
            try:
                self._click_button(self._page, "xpath=" + "//*[@id='app']/div/div[2]/div/div/div/div[2]/div[2]/div/button")
            except Exception:
                pass
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # 点击 el-select 的 suffix 箭头打开下拉（不触发 toggle-close）
        self._click_button(self._page, ".el-select .el-input__suffix")
        # 等待下拉选项出现
        try:
            self._page.wait_for_selector(
                "xpath=//div[contains(@class,'el-select-dropdown')]//li",
                state="visible",
                timeout=5000,
            )
        except Exception:
            pass

        # 获取下拉选项并点击目标
        options = self._get_visible_user_options()
        if userid_index >= len(options):
            logging.error(f"用户索引 {userid_index} 超出范围, 共 {len(options)} 个选项")
            return
        options[userid_index].click()
        logging.info(f"已切换到用户索引 {userid_index}")

    def _get_visible_user_options(self):
        """获取可见的用户下拉选项（兼容 el-dropdown 和 el-select）。

        注：Element UI 下拉菜单挂载在 <body> 下，选择器需同时覆盖两种组件。
        """
        # 等待下拉菜单出现
        try:
            self._page.wait_for_selector(
                "xpath="
                "//ul[contains(@class,'el-dropdown-menu')]//li"
                " | //div[contains(@class,'el-select-dropdown')]//li[contains(@class,'el-select-dropdown__item')]",
                state="attached",
                timeout=5000,
            )
        except Exception:
            pass

        all_options = self._page.query_selector_all(
            "xpath="
            "//ul[contains(@class,'el-dropdown-menu')]//li"
            " | //div[contains(@class,'el-select-dropdown')]//li[contains(@class,'el-select-dropdown__item')]"
        )
        result = []
        for option in all_options:
            try:
                if not option.is_visible():
                    continue
                cls = option.get_attribute("class") or ""
                if "is-disabled" in cls or "disabled" in cls:
                    continue
                result.append(option)
            except Exception:
                continue
        if not result:
            logging.warning(f"未找到可见的用户选项 (共扫描到 {len(all_options)} 个元素)")
        return result


    def _get_all_data(self, user_id, userid_index):
        logging.info(f"[{user_id}] 正在获取电费余额...")
        balance = self._get_electric_balance()
        if balance is None:
            logging.error(f"[{user_id}] 获取电费余额失败")
        else:
            logging.info(f"[{user_id}] 电费余额: {balance} 元")

        user_name = self._user_name_map.get(user_id, "")
        if user_name:
            logging.info(f"[{user_id}] 用户名: {user_name}")

        logging.info(f"[{user_id}] 正在切换到用电量页面...")
        self._page.goto(ELECTRIC_USAGE_URL)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        try:
            self._choose_current_userid(userid_index)
        except Exception as e:
            logging.warning(f"[{user_id}] 用电量页面用户切换失败 (非致命): {e}")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # ── Vue state 优先：一次性提取年度/月度/每日/分时数据 ──
        usage_info = None
        enhanced_balance = None
        try:
            components = vue_state.selected_vue_data(self._page)
            enhanced_balance = vue_state.normalize_balance(components)
            usage_info = vue_state.normalize_usage(components)
            if usage_info:
                logging.info(f"[{user_id}] Vue state 提取成功: 年度={usage_info.get('year')}, "
                             f"月度={len(usage_info.get('months', []))}条, "
                             f"日数据={len(usage_info.get('daily', []))}条")
        except Exception as e:
            logging.warning(f"[{user_id}] Vue state 提取失败: {e}")

        # ── 年度数据 ──
        if usage_info:
            yearly_usage = usage_info.get("yearly_usage")
            yearly_charge = usage_info.get("yearly_charge")
            logging.info(f"[{user_id}] 年度用电量: {yearly_usage} 度, 年度电费: {yearly_charge} 元 (Vue)")
        else:
            logging.info(f"[{user_id}] 正在获取年度用电数据 (DOM)...")
            yearly_usage, yearly_charge = self._get_yearly_data()
            if yearly_usage is not None:
                logging.info(f"[{user_id}] 年度用电量: {yearly_usage} 度 (DOM)")
            if yearly_charge is not None:
                logging.info(f"[{user_id}] 年度电费: {yearly_charge} 元 (DOM)")

        # ── 月度数据 ──
        if usage_info and usage_info.get("months"):
            months_raw = usage_info["months"]
            month = [m.get("month", "") for m in months_raw]
            month_usage = [str(m.get("total_usage", "")) for m in months_raw]
            month_charge = [str(m.get("total_charge", "")) for m in months_raw]
            for i in range(len(month)):
                logging.info(f"[{user_id}] {month[i]}: 用电 {month_usage[i]} 度, 电费 {month_charge[i]} 元 (Vue)")
        else:
            logging.info(f"[{user_id}] 正在获取月度用电数据 (DOM)...")
            month, month_usage, month_charge = self._get_month_usage()
            if month is not None:
                for i in range(len(month)):
                    logging.info(f"[{user_id}] {month[i]}: 用电 {month_usage[i]} 度, 电费 {month_charge[i]} 元 (DOM)")

        # ── 最近一日用电 ──
        if usage_info and usage_info.get("daily"):
            daily_sorted = sorted(usage_info["daily"], key=lambda d: d.get("date", ""))
            daily_last = daily_sorted[-1]
            last_daily_date = daily_last.get("date", "")
            last_daily_usage = daily_last.get("total_usage")
            logging.info(f"[{user_id}] 最近用电: {last_daily_date} {last_daily_usage} 度 (Vue)")
        else:
            logging.info(f"[{user_id}] 正在获取每日用电量 (DOM)...")
            last_daily_date, last_daily_usage = self._get_yesterday_usage()
            if last_daily_usage is not None:
                logging.info(f"[{user_id}] 最近用电: {last_daily_date} {last_daily_usage} 度 (DOM)")

        # ── 分时数据（仅 Vue state 提供） ──
        tou_data = usage_info
        if tou_data and tou_data.get("daily"):
            for d in tou_data["daily"][:7]:
                logging.info(f"  [日数据] {d.get('date')}: "
                             f"总={d.get('total_usage')}度, "
                             f"谷={d.get('valley_usage')}, 平={d.get('flat_usage')}, "
                             f"峰={d.get('peak_usage')}, 尖={d.get('tip_usage')}")
            if len(tou_data["daily"]) > 7:
                logging.info(f"  ... 还有 {len(tou_data['daily']) - 7} 条日数据")
        elif not usage_info:
            logging.info(f"[{user_id}] Vue state 不可用，跳过分时数据")

        # ── 电费账单明细（仅 Vue state，无 DOM 兜底） ──
        bill_tou_data = None
        if self.db is not None:
            try:
                bill_tou_data = self._get_bill_detail(user_id)
            except Exception as e:
                logging.warning(f"[{user_id}] 电费账单分时数据获取失败: {e}")

        # ── 数据库存储 ──
        if self.db is not None:
            logging.info(f"[{user_id}] 数据库类型: {self.db_type}, 开始保存数据到数据库")
            # 每日详细列表：Vue state 优先，DOM 兜底
            if usage_info and usage_info.get("daily"):
                date_list = [d.get("date", "") for d in usage_info["daily"]]
                usage_list = [str(d.get("total_usage", "")) for d in usage_info["daily"]]
            else:
                date_list, usage_list = self._get_daily_usage_data()
            self._save_user_data(
                user_id, balance, enhanced_balance,
                last_daily_date, last_daily_usage,
                date_list, usage_list,
                month, month_usage, month_charge,
                yearly_charge, yearly_usage,
                tou_data, bill_tou_data, user_name,
            )
        else:
            logging.info(f"[{user_id}] 未配置数据库, 跳过数据存储")

        # 按月排序取最新月（month 格式 "YYYY-MM" 可直接字符串比较）
        if month and month_charge and month_usage:
            sorted_triples = sorted(zip(month, month_usage, month_charge), key=lambda t: t[0])
            _, month_usage, month_charge = sorted_triples[-1]
        else:
            month_usage = month[-1] if month else None
            month_charge = month[-1] if month else None

        return balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance, bill_tou_data

    def _get_user_ids(self):
        """获取用户 ID 列表。优先从 el-dropdown 获取（余额页面），
        失败则从 el-select 获取（用电量页面），最后从页面源码正则匹配。"""
        try:
            # 方式一：经典方式 - 从 el-dropdown 下拉框获取
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            dropdowns = self._page.query_selector_all(".el-dropdown")
            if dropdowns:
                self._click_button(self._page, "xpath=" + "//div[@class='el-dropdown']/span")
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                try:
                    target = self._page.query_selector(".el-dropdown-menu.el-popper li")
                    # WebDriverWait(driver, 10)# .until(# EC.visibility_of(target))
                    # WebDriverWait(driver, 10)# .until(
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                    userid_elements = self._page.query_selector(".el-dropdown-menu.el-popper").query_selector_all( "li")
                    userid_list = []
                    for element in userid_elements:
                        matches = re.findall("[0-9]+", element.inner_text())
                        if matches:
                            uid = matches[-1]
                            userid_list.append(uid)
                    if userid_list:
                        logging.info(f"从 el-dropdown 获取到 {len(userid_list)} 个用户: {userid_list}")
                        return userid_list
                except Exception as e:
                    logging.debug(f"el-dropdown 获取失败, 尝试其他方式: {e}")

            # 方式二：从 el-select 下拉框获取（用电量页面）
            try:
                select_inputs = self._page.query_selector_all( ".houseNum .el-select .el-input__inner")
                if not select_inputs:
                    self._page.goto(ELECTRIC_USAGE_URL)
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
                    select_inputs = self._page.query_selector_all( ".houseNum .el-select .el-input__inner")

                if select_inputs:
                    select_inputs[0].click()
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

                    options = self._page.query_selector_all( ".el-select-dropdown__item")
                    userid_list = []
                    for opt in options:
                        text = opt.inner_text().strip()
                        if re.match(r'^\d{4}$', text):
                            continue
                        opt.click()
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                        try:
                            current_id = self._get_current_userid()
                            if current_id and current_id not in userid_list:
                                userid_list.append(current_id)
                                logging.info(f"从 el-select 获取到用户: {current_id} ({text})")
                        except Exception:
                            pass
                        select_inputs = self._page.query_selector_all( ".houseNum .el-select .el-input__inner")
                        if select_inputs:
                            select_inputs[0].click()
                            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

                    if userid_list:
                        logging.info(f"从 el-select 获取到 {len(userid_list)} 个用户: {userid_list}")
                        return userid_list
            except Exception as e:
                logging.debug(f"el-select 获取失败: {e}")

            # 方式三：从页面源码正则匹配所有13位户号
            page_source = self._page.content() or ""
            all_ids = list(set(re.findall(r'\b(\d{13})\b', page_source)))
            if all_ids:
                logging.info(f"从页面源码正则匹配到 {len(all_ids)} 个用户: {all_ids}")
                return all_ids

            logging.error("所有方式均未能获取用户 ID 列表")
            return []
        except Exception as e:
            logging.error(f"获取用户 ID 列表异常: {e}")
            return []

    def _get_electric_balance(self):
        try:
            try:
                # 定位是否有"应交金额"标题（确认是后缴费账户）
                title_text = self._page.query_selector("xpath=" +  "//p[contains(@class, 'balance_title') and contains(text(), '应交金额')]").inner_text()
                if "应交金额" in title_text:
                    # 后缴费账户：需要查找"账户余额"，而不是"应交金额"
                    # 查找包含"账户余额"的balance_title元素，然后获取其内部的金额
                    balance_content = self._page.query_selector("xpath=" +  "//p[contains(@class, 'balance_title') and contains(text(), '账户余额')]")
                    # 提取数字部分
                    balance_text = re.sub(r'[^\d.]', '', balance_content.inner_text())
                    if balance_text:
                        return float(balance_text)
            except Exception as e:
                # 后缴费账户解析失败，继续尝试预缴费账户逻辑
                pass

            # 2. 预缴费账户的"账户余额"（原逻辑）
            balance_text = self._page.query_selector(".cff8").inner_text()
            balance = balance_text.replace("元", "")
            if "欠费" in balance_text:
                return -float(balance)
            else:
                return float(balance)
        except Exception as e:
            logging.error(f"获取余额失败: {e}")
            return None

    def _get_yearly_data(self):

        try:
            if datetime.now().month == 1:
                self._click_button(self._page, "xpath=" + '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                span_element = self._page.query_selector("xpath=" +  f"//span[text() = '{datetime.now().year - 1}']")
                span_element.click()
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            self._click_button(self._page, "xpath=" + "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # 等待数据显示
            target = self._page.query_selector(".total")
            # WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME)# .until(# EC.visibility_of(target))
        except Exception as e:
            logging.error(f"年度数据获取失败: {e}")
            return None, None

        # 获取数据
        try:
            yearly_usage = self._page.query_selector("xpath=" +  "//ul[@class='total']/li[1]/span").inner_text()
        except Exception as e:
            logging.error(f"年度用电量数据获取失败: {e}")
            yearly_usage = None

        try:
            yearly_charge = self._page.query_selector("xpath=" +  "//ul[@class='total']/li[2]/span").inner_text()
        except Exception as e:
            logging.error(f"年度电费数据获取失败: {e}")
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self):
        """获取最近一次用电量"""
        try:
            # 点击日用电量 tab
            self._click_button(self._page, "xpath=" + "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
            # 等待数据表格出现（兼容多种滚动类名）
            usage_element = self._page.query_selector("xpath=" + """//*[@id="pane-second"]/div[2]/div[2]/div[1]/div[3]/table/tbody/tr[1]/td[2]/div""")
            # WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME)# .until(# EC.visibility_of(usage_element)) # 等待用电量出现

            # 增加是哪一天
            date_element = self._page.query_selector("xpath=" + """//*[@id="pane-second"]/div[2]/div[2]/div[1]/div[3]/table/tbody/tr[1]/td[1]/div""")
            last_daily_date = date_element.inner_text() # 获取最近一次用电量的日期
            return last_daily_date, float(usage_element.inner_text())
        except Exception as e:
            logging.error(f"每日用电量数据获取失败: {e}")
            return None, None

    def _get_month_usage(self):
        """获取每月用电量（从月度电费 tab 的表格中提取）"""
        try:
            self._click_button(self._page, "xpath=" + "//div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            if datetime.now().month == 1:
                self._click_button(self._page, ".el-select .el-input__suffix")
                time.sleep(1)
                span = self._page.query_selector(f"xpath=//span[text()='{datetime.now().year - 1}']")
                if span:
                    span.click()
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

            # 从实际 DOM 提取月度表格：.el-table.posiquery 内的 tbody 行
            rows = self._page.query_selector_all(
                "#pane-first .el-table__body-wrapper tbody tr")
            if not rows:
                logging.error("未找到月度用电数据表格")
                return None, None, None

            month, usage, charge = [], [], []
            for row in rows:
                try:
                    cells = row.query_selector_all("td")
                    if len(cells) < 3:
                        continue
                    # td[0]: 日期范围, td[1]: 电量(kWh), td[2]: 电费(元)
                    date_text = cells[0].inner_text().strip()
                    usage_text = cells[1].inner_text().strip()
                    charge_text = cells[2].inner_text().strip()
                    # 用电量可能包含 "MAX" 标记（cells[1] 内可能有嵌套 span）
                    usage_val = re.sub(r'[^\d.]', '', usage_text)
                    charge_val = re.sub(r'[^\d.]', '', charge_text)
                    if usage_val and charge_val:
                        month.append(date_text)
                        usage.append(usage_val)
                        charge.append(charge_val)
                except Exception:
                    continue

            if not month:
                logging.error("未能解析任何月度数据")
                return None, None, None

            logging.info(f"获取到 {len(month)} 个月度数据")
            return month, usage, charge
        except Exception as e:
            logging.error(f"月度数据获取失败: {e}")
            return None, None, None

    # 增加获取每日用电量的函数
    def _get_daily_usage_data(self):
        """获取每日用电量数据 (7天或30天)，通过 radio 按钮切换，失败时返回空列表"""
        try:
            fetch_days = int(os.getenv("DAILY_FETCH_DAYS", 7))
            if fetch_days not in (7, 30):
                fetch_days = 7
            logging.info(f"正在获取每日用电量数据 (最近 {fetch_days} 天)")
            # 点击"日用电量" tab
            self._click_button(self._page, "xpath=" + "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 3)

            # 通过 radio 按钮点击 7天 或 30天
            if fetch_days == 30:
                try:
                    radio = self._page.query_selector("xpath=" + 
                        "//span[contains(@class,'el-radio__label') and contains(text(),'近30天')]"
                        "/preceding-sibling::span//input[@class='el-radio__original']")
                    radio.click()
                    logging.info("已点击 '近30天' radio 按钮")
                except Exception:
                    try:
                        self._click_button(self._page, "xpath="+
                            "//*[@id='pane-second']//label[2]//span[@class='el-radio__input']")
                        logging.info("已点击 '近30天' 备用方案")
                    except Exception:
                        logging.warning("未找到 '近30天' radio, 使用默认数据")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 3)

            # 等待日用电量表格出现（用 attached 替代 visible，避免匹配到隐藏 tab 的元素导致超时）
            self._page.wait_for_selector(
                "xpath=" +
                "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]"
                + "/table/tbody/tr",
                state="attached",
                timeout=self.DRIVER_IMPLICITY_WAIT_TIME * 1000
            )

            # 获取用电量数据
            days_element = self._page.query_selector_all("xpath=" + 
                "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]"
                "/table/tbody/tr")
            date = []
            usages = []
            for i in days_element:
                try:
                    day = i.query_selector("xpath=" +  "td[1]/div").inner_text()
                    usage = i.query_selector("xpath=" +  "td[2]/div").inner_text()
                    if usage != "":
                        usages.append(usage)
                        date.append(day)
                except Exception:
                    pass
            logging.info(f"DOM 方式成功获取 {len(date)} 天的每日用电量数据")
            return date, usages
        except Exception as e:
            logging.warning(f"DOM 方式获取每日用电量数据失败: {e}")
            return [], []

    def _get_daily_tou_data(self):
        """通过展开日用电量表格行获取每日分时电量（谷/平/峰/尖）"""
        tou_rows = []
        try:
            # 找到所有展开图标并逐个点击
            expand_icons = self._page.query_selector_all(
                ".el-table__expand-icon")
            for icon in expand_icons:
                try:
                    icon.click()
                    time.sleep(0.5)
                except Exception:
                    continue

            time.sleep(1)

            # 读取展开行中的分时电量
            expanded_cells = self._page.query_selector_all(
                ".el-table__expanded-cell .drop-box-left")
            for cell in expanded_cells:
                tou = {"valley_usage": 0.0, "flat_usage": 0.0, "peak_usage": 0.0, "tip_usage": 0.0}
                paragraphs = cell.query_selector_all( "p")
                for p in paragraphs:
                    text = p.inner_text()
                    try:
                        num_el = p.query_selector( ".num")
                        val = float(num_el.inner_text())
                    except Exception:
                        continue
                    if "谷" in text:
                        tou["valley_usage"] = val
                    elif "平" in text:
                        tou["flat_usage"] = val
                    elif "峰" in text:
                        tou["peak_usage"] = val
                    elif "尖" in text:
                        tou["tip_usage"] = val
                tou_rows.append(tou)
            logging.info(f"通过展开行获取到 {len(tou_rows)} 条分时电量数据")
        except Exception as e:
            logging.warning(f"获取展开行分时电量失败: {e}")
        return tou_rows

    def _get_bill_detail(self, user_id):
        """从用电量页面通过 Vue state 获取月度分时电量"""
        logging.info(f"[{user_id}] 尝试获取电费账单分时数据...")
        try:
            components = vue_state.selected_vue_data(self._page)
            bill = vue_state.normalize_bill_detail(components)
            if not bill.get("month"):
                logging.info(f"[{user_id}] 电费账单分时数据为空（billList 无数据），跳过")
                return None
            logging.info(f"[{user_id}] 账单分时数据: {bill['month']}, "
                         f"谷={bill.get('valley_usage')}, 平={bill.get('flat_usage')}, "
                         f"峰={bill.get('peak_usage')}, 尖={bill.get('tip_usage')}")
            return bill
        except Exception as e:
            logging.warning(f"[{user_id}] 获取账单分时数据异常: {e}")
            return None

    def _save_user_data(self, user_id, balance, enhanced_balance,
                        last_daily_date, last_daily_usage,
                        date_list, usage_list,
                        month, month_usage, month_charge,
                        yearly_charge, yearly_usage,
                        tou_data=None, bill_tou_data=None, user_name=""):
        if not self.db.connect_user_db(user_id):
            logging.error(f"[{user_id}] 数据库连接失败, 数据未写入")
            return

        try:
            self.db.upsert_user(user_id, self._username, user_name)
            logging.info(f"[{user_id}] 用户信息已更新 (user_name={user_name})")

            # 写入余额日志
            if balance is not None:
                bal_data = {"balance": balance, "user_name": user_name}
                if enhanced_balance:
                    bal_data.update({
                        "as_of": enhanced_balance.get("as_of"),
                        "amount_due": enhanced_balance.get("amount_due"),
                    })
                self.db.insert_balance_log(bal_data)
                logging.info(f"[{user_id}] 余额日志已写入: {balance} 元")

            # 写入每日用电量（DOM 方式）
            if date_list:
                for i in range(len(date_list)):
                    try:
                        self.db.insert_daily_data({
                            "date": date_list[i],
                            "total_usage": float(usage_list[i]),
                            "user_name": user_name,
                        })
                    except Exception as e:
                        logging.debug(f"[{user_id}] 日用电 {date_list[i]} 写入失败 (可能已存在): {e}")
                logging.info(f"[{user_id}] 每日用电量已写入 {len(date_list)} 条")

            # 写入 Vue state 分时日用电量
            if tou_data and tou_data.get("daily"):
                tou_count = 0
                for row in tou_data["daily"]:
                    try:
                        row["user_name"] = user_name
                        self.db.insert_daily_data(row)
                        tou_count += 1
                    except Exception as e:
                        logging.debug(f"[{user_id}] 分时日用电 {row.get('date')} 写入失败: {e}")
                logging.info(f"[{user_id}] Vue state 分时日用电已写入 {tou_count} 条")

            # 写入月度用电量（DOM 方式）
            if month:
                cur_year = str(datetime.now().year)
                for i in range(len(month)):
                    try:
                        # 将 "1月1日-1月31日" 格式转为 "2026-01"
                        m_text = month[i]
                        m_num = re.search(r'(\d+)月', m_text)
                        m_formatted = f"{cur_year}-{int(m_num.group(1)):02d}" if m_num else m_text
                        self.db.insert_monthly_data({
                            "month": m_formatted,
                            "total_usage": float(month_usage[i]) if month_usage[i] else None,
                            "total_charge": float(month_charge[i]) if month_charge[i] else None,
                            "user_name": user_name,
                        })
                    except Exception as e:
                        logging.debug(f"[{user_id}] 月度 {month[i]} 写入失败: {e}")
                logging.info(f"[{user_id}] 月度用电量已写入 {len(month)} 条")

            # 写入 Vue state 分时月用电量
            if tou_data and tou_data.get("months"):
                for m_row in tou_data["months"]:
                    try:
                        m_row["user_name"] = user_name
                        self.db.insert_monthly_data(m_row)
                    except Exception as e:
                        logging.debug(f"[{user_id}] 分时月度 {m_row.get('month')} 写入失败: {e}")
                logging.info(f"[{user_id}] Vue state 分时月用电已写入 {len(tou_data['months'])} 条")

            # 写入账单分时月用电量
            if bill_tou_data and bill_tou_data.get("month"):
                try:
                    self.db.insert_monthly_data({
                        "month": bill_tou_data["month"],
                        "total_usage": bill_tou_data.get("usage"),
                        "total_charge": bill_tou_data.get("charge"),
                        "valley_usage": bill_tou_data.get("valley_usage", 0),
                        "flat_usage": bill_tou_data.get("flat_usage", 0),
                        "peak_usage": bill_tou_data.get("peak_usage", 0),
                        "tip_usage": bill_tou_data.get("tip_usage", 0),
                        "user_name": user_name,
                    })
                    logging.info(f"[{user_id}] 账单分时月度数据已写入: {bill_tou_data['month']}")
                except Exception as e:
                    logging.warning(f"[{user_id}] 账单分时月度写入失败: {e}")

            # 写入年度用电量
            year = str(datetime.now().year)
            if yearly_usage is not None or yearly_charge is not None:
                try:
                    year_data = {"year": year, "user_name": user_name}
                    if yearly_usage is not None:
                        year_data["total_usage"] = float(yearly_usage)
                    if yearly_charge is not None:
                        year_data["total_charge"] = float(yearly_charge)
                    self.db.insert_yearly_data(year_data)
                    logging.info(f"[{user_id}] 年度用电量已写入: {year}")
                except Exception as e:
                    logging.warning(f"[{user_id}] 年度用电量写入失败: {e}")

            # 从 Vue state 获取分时年度汇总
            if tou_data and tou_data.get("year"):
                try:
                    self.db.insert_yearly_data({
                        "year": tou_data["year"],
                        "total_usage": tou_data.get("yearly_usage"),
                        "total_charge": tou_data.get("yearly_charge"),
                        "user_name": user_name,
                    })
                    logging.info(f"[{user_id}] Vue state 年度数据已写入: {tou_data['year']}")
                except Exception as e:
                    logging.warning(f"[{user_id}] Vue state 年度写入失败: {e}")

            # 数据清理
            self.db.cleanup_old_data()
            logging.info(f"[{user_id}] 数据清理完成")

        except Exception as e:
            logging.error(f"[{user_id}] 数据保存过程出错: {e}")
        finally:
            self.db.close_connect()

if __name__ == "__main__":
    with open("bg.jpg", "rb") as f:
        test1 = f.read()
        print(type(test1))
        print(test1)

