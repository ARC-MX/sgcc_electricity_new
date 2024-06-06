import logging
import os
import re
import subprocess
import time
import traceback

import random
import base64
import json
import requests
import dotenv
import pymongo
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

DEBUG = False

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

# # cv2转base64
# def cv2_to_base64(img):
#     img = cv2.imencode('.jpg', img)[1]
#     image_code = str(base64.b64encode(img))[2:-1]

#     return image_code

# # base64转cv2
# def base64_to_cv2(base64_code):
#     img_data = base64.b64decode(base64_code)
#     img_array = np.fromstring(img_data, np.uint8)
#     img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
#     return img

# def bytes2cv(img):
#     '''二进制图片转cv2

#     :param im: 二进制图片数据，bytes
#     :return: cv2图像，numpy.ndarray
#     '''
#     img_array = np.fromstring(img, np.uint8)  # 转换np序列
#     img_raw = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)  # 转换Opencv格式BGR
#     return img_raw

# def cv2bytes(im):
#     '''cv2转二进制图片

#     :param im: cv2图像，numpy.ndarray
#     :return: 二进制图片数据，bytes
#     '''
#     return np.array(cv2.imencode('.png', im)[1]).tobytes()

# def cv2_crop(im, box):
#     '''cv2实现类似PIL的裁剪

