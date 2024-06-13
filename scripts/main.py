import logging
import logging.config
import os
import sys
import time
import traceback
from datetime import datetime

import dotenv
import schedule

from const import *
from data_fetcher import DataFetcher
from sensor_updator import SensorUpdator


def main():
    # 读取 .env 文件
    dotenv.load_dotenv()

    try:
        PHONE_NUMBER = os.getenv("PHONE_NUMBER")
        PASSWORD = os.getenv("PASSWORD")
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        JOB_START_TIME = os.getenv("JOB_START_TIME")
        FIRST_SLEEP_TIME = int(os.getenv("FIRST_SLEEP_TIME"))
        LOG_LEVEL = os.getenv("LOG_LEVEL")

    except Exception as e:
        logging.error(f"读取.env文件失败，程序将退出，错误信息为{e}")
        sys.exit()

    logger_init(LOG_LEVEL)
    logging.info("程序开始，当前仓库版本为1.4.1，仓库地址为https://github.com/ARC-MX/sgcc_electricity_new.git")

    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)
    updator = SensorUpdator(HASS_URL, HASS_TOKEN)
    logging.info(f"当前登录的用户名为: {PHONE_NUMBER}，homeassistant地址为{HASS_URL},程序将在每天{JOB_START_TIME}执行")
    schedule.every().day.at(JOB_START_TIME).do(run_task, fetcher, updator)

    if datetime.now().time() < datetime.strptime(JOB_START_TIME, "%H:%M").time():
        logging.info(f"此次为首次运行，当前时间早于 JOB_START_TIME: {JOB_START_TIME}，{JOB_START_TIME}再执行！")
        schedule.every().day.at(JOB_START_TIME).do(run_task, fetcher, updator)
    else:
        logging.info(f"此次为首次运行，等待时间(FIRST_SLEEP_TIME)为{FIRST_SLEEP_TIME}秒，可在.env中设置")
        time.sleep(FIRST_SLEEP_TIME)
        run_task(fetcher, updator)

    while True:
        schedule.run_pending()
        time.sleep(1)


def run_task(data_fetcher: DataFetcher, sensor_updator: SensorUpdator):
    try:
        user_id_list, balance_list, last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list = data_fetcher.fetch()
        # user_id_list, balance_list, last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list = ['123456'],[58.1],['2024-05-12'],[3.0],['239.1'],['533'],['2024-04-01-2024-04-30'],['118'],['52.93']
        for i in range(0, len(user_id_list)):
            profix = f"_{user_id_list[i]}" if len(user_id_list) > 1 else ""
            if balance_list[i] is not None:
                sensor_updator.update(BALANCE_SENSOR_NAME + profix, None, balance_list[i], BALANCE_UNIT)
            if last_daily_usage_list[i] is not None:
                sensor_updator.update(DAILY_USAGE_SENSOR_NAME + profix, last_daily_date_list[i], last_daily_usage_list[i], USAGE_UNIT)
            if yearly_usage_list[i] is not None:
                sensor_updator.update(YEARLY_USAGE_SENSOR_NAME + profix, None, yearly_usage_list[i], USAGE_UNIT)
            if yearly_charge_list[i] is not None:
                sensor_updator.update(YEARLY_CHARGE_SENSOR_NAME + profix, None, yearly_charge_list[i], BALANCE_UNIT)
            if month_charge_list[i] is not None:
                sensor_updator.update(MONTH_CHARGE_SENSOR_NAME + profix, month_list[i], month_charge_list[i], BALANCE_UNIT, month=True)
            if month_usage_list[i] is not None:
                sensor_updator.update(MONTH_USAGE_SENSOR_NAME + profix, month_list[i], month_usage_list[i], USAGE_UNIT, month=True)
        logging.info("state-refresh task run successfully!")
    except Exception as e:
        logging.error(f"state-refresh task failed, reason is {e}")
        traceback.print_exc()


def logger_init(level: str):
    logger = logging.getLogger()
    logger.setLevel(level)
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    format = logging.Formatter("%(asctime)s  [%(levelname)-8s] ---- %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(format)
    logger.addHandler(sh)


if __name__ == "__main__":
    main()
