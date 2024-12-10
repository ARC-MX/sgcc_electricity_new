import logging
import logging.config
import requests
import os
import sys
import time
import traceback
from datetime import datetime,timedelta

import dotenv
import schedule

from const import *
from data_fetcher import DataFetcher
from sensor_updator import SensorUpdator

BALANCE = 0.0
PUSHPLUS_TOKEN = []
RECHARGE_NOTIFY = False
def main():
    # 读取 .env 文件
    dotenv.load_dotenv(verbose=True)
    global BALANCE
    global PUSHPLUS_TOKEN
    global RECHARGE_NOTIFY
    try:
        PHONE_NUMBER = os.getenv("PHONE_NUMBER")
        PASSWORD = os.getenv("PASSWORD")
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        JOB_START_TIME = os.getenv("JOB_START_TIME")
        LOG_LEVEL = os.getenv("LOG_LEVEL")
        VERSION = os.getenv("VERSION")
        BALANCE = float(os.getenv("BALANCE"))
        PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN").split(",")
        RECHARGE_NOTIFY = os.getenv("RECHARGE_NOTIFY", "false").lower() == "true"
    except Exception as e:
        logging.error(f"Failing to read the .env file, the program will exit with an error message: {e}.")
        sys.exit()

    logger_init(LOG_LEVEL)
    logging.info(f"The current repository version is {VERSION}, and the repository address is https://github.com/ARC-MX/sgcc_electricity_new.git")

    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)
    updator = SensorUpdator(HASS_URL, HASS_TOKEN)
    logging.info(f"The current logged-in user name is {PHONE_NUMBER}, the homeassistant address is {HASS_URL}, and the program will be executed every day at {JOB_START_TIME}.")

    next_run_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(hours=12)
    logging.info(f'Run job now! The next run will be at {JOB_START_TIME} and {next_run_time.strftime("%H:%M")} every day')
    schedule.every().day.at(JOB_START_TIME).do(run_task, fetcher, updator)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher, updator)
    run_task(fetcher, updator)

    while True:
        schedule.run_pending()
        time.sleep(1)


def run_task(data_fetcher: DataFetcher, sensor_updator: SensorUpdator):
    try:
        user_id_list, balance_list, last_daily_date_list, last_daily_usage_list, yearly_charge_list, yearly_usage_list, month_list, month_usage_list, month_charge_list = data_fetcher.fetch()
        
        if not user_id_list:
            logging.error("Failed to get user IDs, task aborted")
            return

        for i in range(0, len(user_id_list)):
            try:
                current_user_id = user_id_list[i]
                profix = f"_{current_user_id}" if len(user_id_list) > 1 else ""
                logging.info(f"开始更新用户 {current_user_id} 的数据")

                # 更新电费余额并检查是否需要发送余额不足提醒
                if balance_list[i] is not None:
                    sensor_name = BALANCE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, None, balance_list[i])
                    logging.info(f"电费余额传感器 {sensor_name} 更新成功: {balance_list[i]} CNY")
                    
                    if balance_list[i] < BALANCE and RECHARGE_NOTIFY:
                        for token in PUSHPLUS_TOKEN:
                            title = '电费余额不足提醒'
                            content = f'您用户号{current_user_id}的当前电费余额为：{balance_list[i]}元，请及时充值。'
                            url = 'http://www.pushplus.plus/send?token='+token+'&title='+title+'&content='+content
                            try:
                                requests.get(url)
                                logging.info(f'已发送余额不足提醒,用户 {current_user_id} 当前余额 {balance_list[i]} CNY')
                            except Exception as e:
                                logging.error(f'发送余额不足提醒失败,用户 {current_user_id}: {str(e)}')

                # 更新日用电量
                if last_daily_usage_list[i] is not None:
                    sensor_name = DAILY_USAGE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, last_daily_date_list[i], last_daily_usage_list[i], month=False)
                    logging.info(f"日用电量传感器 {sensor_name} 更新成功: {last_daily_usage_list[i]} KWH")
                
                # 更新年度用电量
                if yearly_usage_list[i] is not None:
                    sensor_name = YEARLY_USAGE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, None, yearly_usage_list[i], month=False)
                    logging.info(f"年度用电量传感器 {sensor_name} 更新成功: {yearly_usage_list[i]} KWH")
                
                # 更新年度电费
                if yearly_charge_list[i] is not None:
                    sensor_name = YEARLY_CHARGE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, None, yearly_charge_list[i], month=False)
                    logging.info(f"年度电费传感器 {sensor_name} 更新成功: {yearly_charge_list[i]} CNY")
                
                # 更新月度电费
                if month_charge_list[i] is not None:
                    sensor_name = MONTH_CHARGE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, month_list[i], month_charge_list[i], month=True)
                    logging.info(f"月度电费传感器 {sensor_name} 更新成功: {month_charge_list[i]} CNY")
                
                # 更新月度用电量
                if month_usage_list[i] is not None:
                    sensor_name = MONTH_USAGE_SENSOR_NAME + profix
                    sensor_updator.update(sensor_name, month_list[i], month_usage_list[i], month=True)
                    logging.info(f"月度用电量传感器 {sensor_name} 更新成功: {month_usage_list[i]} KWH")

                logging.info(f"用户 {current_user_id} 的所有数据更新完成")

            except Exception as e:
                logging.error(f"更新用户 {current_user_id} 的数据失败: {str(e)}")
                continue

        logging.info("所有用户数据更新完成!")
    except Exception as e:
        logging.error(f"数据更新任务失败: {str(e)}")
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