#     :param im: cv2加载好的图像
#     :param box: 裁剪的矩形，(left, upper, right, lower)元组
#     '''
#     return im.copy()[box[1]:box[3], box[0]:box[2], :]

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
        enable_database_storage = os.getenv("ENABLE_DATABASE_STORAGE", "false").lower() == "true"

        if enable_database_storage:
            # 将数据存储到数据库
            logging.debug("enable_database_storage为true，将会储存到数据库")
            self.test_mongodb_connection()
            self.db = self.client[os.getenv("DB_NAME")] # 创建数据库
        else:
            # 将数据存储到其他介质，如文件或内存
            self.client = None
            self.db = None
            logging.info("enable_database_storage为false，不会储存到数据库")

        self.DRIVER_IMPLICITY_WAIT_TIME = int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME"))
        self.RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT"))
        self.LOGIN_EXPECTED_TIME = int(os.getenv("LOGIN_EXPECTED_TIME"))
        self.RETRY_WAIT_TIME_OFFSET_UNIT = int(os.getenv("RETRY_WAIT_TIME_OFFSET_UNIT"))

    def base64_api(self, b64, typeid=33):
        data = {"username": self._tujian_uname, "password": self._tujian_passwd, "typeid": typeid, "image": b64}
        result = json.loads(requests.post("http://api.ttshitu.com/predict", json=data).text)
        if result['success']:
            return result["data"]["result"]
        else:
            #！！！！！！！注意：返回 人工不足等 错误情况 请加逻辑处理防止脚本卡死 继续重新 识别
            return result["message"]
        return ""

    def test_mongodb_connection(self):
        """测试数据库连接情况"""
        try:
            MONGO_URL = os.getenv("MONGO_URL")
            # 创建 MongoDB 客户端
            self.client = pymongo.MongoClient(MONGO_URL)

            # 检查连接是否可用
            self.client.admin.command('ping')

            logging.info("MongoDB connection test successful")
        except Exception as e:
            logging.error("Failed to connect to MongoDB: " + str(e))

    def connect_user_collection(self, user_id):
        """创建数据库集合，collection_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        # 创建集合
        collection_name = f"electricity_daily_usage_{user_id}"
        try:
            collection = self.db.create_collection(collection_name)
            logging.info(f"集合: {collection_name} 创建成功")
            self.create_col_index(collection)
        # 如果集合已存在，则不会创建
        except:
            collection = self.db[collection_name]
            logging.debug("集合: {collection_name} 集合已存在")
        finally:
            return collection

    def create_col_index(self, collection):
            # 创建索引
            try:
                collection.create_index([('date', pymongo.DESCENDING)], unique=True)
                logging.info(f"创建索引'date'成功")
            except:
                logging.debug("索引'date'已存在")

    def fetch(self):
        """the entry, only retry logic here """
        try:
            return self._fetch()
        except Exception as e:
            traceback.print_exc()
            logging.error(
                f"Webdriver quit abnormly, reason: {e}. {self.RETRY_TIMES_LIMIT} retry times left.")

    def _fetch(self):
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
            if DEBUG:
                driver.get(LOGIN_URL)
                pass
            else:
                if self._login(driver):
                    raise Exception("_login unsuccessed !")
            logging.info(f"Login successfully on {LOGIN_URL}")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            user_id_list = self._get_user_ids(driver)
            logging.info(f"将获取{len(user_id_list)}户数据，user_id: {user_id_list}")
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            balance_list = self._get_electric_balances(driver, user_id_list)  #
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            ### get data except electricity charge balance
            last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list  = self._get_other_data(driver, user_id_list)
            time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
            driver.quit()

            logging.info("Webdriver quit after fetching data successfully.")
            logging.info("浏览器已退出")
            return user_id_list, balance_list, last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list 

        finally:
            driver.quit()

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

    def _login(self, driver):

        driver.get(LOGIN_URL)
        logging.info("Open LOGIN_URL:{LOGIN_URL}.\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # swtich to username-password login page
        driver.find_element(By.CLASS_NAME, "user").click()
        logging.info("find_element 'user'.\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # input username and password
        input_elements = driver.find_elements(By.CLASS_NAME, "el-input__inner")
        input_elements[0].send_keys(self._username)
        logging.info(f"input_elements username : {self._username}.\r")
        input_elements[1].send_keys(self._password)
        logging.info(f"input_elements password : {self._password}.\r")
        # click agree button
        self._click_button(driver, By.XPATH, '//*[@id="login_box"]/div[2]/div[1]/form/div[1]/div[3]/div/span[2]')
        logging.info("Click the Agree option.\r")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # click login button
        self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
        logging.info("Click login button.\r")
        # sometimes ddddOCR may fail, so add retry logic)
        for retry_times in range(1, self.RETRY_TIMES_LIMIT + 1):

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
                    self._click_button(driver, By.CLASS_NAME, "el-button.el-button--primary")
                    time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT*2)
                    logging.info(f"Sliding CAPTCHA recognition failed and reloaded.\r")
                    continue
                except:
                    logging.debug(
                        f"Login failed, maybe caused by invalid captcha, {self.RETRY_TIMES_LIMIT - retry_times} retry times left.")
            else:
                return False
        
        logging.error(f"Login failed, maybe caused by Sliding CAPTCHA recognition failed")
        raise Exception(
            "Login failed, maybe caused by 1.incorrect phone_number and password, please double check. or 2. network, please mnodify LOGIN_EXPECTED_TIME in .env and run docker compose up --build.")
        return True
        

    def _get_electric_balances(self, driver, user_id_list):

        balance_list = []

        # switch to electricity charge balance page
        driver.get(BALANCE_URL)
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # get electricity charge balance for each user id
        for i in range(1, len(user_id_list) + 1):
            balance = self._get_eletric_balance(driver)
            if (balance is None):
                logging.info(f"Get electricity charge balance for {user_id_list[i - 1]} failed, Pass.")
            else:
                logging.info(
                    f"Get electricity charge balance for {user_id_list[i - 1]} successfully, balance is {balance} CNY.")
            balance_list.append(balance)

            # swtich to next userid
            if (i != len(user_id_list)):
                self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                self._click_button(driver, By.XPATH,
                                   f"//ul[@class='el-scrollbar__view el-select-dropdown__list']/li[{i + 1}]")

        return balance_list

    def _get_other_data(self, driver, user_id_list):
        last_daily_date_list = []
        last_daily_usage_list = []
        yearly_usage_list = []
        yearly_charge_list = []
        month_list = []
        month_charge_list = []
        month_usage_list = []
        # swithc to electricity usage page
        driver.get(ELECTRIC_USAGE_URL)

        # get data for each user id
        for i in range(1, len(user_id_list) + 1):

            yearly_usage, yearly_charge = self._get_yearly_data(driver)

            if yearly_usage is None:
                logging.error(f"Get year power usage for {user_id_list[i - 1]} failed, pass")
            else:
                logging.info(
                    f"Get year power usage for {user_id_list[i - 1]} successfully, usage is {yearly_usage} kwh")
            if yearly_charge is None:
                logging.error(f"Get year power charge for {user_id_list[i - 1]} failed, pass")
            else:
                logging.info(
                    f"Get year power charge for {user_id_list[i - 1]} successfully, yealrly charge is {yearly_charge} CNY")

            # get month usage
            month, month_usage, month_charge = self._get_month_usage(driver)
            if month is None:
                logging.error(f"Get month power usage for {user_id_list[i - 1]} failed, pass")
            else:
                for m in range(len(month)):
                    logging.info(
                        f"Get month power charge for {user_id_list[i - 1]} successfully, {month[m]} usage is {month_usage[m]} KWh, charge is {month_charge[m]} CNY.")
            # get yesterday usage
            last_daily_datetime, last_daily_usage = self._get_yesterday_usage(driver)

            # 新增储存30天用电量
            if self.client is not None:
                self.save_30_days_usage(driver, user_id_list[i - 1])

            if last_daily_usage is None:
                logging.error(f"Get daily power consumption for {user_id_list[i - 1]} failed, pass")
            else:
                logging.info(
                    f"Get daily power consumption for {user_id_list[i - 1]} successfully, , {last_daily_datetime} usage is {last_daily_usage} kwh.")

            last_daily_date_list.append(last_daily_datetime)
            last_daily_usage_list.append(last_daily_usage)
            yearly_charge_list.append(yearly_charge)
            yearly_usage_list.append(yearly_usage)
            month_list.append(month[-1])
            month_charge_list.append(month_charge[-1])
            month_usage_list.append(month_usage[-1])

            # switch to next user id
            if i != len(user_id_list):
                self._click_button(driver, By.CLASS_NAME, "el-input__suffix")
                time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
                self._click_button(driver, By.XPATH,
                                   f"//body/div[@class='el-select-dropdown el-popper']//ul[@class='el-scrollbar__view el-select-dropdown__list']/li[{i + 1}]")

        return last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list

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

    def _get_eletric_balance(self, driver):
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

    # 增加储存30天用电量的到mongodb的函数
    def save_30_days_usage(self, driver, user_id):
        """储存30天用电量"""
        self._click_button(driver, By.XPATH, "//*[@id='pane-second']/div[1]/div/label[2]/span[1]")
        time.sleep(self.RETRY_WAIT_TIME_OFFSET_UNIT)
        # 等待30天用电量的数据出现
        usage_element = driver.find_element(By.XPATH,
                                            "//div[@class='el-tab-pane dayd']//div[@class='el-table__body-wrapper is-scrolling-none']/table/tbody/tr[1]/td[2]/div")
        WebDriverWait(driver, self.DRIVER_IMPLICITY_WAIT_TIME).until(EC.visibility_of(usage_element))
        # 30天用电量的数据
        days_element = driver.find_elements(By.XPATH,
                                            "//*[@id='pane-second']/div[2]/div[2]/div[1]/div[3]/table/tbody/tr")  # 30天的值 列表 2023-05-0511.98


        # 连接数据库集合
        collection = self.connect_user_collection(user_id)

        # 将30天的用电量保存为字典
        for i in days_element:
            day = i.find_element(By.XPATH, "td[1]/div").text
            usage = i.find_element(By.XPATH, "td[2]/div").text
            dic = {'date': day, 'usage': float(usage)}
            # 插入到数据库
            try:
                collection.insert_one(dic)
                logging.info(f"{day}的用电量{usage}KWh已经成功存入数据库")
            except:
                logging.debug(f"{day}的用电量存入数据库失败,可能已经存在")

    @staticmethod
    def _click_button(driver, button_search_type, button_search_key):
        '''wrapped click function, click only when the element is clickable'''
        click_element = driver.find_element(button_search_type, button_search_key)
        WebDriverWait(driver, int(os.getenv("DRIVER_IMPLICITY_WAIT_TIME"))).until(EC.element_to_be_clickable(click_element))
        driver.execute_script("arguments[0].click();", click_element)

    @staticmethod
    def _is_captcha_legal(captcha):
        ''' check the ddddocr result, justify whether it's legal'''
        if (len(captcha) != 4):
            return False
        for s in captcha:
            if (not s.isalpha() and not s.isdigit()):
                return False
        return True

    @staticmethod
    def _get_chromium_version():
        result = str(subprocess.check_output(["chromium", "--product-version"]))
        version = re.findall(r"(\d*)\.", result)[0]
        logging.info(f"chromium-driver version is {version}")
        return int(version)

    @staticmethod 
    def _sliding_track(driver, distance):# 机器模拟人工滑动轨迹
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


if __name__ == "__main__":
    with open("bg.jpg", "rb") as f:
        test1 = f.read()
        print(type(test1))
        print(test1)
