import logging
import os
import re
import time
import json

import random
import base64
from datetime import datetime
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from sensor_updator import SensorUpdator
from error_watcher import ErrorWatcher
from typing import Optional

from const import *

import numpy as np
from captcha_selenium import solve_captcha_in_browser
import vue_state

class DataFetcher:

    def __init__(self, username: str, password: str):
        if 'PYTHON_IN_DOCKER' not in os.environ:
            import dotenv
            dotenv.load_dotenv(verbose=True)
        self._username = username
        self._password = password

        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 60))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
        self.IGNORE_USER_ID = os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",")
        self.QR_CODE_LOGIN_WAIT_COUNT = int(os.getenv("QR_CODE_LOGIN_WAIT_COUNT", 7))
        self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT = int(os.getenv("QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT", 10))
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
        if self.db_type == 'mysql':
            from db import MysqlDB
            self.db = MysqlDB()
            logging.info("使用 MySQL 数据库存储数据。")
        elif self.db_type == 'sqlite':
            from db import SqliteDB
            self.db = SqliteDB()
            logging.info("使用 SQLite 数据库存储数据。")
        else:
            self.db = None
            logging.info("不使用数据库存储数据。")

    # @staticmethod
    def _click_button(self, driver, button_search_type, button_search_key):
        '''封装点击函数，仅在元素可点击时点击'''
        click_element = driver.find_element(button_search_type, button_search_key)
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)
        # 点击后添加微小随机暂停，模拟人工操作
        time.sleep(random.uniform(0.1, 0.5))


    def insert_expand_data(self, data:dict):
        self.db.insert_expand_data(data)

    def _get_webdriver(self):
        chrome_options = webdriver.ChromeOptions()

        # 基础参数
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--start-maximized")

        # 反检测核心参数（参考 ha-95598）
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        # 可选：环境变量自定义反检测参数
        browser_lang = os.getenv("BROWSER_LANGUAGE", "zh-HK,zh,en-US,en")
        browser_ua = os.getenv("BROWSER_USER_AGENT", "")
        device_scale = os.getenv("BROWSER_DEVICE_SCALE_FACTOR", "2")
        window_size = os.getenv("BROWSER_WINDOW_SIZE", "1158,848")

        chrome_options.add_argument(f"--lang={browser_lang}")
        chrome_options.add_argument(f"--window-size={window_size}")
        chrome_options.add_argument(f"--force-device-scale-factor={device_scale}")
        chrome_options.add_argument("--high-dpi-support=1")
        if browser_ua:
            chrome_options.add_argument(f"user-agent={browser_ua}")

        chrome_options.add_experimental_option("prefs", {
            "intl.accept_languages": browser_lang,
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        })

        # 无头模式（Docker 环境）
        if 'PYTHON_IN_DOCKER' in os.environ:
            chrome_options.add_argument("--headless=new")
            chrome_options.binary_location = "/usr/bin/chromium"
            service = ChromeService(executable_path="/usr/bin/chromedriver")
        else:
            service = self._find_chromedriver()

        driver = webdriver.Chrome(options=chrome_options, service=service)
        driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)
        return driver

    @staticmethod
    def _find_chromedriver() -> ChromeService:
        """在非 Docker 环境中查找可用的 ChromeDriver。"""
        import shutil

        # 1) 尝试系统 PATH
        path = shutil.which("chromedriver") or shutil.which("chromedriver.exe")
        if path:
            return ChromeService(executable_path=path)

        # 2) 尝试 CloakBrowser 缓存的 chromedriver（如果有）
        for base in [
            os.path.expanduser("~/.cloakbrowser"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), ".cloakbrowser"),
        ]:
            try:
                for root, dirs, files in os.walk(base):
                    if "chromedriver.exe" in files or "chromedriver" in files:
                        fname = "chromedriver.exe" if "chromedriver.exe" in files else "chromedriver"
                        path = os.path.join(root, fname)
                        if os.path.isfile(path):
                            return ChromeService(executable_path=path)
                    # 最多扫描两级目录
                    if len(root) - len(base) > 200:
                        dirs.clear()
            except Exception:
                pass

        # 3) 尝试 Selenium Manager 自动下载
        try:
            return ChromeService()
        except Exception:
            pass

        raise RuntimeError(
            "ChromeDriver 未找到。请安装 ChromeDriver 或运行: pip install chromedriver-binary-auto"
        )

    @ErrorWatcher.watch
    def _login(self, driver, phone_code = False):
        try:
            driver.get(LOGIN_URL)
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME * 3).until(EC.visibility_of_element_located((By.CLASS_NAME, "user")))
        except Exception:
            logging.error(f"登录页面加载失败: {LOGIN_URL}")
            return False
        logging.info(f"打开登录页面: {LOGIN_URL}。\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
        # swtich to username-password login page
        # 临时关闭隐式等待，避免与 WebDriverWait 叠加导致超时
        driver.implicitly_wait(0)
        try:
            WebDriverWait(driver, 10).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, 'el-loading-mask')))
        finally:
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

        element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'user')))
        driver.execute_script("arguments[0].click();", element)
        logging.info("已找到 'user' 元素。\r")
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # 点击同意按钮
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("已点击同意选项。\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        if phone_code:
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"已输入用户名: {self._username}\r")
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
            code = input("请输入手机验证码: ")
            input_elements[3].send_keys(code)
            logging.info(f"已输入验证码: {code}。\r")
            # 点击登录按钮
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("已点击登录按钮。\r")

            return True
        # 增加判空校验便于测试备用方案
        elif self._password is not None and len(self._password) > 0:
            # 输入用户名和密码
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[0].send_keys(self._username)
            logging.info(f"已输入用户名: {self._username}\r")
            input_elements[1].send_keys(self._password)
            logging.info(f"已输入密码: {self._password}\r")

            # 点击登录按钮
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
            logging.info("已点击登录按钮。\r")

            # 快速检查：如果已经跳转离开登录页，说明无需验证码，直接成功
            if driver.current_url != LOGIN_URL:
                logging.info("无需验证码登录成功 (已被重定向)。\r")
                return True

            # 处理腾讯点击验证码
            captcha_passed = solve_captcha_in_browser(driver, max_retries=self.RETRY_TIMES_LIMIT)
            if captcha_passed:
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                if driver.current_url != LOGIN_URL:
                    logging.info("通过点击验证码登录成功。\r")
                    return True
                else:
                    error = self._get_error_message(driver, "//div[@class='errmsg-tip']//span")
                    if error:
                        logging.info(f"验证码通过但登录失败: [{error}]\r")
                    else:
                        logging.error("验证码已通过但仍停留在登录页面。")
            else:
                logging.error("点击验证码识别在所有重试后均失败。")
        return self._fallback_login(driver)

    def _get_error_message(self, driver, path) -> Optional[str]:
        """获取错误信息，如果不存在则返回 None"""
        # 关闭隐式等待
        driver.implicitly_wait(0)
        try:
            element = driver.find_element(By.XPATH, path)
            return element.text
        except Exception:
            return None
        finally:
            driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)  # 恢复隐式等待

    def _fallback_login(self, driver) -> bool:
        """使用备用方案登录"""
        fallback = os.getenv("LOGIN_FALLBACK")
        if fallback == 'qrcode':
            return self._qr_login(driver)
        return False

    def _qr_login(self, driver) -> bool:
        logging.info("二维码登录开始")
        # 切换验证码
        element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.presence_of_element_located((By.CLASS_NAME, 'qr_code')))
        driver.execute_script("arguments[0].click();", element)
        logging.info("已切换到二维码模式")

        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # 获取登录二维码
        qrElement = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, "//div[@class='sweepCodePic']//img")))
        logging.info("已找到二维码图片元素")

        img_src = qrElement.get_attribute('src')

        if img_src.startswith('data:image'):
            base64_data = img_src.split(',')[1]
            img_screenshot = base64.b64decode(base64_data)
        else:
          logging.info('二维码图片源不是 base64 格式')
          img_screenshot = qrElement.screenshot_as_png

        with open("/data/login_qr_code.png", "wb") as f:
            f.write(img_screenshot)
            logging.info("已将二维码保存到 /data/login_qr_code.png")

        from notify import UrlLoginQrCodeNotify
        notifyFunc = UrlLoginQrCodeNotify()
        notifyFunc(img_screenshot)
        for i in range(1, self.QR_CODE_LOGIN_WAIT_COUNT + 1):
            logging.info(f'二维码登录等待检查[{self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT}] 次数[{i}]')
            time.sleep(self.QR_CODE_LOGIN_WAIT_TIME_INTERVAL_UNIT)
            if (driver.current_url != LOGIN_URL):
                logging.info("二维码登录成功")
                return True
            else:
                error = self._get_error_message(driver, "//div[@class='sweepCodePic']//div[@class='erwBg']//p")
                if error is not None:
                    logging.error(f'二维码登录错误[{error}]')
                    return False

        logging.warning("二维码登录超时")

        return False

    def _random_delay(self, min_seconds=0.5, max_seconds=3.0):
        """添加随机延迟，使自动化操作更难被检测。"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)


    def fetch(self):

        """主逻辑"""

        driver = self._get_webdriver()
        ErrorWatcher.instance().set_driver(driver)

        driver.maximize_window()
        self._random_delay(1, 3)
        logging.info("浏览器驱动已初始化。")
        updator = SensorUpdator()

        try:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                if self._login(driver,phone_code=True):
                    logging.info("登录成功!")
                else:
                    logging.info("登录失败!")
                    raise Exception("login unsuccessed")
            else:
                if self._login(driver):
                    logging.info("登录成功!")
                else:
                    logging.info("登录失败!")
                    raise Exception("login unsuccessed")
        except Exception as e:
            logging.error(
                f"浏览器驱动异常退出，原因: {e}。还剩 {self.RETRY_TIMES_LIMIT} 次重试机会。")
            driver.quit()
            return

        logging.info(f"在 {LOGIN_URL} 登录成功")
        self._random_delay(1, 3)
        # self._random_mouse_move(driver)
        logging.info(f"尝试获取用户 ID 列表")
        user_id_list = self._get_user_ids(driver)
        logging.info(f"共找到 {len(user_id_list)} 个用户 ID，其中 {user_id_list} 将被忽略: {self.IGNORE_USER_ID}")
        self._random_delay(0.5, 2)


        for userid_index, user_id in enumerate(user_id_list):
            try:
                self._random_delay(1, 3)
                # 切换到电费余额页面
                driver.get(BALANCE_URL)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                self._choose_current_userid(driver,userid_index)
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                current_userid = self._get_current_userid(driver)
                if current_userid in self.IGNORE_USER_ID:
                    logging.info(f"用户 ID {current_userid} 将被忽略")
                    continue
                else:
                    ### 获取数据
                    balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance = self._get_all_data(driver, user_id, userid_index)
                    logging.info(f"用户 [{user_id}] 数据获取完成: 余额={balance}元, 最近日用电={last_daily_usage}度({last_daily_date}), "
                                 f"年度用电={yearly_usage}度, 年度电费={yearly_charge}元, 月用电={month_usage}度, 月电费={month_charge}元")
                    updator.update_one_userid(user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data=tou_data, enhanced_balance=enhanced_balance)

                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            except Exception as e:
                if (userid_index != len(user_id_list)):
                    logging.info(f"当前用户 {user_id} 数据抓取失败: {e}，将继续抓取下一个用户数据。")
                else:
                    logging.info(f"用户 {user_id} 数据抓取失败: {e}")
                    logging.info("数据抓取完成后浏览器驱动退出。")
                continue

        driver.quit()


    def _get_current_userid(self, driver) -> str:
        """读取当前页面的用户户号（兼容多种页面布局）"""
        # 方式一：从"用电户号"标签中读取
        try:
            label = driver.find_element(By.XPATH, "//*[contains(normalize-space(.), '用电户号')]").text or ""
            matches = re.findall(r"\b\d{13}\b", label)
            if matches:
                return matches[-1]
        except Exception:
            pass
        # 方式二：从页面源码中正则匹配
        try:
            page_source = driver.page_source or ""
            match = re.search(r"用电户号[:：\s]*([0-9]{13})", page_source)
            if match:
                return match.group(1)
        except Exception:
            pass
        # 方式三：从下拉框中读取当前选中项
        try:
            dropdown = driver.find_element(By.CLASS_NAME, "el-dropdown")
            text = dropdown.text or ""
            matches = re.findall(r"\b\d{13}\b", text)
            if matches:
                return matches[-1]
        except Exception:
            pass
        logging.warning("无法读取当前户号")
        return ""

    def _choose_current_userid(self, driver, userid_index):
        """切换到指定索引的用户户号"""
        # 关闭确认弹窗（如果有）
        elements = driver.find_elements(By.CLASS_NAME, "button_confirm")
        if elements:
            try:
                self._click_button(driver, By.XPATH, "//*[@id='app']/div/div[2]/div/div/div/div[2]/div[2]/div/button")
            except Exception:
                pass
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # 打开用户选择器（兼容多种触发方式）
        try:
            trigger = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//span[contains(normalize-space(.), '切换用户')]"
                    " | //div[contains(@class,'houseNum')]//div[contains(@class,'el-select')]//span[contains(@class,'el-input__suffix')]"
                    " | //div[contains(@class,'houseNum')]//span[contains(normalize-space(.), '切换用户')]"
                ))
            )
            driver.execute_script("arguments[0].click();", trigger)
        except Exception:
            # 备用方案: 点击 el-input__suffix（下拉箭头）
            self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # 获取下拉选项并点击目标
        options = self._get_visible_user_options(driver)
        if userid_index >= len(options):
            logging.error(f"用户索引 {userid_index} 超出范围, 共 {len(options)} 个选项")
            return
        driver.execute_script("arguments[0].click();", options[userid_index])
        logging.info(f"已切换到用户索引 {userid_index}")

    def _get_visible_user_options(self, driver):
        """获取可见的用户下拉选项（兼容 el-dropdown 和 el-select）"""
        return [
            option
            for option in driver.find_elements(
                By.XPATH,
                "//ul[contains(@class,'el-dropdown-menu')]//li"
                " | //div[contains(@class,'el-select-dropdown')]//li",
            )
            if option.is_displayed()
            and "is-disabled" not in (option.get_attribute("class") or "")
            and "disabled" not in (option.get_attribute("class") or "")
        ]


    def _get_all_data(self, driver, user_id, userid_index):
        logging.info(f"[{user_id}] 正在获取电费余额...")
        balance = self._get_electric_balance(driver)
        if balance is None:
            logging.error(f"[{user_id}] 获取电费余额失败")
        else:
            logging.info(f"[{user_id}] 电费余额: {balance} 元")

        # 尝试通过 Vue state 获取增强余额
        enhanced_balance = None
        user_name = self._user_name_map.get(user_id, "")
        if user_name:
            logging.info(f"[{user_id}] 用户名: {user_name}")
        if self.db is not None:
            try:
                components = vue_state.selected_vue_data(driver)
                enhanced_balance = vue_state.normalize_balance(components)
            except Exception as e:
                logging.warning(f"[{user_id}] 增强余额获取失败: {e}")

        logging.info(f"[{user_id}] 正在切换到用电量页面...")
        driver.get(ELECTRIC_USAGE_URL)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        try:
            self._choose_current_userid(driver, userid_index)
        except Exception as e:
            logging.warning(f"[{user_id}] 用电量页面用户切换失败 (非致命): {e}")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        logging.info(f"[{user_id}] 正在获取年度用电数据...")
        yearly_usage, yearly_charge = self._get_yearly_data(driver)
        if yearly_usage is None:
            logging.error(f"[{user_id}] 获取年度用电量失败")
        else:
            logging.info(f"[{user_id}] 年度用电量: {yearly_usage} 度")
        if yearly_charge is None:
            logging.error(f"[{user_id}] 获取年度电费失败")
        else:
            logging.info(f"[{user_id}] 年度电费: {yearly_charge} 元")

        logging.info(f"[{user_id}] 正在获取月度用电数据...")
        month, month_usage, month_charge = self._get_month_usage(driver)
        if month is None:
            logging.error(f"[{user_id}] 获取月度用电数据失败")
        else:
            for m in range(len(month)):
                logging.info(f"[{user_id}] {month[m]}: 用电 {month_usage[m]} 度, 电费 {month_charge[m]} 元")

        logging.info(f"[{user_id}] 正在获取每日用电量...")
        last_daily_date, last_daily_usage = self._get_yesterday_usage(driver)
        if last_daily_usage is None:
            logging.error(f"[{user_id}] 获取每日用电量失败")
        else:
            logging.info(f"[{user_id}] 最近用电: {last_daily_date} 用电 {last_daily_usage} 度")

        # 尝试通过 Vue state 获取分时电量
        tou_data = None
        if self.db is not None:
            try:
                components = vue_state.selected_vue_data(driver)
                usage_info = vue_state.normalize_usage(components)
                tou_data = usage_info
                logging.info(f"[{user_id}] Vue state 分时数据: 年度={usage_info.get('year')}, "
                             f"月数据={len(usage_info.get('months', []))}条, "
                             f"日数据={len(usage_info.get('daily', []))}条")
                # 打印 Vue state 日数据详情
                if usage_info.get("daily"):
                    for d in usage_info["daily"][:7]:
                        logging.info(f"  [日数据] {d.get('date')}: "
                                     f"总={d.get('total_usage')}度, "
                                     f"谷={d.get('valley_usage')}, 平={d.get('flat_usage')}, "
                                     f"峰={d.get('peak_usage')}, 尖={d.get('tip_usage')}")
                    if len(usage_info["daily"]) > 7:
                        logging.info(f"  ... 还有 {len(usage_info['daily']) - 7} 条日数据")
            except Exception as e:
                logging.warning(f"[{user_id}] Vue state 分时数据获取失败: {e}")

        # 尝试获取电费账单明细（月度分时）
        bill_tou_data = None
        if self.db is not None:
            try:
                bill_tou_data = self._get_bill_detail(driver, user_id)
            except Exception as e:
                logging.warning(f"[{user_id}] 电费账单分时数据获取失败: {e}")

        # 数据库存储
        if self.db is not None:
            logging.info(f"[{user_id}] 数据库类型: {self.db_type}, 开始保存数据到数据库")
            date_list, usage_list = self._get_daily_usage_data(driver)
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

        if month_charge:
            month_charge = month_charge[-1]
        else:
            month_charge = None
        if month_usage:
            month_usage = month_usage[-1]
        else:
            month_usage = None

        return balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage, tou_data, enhanced_balance

    def _get_user_ids(self, driver):
        """获取用户 ID 列表。优先从 el-dropdown 获取（余额页面），
        失败则从 el-select 获取（用电量页面），最后从页面源码正则匹配。"""
        try:
            # 方式一：经典方式 - 从 el-dropdown 下拉框获取
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            dropdowns = driver.find_elements(By.CLASS_NAME, 'el-dropdown')
            if dropdowns:
                self._click_button(driver, By.XPATH, "//div[@class='el-dropdown']/span")
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                try:
                    target = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_element(By.TAG_NAME, "li")
                    WebDriverWait(driver, 10).until(EC.visibility_of(target))
                    WebDriverWait(driver, 10).until(
                        EC.text_to_be_present_in_element((By.XPATH, "//ul[@class='el-dropdown-menu el-popper']/li"), ":"))
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                    userid_elements = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_elements(By.TAG_NAME, "li")
                    userid_list = []
                    for element in userid_elements:
                        matches = re.findall("[0-9]+", element.text)
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
                select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
                if not select_inputs:
                    driver.get(ELECTRIC_USAGE_URL)
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
                    select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")

                if select_inputs:
                    driver.execute_script("arguments[0].click();", select_inputs[0])
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

                    options = driver.find_elements(By.CSS_SELECTOR, ".el-select-dropdown__item")
                    userid_list = []
                    for opt in options:
                        text = opt.text.strip()
                        if re.match(r'^\d{4}$', text):
                            continue
                        driver.execute_script("arguments[0].click();", opt)
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                        try:
                            current_id = self._get_current_userid(driver)
                            if current_id and current_id not in userid_list:
                                userid_list.append(current_id)
                                logging.info(f"从 el-select 获取到用户: {current_id} ({text})")
                        except Exception:
                            pass
                        select_inputs = driver.find_elements(By.CSS_SELECTOR, ".houseNum .el-select .el-input__inner")
                        if select_inputs:
                            driver.execute_script("arguments[0].click();", select_inputs[0])
                            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

                    if userid_list:
                        logging.info(f"从 el-select 获取到 {len(userid_list)} 个用户: {userid_list}")
                        return userid_list
            except Exception as e:
                logging.debug(f"el-select 获取失败: {e}")

            # 方式三：从页面源码正则匹配所有13位户号
            page_source = driver.page_source or ""
            all_ids = list(set(re.findall(r'\b(\d{13})\b', page_source)))
            if all_ids:
                logging.info(f"从页面源码正则匹配到 {len(all_ids)} 个用户: {all_ids}")
                return all_ids

            logging.error("所有方式均未能获取用户 ID 列表")
            return []
        except Exception as e:
            logging.error(f"获取用户 ID 列表异常: {e}")
            return []

    def _get_electric_balance(self, driver):
        try:
            try:
                # 定位是否有"应交金额"标题（确认是后缴费账户）
                title_text = driver.find_element(By.XPATH, "//p[contains(@class, 'balance_title') and contains(text(), '应交金额')]").text
                if "应交金额" in title_text:
                    # 后缴费账户：需要查找"账户余额"，而不是"应交金额"
                    # 查找包含"账户余额"的balance_title元素，然后获取其内部的金额
                    balance_content = driver.find_element(By.XPATH, "//p[contains(@class, 'balance_title') and contains(text(), '账户余额')]")
                    # 提取数字部分
                    balance_text = re.sub(r'[^\d.]', '', balance_content.text)
                    if balance_text:
                        return float(balance_text)
            except Exception as e:
                # 后缴费账户解析失败，继续尝试预缴费账户逻辑
                pass

            # 2. 预缴费账户的"账户余额"（原逻辑）
            balance_text = driver.find_element(By.CLASS_NAME, "cff8").text
            balance = balance_text.replace("元", "")
            if "欠费" in balance_text:
                return -float(balance)
            else:
                return float(balance)
        except Exception as e:
            logging.error(f"获取余额失败: {e}")
            return None

    def _get_yearly_data(self, driver):

        try:
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                span_element = driver.find_element(By.XPATH, f"//span[text() = '{datetime.now().year - 1}']")
                span_element.click()
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # 等待数据显示
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
        except Exception as e:
            logging.error(f"年度数据获取失败: {e}")
            return None, None

        # 获取数据
        try:
            yearly_usage = driver.find_element(By.XPATH, "//ul[@class='total']/li[1]/span").text
        except Exception as e:
            logging.error(f"年度用电量数据获取失败: {e}")
            yearly_usage = None

        try:
            yearly_charge = driver.find_element(By.XPATH, "//ul[@class='total']/li[2]/span").text
        except Exception as e:
            logging.error(f"年度电费数据获取失败: {e}")
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self, driver):
        """获取最近一次用电量"""
        try:
            # 点击日用电量 tab
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 2)
            # 等待数据表格出现（兼容多种滚动类名）
            usage_element = driver.find_element(By.XPATH,"""//*[@id="pane-second"]/div[2]/div[2]/div[1]/div[3]/table/tbody/tr[1]/td[2]/div""")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(usage_element)) # 等待用电量出现

            # 增加是哪一天
            date_element = driver.find_element(By.XPATH,"""//*[@id="pane-second"]/div[2]/div[2]/div[1]/div[3]/table/tbody/tr[1]/td[1]/div""")
            last_daily_date = date_element.text # 获取最近一次用电量的日期
            return last_daily_date, float(usage_element.text)
        except Exception as e:
            logging.error(f"每日用电量数据获取失败: {e}")
            return None, None

    def _get_month_usage(self, driver):
        """获取每月用电量"""

        try:
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            if datetime.now().month == 1:
                self._click_button(driver, By.XPATH, '//*[@id="pane-first"]/div[1]/div/div[1]/div/div/input')
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                span_element = driver.find_element(By.XPATH, f"//span[text() = '{datetime.now().year - 1}']")
                span_element.click()
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # 等待月度数据出现
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
            month_element = driver.find_element(By.XPATH, "//*[@id='pane-first']/div[1]/div[2]/div[2]/div/div[3]/table/tbody").text
            month_element = month_element.split("\n")
            month_element = [x for x in month_element if x != "MAX"]
            if len(month_element) % 3 != 0:
                month_element = month_element[:-(len(month_element) % 3)]
            month_element = np.array(month_element).reshape(-1, 3)
            # 将每月的用电量保存为List
            month = []
            usage = []
            charge = []
            for i in range(len(month_element)):
                month.append(month_element[i][0])
                usage.append(month_element[i][1])
                charge.append(month_element[i][2])
            return month, usage, charge
        except Exception as e:
            logging.error(f"月度数据获取失败: {e}")
            return None,None,None

    # 增加获取每日用电量的函数
    def _get_daily_usage_data(self, driver):
        """获取每日用电量数据 (7天或30天)，通过 radio 按钮切换，失败时返回空列表"""
        try:
            fetch_days = int(os.getenv("DAILY_FETCH_DAYS", 7))
            if fetch_days not in (7, 30):
                fetch_days = 7
            logging.info(f"正在获取每日用电量数据 (最近 {fetch_days} 天)")
            # 点击"日用电量" tab
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 3)

            # 通过 radio 按钮点击 7天 或 30天
            if fetch_days == 30:
                try:
                    radio = driver.find_element(By.XPATH,
                        "//span[contains(@class,'el-radio__label') and contains(text(),'近30天')]"
                        "/preceding-sibling::span//input[@class='el-radio__original']")
                    driver.execute_script("arguments[0].click();", radio)
                    logging.info("已点击 '近30天' radio 按钮")
                except Exception:
                    try:
                        self._click_button(driver, By.XPATH,
                            "//*[@id='pane-second']//label[2]//span[@class='el-radio__input']")
                        logging.info("已点击 '近30天' 备用方案")
                    except Exception:
                        logging.warning("未找到 '近30天' radio, 使用默认数据")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT * 3)

            # 等待用电量数据出现（兼容多种滚动类名）
            usage_element = WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
                EC.visibility_of_element_located((
                    By.XPATH,
                    "//div[contains(@class,'el-tab-pane')]//div[contains(@class,'el-table__body-wrapper')]"
                    "//table/tbody/tr[1]/td[2]/div"
                ))
            )

            # 获取用电量数据
            days_element = driver.find_elements(By.XPATH,
                "//*[@id='pane-second']//div[contains(@class,'el-table__body-wrapper')]"
                "/table/tbody/tr")
            date = []
            usages = []
            for i in days_element:
                try:
                    day = i.find_element(By.XPATH, "td[1]/div").text
                    usage = i.find_element(By.XPATH, "td[2]/div").text
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

    def _get_daily_tou_data(self, driver):
        """通过展开日用电量表格行获取每日分时电量（谷/平/峰/尖）"""
        tou_rows = []
        try:
            # 找到所有展开图标并逐个点击
            expand_icons = driver.find_elements(By.CSS_SELECTOR,
                ".el-table__expand-icon")
            for icon in expand_icons:
                try:
                    driver.execute_script("arguments[0].click();", icon)
                    time.sleep(0.5)
                except Exception:
                    continue

            time.sleep(1)

            # 读取展开行中的分时电量
            expanded_cells = driver.find_elements(By.CSS_SELECTOR,
                ".el-table__expanded-cell .drop-box-left")
            for cell in expanded_cells:
                tou = {"valley_usage": 0.0, "flat_usage": 0.0, "peak_usage": 0.0, "tip_usage": 0.0}
                paragraphs = cell.find_elements(By.TAG_NAME, "p")
                for p in paragraphs:
                    text = p.text
                    try:
                        num_el = p.find_element(By.CSS_SELECTOR, ".num")
                        val = float(num_el.text)
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

    def _get_bill_detail(self, driver, user_id):
        """从用电量页面通过 Vue state 获取月度分时电量"""
        logging.info(f"[{user_id}] 尝试从当前页面获取电费账单分时数据...")
        try:
            # 不再跳转到 403 的 BILL_SUMMARY_URL, 直接从当前页面提取
            components = vue_state.selected_vue_data(driver)
            bill = vue_state.normalize_bill_detail(components)
            if bill.get("month"):
                logging.info(f"[{user_id}] 账单分时数据: {bill['month']}, "
                             f"谷={bill.get('valley_usage')}, 平={bill.get('flat_usage')}, "
                             f"峰={bill.get('peak_usage')}, 尖={bill.get('tip_usage')}")
                return bill
            logging.info(f"[{user_id}] Vue state 中未找到账单数据, 跳过")
            return None
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
