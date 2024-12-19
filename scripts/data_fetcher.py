import logging
import os
import re
import subprocess
import time
import traceback
import copy

import random
import base64
import json
import requests
import dotenv
import sqlite3
import undetected_chromedriver as uc

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from const import *

import numpy as np
# import cv2
from io import BytesIO
from PIL import Image
from onnx import ONNX
import platform


def __ease_out_expo(sep):
    if sep == 1:
        return 1
    else:
        return 1 - pow(2, -10 * sep)

def _get_tracks(distance):
    """
    拿到移动轨迹，模仿人的滑动行为，先匀加速后匀减速
    匀变速运动基本公式：
    ①v = v0+at
    ②s = v0t+1/2at^2
    """
    if distance == 0:
        return [0]
    #初速度
    v = 0
    #单位时间为0.3s来统计轨迹，轨迹即0.3内的位移
    t = 0.31
    #位置/轨迹列表，列表内的一个元素代表0.3s的位移
    tracks = []
    #当前位移
    current = 0
    #到达mid值开始减速
    mid = distance*4/5

    while current < distance:
        if current < mid:       #加速度越小，单位时间内的位移越小，模拟的轨迹就越多越详细
            a = 20
        else:
            a = -30
        #初速度
        v0 = v
        #0.3秒内的位移
        s = v0*t+0.5*a*(t**2)
        #当前的位置
        current += s
        #添加到轨迹列表
        tracks.append(round(s))
        #速度已经到达v,该速度作为下次的初速度
        v = v0+a*t
    print("sum(tracks) is {}, sum(tracks) - distance is {}",sum(tracks),sum(tracks)-round(distance*1.02))
    tracks.append(sum(tracks)-distance)
    logging.info(f"image tracks distance is {sum(tracks)}")
    return tracks 

def base64_to_PLI(base64_str: str):
    base64_data = re.sub('^data:image/.+;base64,', '', base64_str)
    byte_data = base64.b64decode(base64_data)
    image_data = BytesIO(byte_data)
    img = Image.open(image_data)
    return img

def get_transparency_location(image):
    '''获取基于透明元素裁切图片的左上角、右下角坐标

    :param image: cv2加载好的图像
    :return: (left, upper, right, lower)元组
    '''
    # 1. 扫描获得最左边透明点和最右边透明点坐标
    height, width, channel = image.shape  # 高、宽、通道数
    assert channel == 4  # 无透明通道报错
    first_location = None  # 最先遇到的透明点
    last_location = None  # 最后遇到的透明点
    first_transparency = []  # 从左往右最先遇到的透明点，元素个数小于等于图像高度
    last_transparency = []  # 从左往右最后遇到的透明点，元素个数小于等于图像高度
    for y, rows in enumerate(image):
        for x, BGRA in enumerate(rows):
            alpha = BGRA[3]
            if alpha != 0:
                if not first_location or first_location[1] != y:  # 透明点未赋值或为同一列
                    first_location = (x, y)  # 更新最先遇到的透明点
                    first_transparency.append(first_location)
                last_location = (x, y)  # 更新最后遇到的透明点
        if last_location:
            last_transparency.append(last_location)

    # 2. 矩形四个边的中点
    top = first_transparency[0]
    bottom = first_transparency[-1]
    left = None
    right = None
    for first, last in zip(first_transparency, last_transparency):
        if not left:
            left = first
        if not right:
            right = last
        if first[0] < left[0]:
            left = first
        if last[0] > right[0]:
            right = last

    # 3. 左上角、右下角
    upper_left = (left[0], top[1])  # 左上角
    bottom_right = (right[0], bottom[1])  # 右下角

    return upper_left[0], upper_left[1], bottom_right[0], bottom_right[1]

class DataFetcher:

    def __init__(self, username: str, password: str):
        dotenv.load_dotenv()
        self._username = username
        self._password = password
        self.onnx = ONNX("./captcha.onnx")
        if platform.system() == 'Windows':
            pass
        else:
            self._chromium_version = self._get_chromium_version()

        # 获取 ENABLE_DATABASE_STORAGE 的值，默认为 False
        self.enable_database_storage = os.getenv("ENABLE_DATABASE_STORAGE", "false").lower() == "true"

        if self.enable_database_storage:
            # 将数据存储到数据库
            logging.info("enable_database_storage is true, we will store the data to the database.")
        else:
            logging.info("enable_database_storage is false, we will not store the data to the database.")

        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME", 60))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME", 10))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT", 10))
        self.IGNORE_USER_ID = os.getenv("IGNORE_USER_ID", "xxxxx,xxxxx").split(",")

    # @staticmethod
    def _click_button(self, driver, button_search_type, button_search_key):
        '''wrapped click function, click only when the element is clickable'''
        click_element = driver.find_element(button_search_type, button_search_key)
        # logging.info(f"click_element:{button_search_key}.is_displayed() = {click_element.is_displayed()}\r")
        # logging.info(f"click_element:{button_search_key}.is_enabled() = {click_element.is_enabled()}\r")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)

    # @staticmethod
    def _is_captcha_legal(self, captcha):
        ''' check the ddddocr result, justify whether it's legal'''
        if (len(captcha) != 4):
            return False
        for s in captcha:
            if (not s.isalpha() and not s.isdigit()):
                return False
        return True

    # @staticmethod
    def _get_chromium_version(self):
        result = str(subprocess.check_output(["chromium", "--product-version"]))
        version = re.findall(r"(\d*)\.", result)[0]
        logging.info(f"chromium-driver version is {version}")
        return int(version)

    # @staticmethod 
    def _sliding_track(self, driver, distance):# 机器模拟人工滑动轨迹
        # 获取按钮
        slider = driver.find_element(By.CLASS_NAME, "slide-verify-slider-mask-item")
        ActionChains(driver).click_and_hold(slider).perform()
        # 获取轨迹
        # tracks = _get_tracks(distance)
        # for t in tracks:
        yoffset_random = random.uniform(-2, 4)
        ActionChains(driver).move_by_offset(xoffset=distance, yoffset=yoffset_random).perform()
            # time.sleep(0.2)
        ActionChains(driver).release().perform()

    def base64_api(self, b64, typeid=33):
        data = {"username": self._tujian_uname, "password": self._tujian_passwd, "typeid": typeid, "image": b64}
        result = json.loads(requests.post("http://api.ttshitu.com/predict", json=data).text)
        if result['success']:
            return result["data"]["result"]
        else:
            #！！！！！！！注意：返回 人工不足等 错误情况 请加逻辑处理防止脚本卡死 继续重新 识别
            return result["message"]
        return ""

    def connect_user_db(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            DB_NAME = os.getenv("DB_NAME", "homeassistant.db")
            self.connect = sqlite3.connect(DB_NAME)
            self.connect.cursor()
            logging.info(f"Database of {DB_NAME} created successfully.")
            try:
                # 创建表名
                self.db_name = f"daily{user_id}"
                sql = f"CREATE TABLE {self.db_name} (date DATE PRIMARY KEY NOT NULL, usage REAL NOT NULL);"
                self.connect.execute(sql)
                logging.info(f"Table {self.db_name} created successfully")
            except BaseException as e:
                logging.debug(f"Table {self.db_name} already exists: {e}")
        # 如果表已存在，则不会创建
        except BaseException as e:
            logging.debug(f"Table: {self.db_name} already exists:{e}")
        finally:
            return self.connect

    def insert_data(self, data:dict):
            # 创建索引
            try:
                sql = f"INSERT OR REPLACE INTO {self.db_name} VALUES(strftime('%Y-%m-%d','{data['date']}'),{data['usage']});"
                self.connect.execute(sql)
                self.connect.commit()
            except BaseException as e:
                logging.debug(f"Data update failed: {e}")

    def _get_webdriver(self):
        chrome_options = Options()
        chrome_options.add_argument('--incognito')
        chrome_options.add_argument('--window-size=4000,1600')
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-dev-shm-usage')
        driver = uc.Chrome(driver_executable_path="/usr/bin/chromedriver", options=chrome_options, version_main=self._chromium_version)
        driver.implicitly_wait(self.DRIVER_IMPLICITY_WAIT_TIME)
        return driver

    def _login(self, driver, phone_code = False):

        driver.get(LOGIN_URL)
        logging.info(f"Open LOGIN_URL:{LOGIN_URL}.\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # swtich to username-password login page
        driver.find_element(By.CLASS_NAME, "user").click()
        logging.info("find_element 'user'.\r")
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # click agree button
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("Click the Agree option.\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        if phone_code:
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[3]/span')
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[2].send_keys(self._username)
            logging.info(f"input_elements username : {self._username}.\r")
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[1]/div[2]/div[2]/div/a')
            code = input("Input your phone verification code: ")
            input_elements[3].send_keys(code)
            logging.info(f"input_elements verification code: {code}.\r")
            # click login button
            self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[2]/form/div[2]/div/button/span')
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("Click login button.\r")

            return True
        else :
            # input username and password
            input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
            input_elements[0].send_keys(self._username)
            logging.info(f"input_elements username : {self._username}.\r")
            input_elements[1].send_keys(self._password)
            logging.info(f"input_elements password : {self._password}.\r")

            # click login button
            self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            logging.info("Click login button.\r")
            # sometimes ddddOCR may fail, so add retry logic)
            for retry_times in range(1, self.RETRY_TIMES_LIMIT + 1):
                
                self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[1]/div[1]/div[2]/span')
                #get canvas image
                background_JS = 'return document.getElementById("slideVerify").childNodes[0].toDataURL("image/png");'
                targe_JS = 'return document.getElementsByClassName("slide-verify-block")[0].toDataURL("image/png");'
                # get base64 image data
                im_info = driver.execute_script(background_JS) 
                background = im_info.split(',')[1]  
                background_image = base64_to_PLI(background)
                logging.info(f"Get electricity canvas image successfully.\r")
                distance = self.onnx.get_distance(background_image)
                logging.info(f"Image CaptCHA distance is {distance}.\r")

                # slider = driver.find_element(By.CLASS_NAME, "slide-verify-slider-mask-item")
                # ActionChains(driver).click_and_hold(slider).perform()
                # ActionChains(driver).move_by_offset(xoffset=round(distance*1.06), yoffset=0).perform()
                # ActionChains(driver).release().perform()

                self._sliding_track(driver, round(distance*1.06)) #1.06是补偿
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                if (driver.current_url == LOGIN_URL): # if login not success
                    try:
                        logging.info(f"Sliding CAPTCHA recognition failed and reloaded.\r")
                        self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
                        continue
                    except:
                        logging.debug(
                            f"Login failed, maybe caused by invalid captcha, {self.RETRY_TIMES_LIMIT - retry_times} retry times left.")
                else:
                    return True
            logging.error(f"Login failed, maybe caused by Sliding CAPTCHA recognition failed")
        return False

        raise Exception(
            "Login failed, maybe caused by 1.incorrect phone_number and password, please double check. or 2. network, please mnodify LOGIN_EXPECTED_TIME in .env and run docker compose up --build.")
        
    def fetch(self):
        """the entry, only retry logic here """
        try:
            return self._fetch()
        except Exception as e:
            traceback.print_exc()
            logging.error(
                f"Webdriver quit abnormly, reason: {e}. {self.RETRY_TIMES_LIMIT} retry times left.")

    def _fetch(self):

        balance_list = []
        last_daily_date_list = []
        last_daily_usage_list = []
        yearly_charge_list = []
        yearly_usage_list = []        
        month_list = []
        month_charge_list = []
        month_usage_list = []


        """main logic here"""
        if platform.system() == 'Windows':
            driverfile_path = r'C:\Users\mxwang\Project\msedgedriver.exe'
            driver = webdriver.Edge(executable_path=driverfile_path)
        else:
            driver = self._get_webdriver()
        
        driver.maximize_window() 
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        logging.info("Webdriver initialized.")

        try:
            if os.getenv("DEBUG_MODE", "false").lower() == "true":
                if self._login(driver,phone_code=True):
                    logging.info("login successed !")
                else:
                    logging.info("login unsuccessed !")
            else:
                if self._login(driver):
                    logging.info("login successed !")
                else:
                    logging.info("login unsuccessed !")
            logging.info(f"Login successfully on {LOGIN_URL}")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
            user_id_list = self._get_user_ids(driver)
            logging.info(f"Here are a total of {len(user_id_list)} userids, which are {user_id_list} among which {self.IGNORE_USER_ID} will be ignored.")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

            current_userid_list = copy.deepcopy(user_id_list)

            for i, user_id in enumerate(user_id_list):           
                try: 
                    # switch to electricity charge balance page
                    driver.get(BALANCE_URL) 
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                    self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                    self._click_button(driver, By.XPATH, f"/html/body/div[2]/div[1]/div[1]/ul/li[{i+1}]/span")
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                    current_userid = self._get_current_userid(driver)
                    if current_userid in self.IGNORE_USER_ID:
                        current_userid_list.remove(current_userid)
                        logging.info(f"The user ID {current_userid} will be ignored in user_id_list")
                        continue
                    else:
                        ### get data 
                        balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_data, month_usage, month_charge  = self._get_all_data(driver, user_id)
                        
                        balance_list.append(balance)
                        last_daily_date_list.append(last_daily_date)
                        last_daily_usage_list.append(last_daily_usage)
                        yearly_charge_list.append(yearly_charge)
                        yearly_usage_list.append(yearly_usage)
                        month_list.append(month_data)
                        month_usage_list.append(month_usage)
                        month_charge_list.append(month_charge)
                        
                        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                except Exception as e:
                    if (i != len(user_id_list)):
                        logging.info(f"The current user {user_id} data fetching failed {e}, the next user data will be fetched.")
                    else:
                        logging.info(f"The user {user_id} data fetching failed, {e}")
                    continue    

            driver.quit()

            logging.info("Webdriver quit after fetching data successfully.")
            return current_userid_list, balance_list, last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list 

        finally:
            driver.quit()

    def _get_current_userid(self, driver):
        current_userid = driver.find_element(By.XPATH, '//*[@id="app"]/div/div/article/div/div/div[2]/div/div/div[1]/div[2]/div/div/div/div[2]/div/div[1]/div/ul/div/li[1]/span[2]').text
        return current_userid


    def _get_all_data(self, driver, user_id):
        balance = self._get_electric_balance(driver)
        if (balance is None):
            logging.info(f"Get electricity charge balance for {user_id} failed, Pass.")
        else:
            logging.info(
                f"Get electricity charge balance for {user_id} successfully, balance is {balance} CNY.")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # swithc to electricity usage page
        driver.get(ELECTRIC_USAGE_URL)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # get data for each user id
        yearly_usage, yearly_charge = self._get_yearly_data(driver)

        if yearly_usage is None:
            logging.error(f"Get year power usage for {user_id} failed, pass")
        else:
            logging.info(
                f"Get year power usage for {user_id} successfully, usage is {yearly_usage} kwh")
        if yearly_charge is None:
            logging.error(f"Get year power charge for {user_id} failed, pass")
        else:
            logging.info(
                f"Get year power charge for {user_id} successfully, yealrly charge is {yearly_charge} CNY")

        # get month usage
        month, month_usage, month_charge = self._get_month_usage(driver)
        if month is None:
            logging.error(f"Get month power usage for {user_id} failed, pass")
        else:
            for m in range(len(month)):
                logging.info(
                    f"Get month power charge for {user_id} successfully, {month[m]} usage is {month_usage[m]} KWh, charge is {month_charge[m]} CNY.")
        # get yesterday usage
        last_daily_datetime, last_daily_usage = self._get_yesterday_usage(driver)

        # 新增储存用电量
        if self.enable_database_storage:
            self.save_daily_usage_data(driver, user_id)

        if last_daily_usage is None:
            logging.error(f"Get daily power consumption for {user_id} failed, pass")
        else:
            logging.info(
                f"Get daily power consumption for {user_id} successfully, , {last_daily_datetime} usage is {last_daily_usage} kwh.")
        if month:
            month_data = month[-1]
        else:
            month_data = None
        if month_charge:
            month_charge = month_charge[-1]
        else:
            month_charge = None
        if month_usage:
            month_usage = month_usage[-1]
        else:
            month_usage = None

        return balance, last_daily_datetime, last_daily_usage, yearly_charge, yearly_usage, month_data, month_usage, month_charge

    def _get_user_ids(self, driver):

        # click roll down button for user id
        self._click_button(driver, By.XPATH, "//div[@class='el-dropdown']/span")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # wait for roll down menu displayed
        target = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_element(By.TAG_NAME, "li")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(
            EC.text_to_be_present_in_element((By.XPATH, "//ul[@class='el-dropdown-menu el-popper']/li"), ":"))

        # get user id one by one
        userid_elements = driver.find_element(By.CLASS_NAME, "el-dropdown-menu.el-popper").find_elements(By.TAG_NAME, "li")
        userid_list = []
        for element in userid_elements:
            userid_list.append(re.findall("[0-9]+", element.text)[-1])
        return userid_list

    def _get_electric_balance(self, driver):
        try:
            balance = driver.find_element(By.CLASS_NAME, "num").text
            return float(balance)
        except:
            return None

    def _get_yearly_data(self, driver):

        try:
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for data displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
        except:
            return None, None

        # get data
        try:
            yearly_usage = driver.find_element(By.XPATH, "//ul[@class='total']/li[1]/span").text

        except:
            yearly_usage = None

        try:
            yearly_charge = driver.find_element(By.XPATH, "//ul[@class='total']/li[2]/span").text
        except:
            yearly_charge = None

        return yearly_usage, yearly_charge

    def _get_yesterday_usage(self, driver):
        """获取最近一次用电量"""
        try:
            # 点击日用电量
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-second']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for data displayed
            usage_element = driver.find_element(By.XPATH,
                                                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(usage_element)) # 等待用电量出现

            # 增加是哪一天
            date_element = driver.find_element(By.XPATH,
                                                "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[1]/div")
            last_daily_date = date_element.text # 获取最近一次用电量的日期
            return last_daily_date, float(usage_element.text)
        except:
            return None

    def _get_month_usage(self, driver):
        """获取每月用电量"""

        try:
            self._click_button(driver, By.XPATH, "//div[@class='el-tabs__nav is-top']/div[@id='tab-first']")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            # wait for month displayed
            target = driver.find_element(By.CLASS_NAME, "total")
            WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(target))
            month_element = driver.find_element(By.XPATH, "//*[@id='pane-first']/div[1]/div[2]/div[2]/div/div[3]/table/tbody").text
            month_element = month_element.split("\n")
            month_element.remove("MAX")
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
        except:
            return None,None,None

    # 增加储存用电量的到mongodb的函数
    def save_daily_usage_data(self, driver, user_id):
        """储存指定天数的用电量"""
        retention_days = int(os.getenv("DATA_RETENTION_DAYS", 7))  # 默认值为7天

        # 7 天在第一个 label, 30 天 开通了智能缴费之后才会出现在第二个, (sb sgcc)
        if retention_days == 7:
            self._click_button(driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[1]/span[1]")
        elif retention_days == 30:
            self._click_button(driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[2]/span[1]")
        else:
            logging.error(f"Unsupported retention days value: {retention_days}")
            return

        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)

        # 等待用电量的数据出现
        usage_element = driver.find_element(By.XPATH,
                                            "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(usage_element))

        # 获取用电量的数据
        days_element = driver.find_elements(By.XPATH,
                                            "//*[@id='pane-second']/div[2]/div[2]/div[1]/div[3]/table/tbody/tr")  # 用电量值列表

        # 连接数据库集合
        self.connect_user_db(user_id)

        # 将用电量保存为字典
        for i in days_element:
            day = i.find_element(By.XPATH, "td[1]/div").text
            usage = i.find_element(By.XPATH, "td[2]/div").text
            if usage != "":
                dic = {'date': day, 'usage': float(usage)}
                # 插入到数据库
                try:
                    self.insert_data(dic)
                    logging.info(f"The electricity consumption of {usage}KWh on {day} has been successfully deposited into the database")
                except Exception as e:
                    logging.debug(f"The electricity consumption of {day} failed to save to the database, which may already exist: {str(e)}")
            else:
                logging.info(f"The electricity consumption of {usage} get nothing")
        self.connect.close()

if __name__ == "__main__":
    with open("bg.jpg", "rb") as f:
        test1 = f.read()
        print(type(test1))
        print(test1)
