import logging
import logging.config
import os
import sys
import time
import schedule
import json
import random
from error_watcher import ErrorWatcher
from sensor_updator import SensorUpdator
from datetime import datetime,timedelta
from const import *
from data_fetcher import DataFetcher

def main():
    global RETRY_TIMES_LIMIT
    if 'PYTHON_IN_DOCKER' not in os.environ: 
        # 读取 .env 文件
        import dotenv
        dotenv.load_dotenv(verbose=True)
    if os.path.isfile('/data/options.json'):
        with open('/data/options.json') as f:
            options = json.load(f)
        try:
            for key, value in options.items():
                os.environ[key] = str(value)
            logging.info(f"当前以Homeassistant Add-on 形式运行.")
        except Exception as e:
            logging.error(f"Failing to read the options.json file, the program will exit with an error message: {e}.")
            sys.exit()

    try:
        PHONE_NUMBER = os.getenv("PHONE_NUMBER")
        logging.info(f"read env PHONE_NUMBER : {PHONE_NUMBER}")
        PASSWORD = os.getenv("PASSWORD")
        HASS_URL = os.getenv("HASS_URL")
        JOB_START_TIME = os.getenv("JOB_START_TIME","07:00" )
        LOG_LEVEL = os.getenv("LOG_LEVEL","INFO")
        VERSION = os.getenv("VERSION")
        RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        
        logger_init(LOG_LEVEL)
        logging.info(f"The current run runs as a docker image.")
    except Exception as e:
        logging.error(f"Failing to read the .env file, the program will exit with an error message: {e}.")
        sys.exit()

    logging.info(f"The current repository version is {VERSION}, and the repository address is https://github.com/ARC-MX/sgcc_electricity_new.git")
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"The current date is {current_datetime}.")

    logging.info(f"start init ErrorWatcher")
    ErrorWatcher.init(root_dir='/data/errors')
    logging.info(f'ErrorWatcher init done!')
    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)
    updator = SensorUpdator()

    # 生成随机延迟时间（-10分钟到+10分钟）
    random_delay_minutes = random.randint(-10, 10)
    parsed_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(minutes=random_delay_minutes)
    logging.info(f"The current logged-in user name is {PHONE_NUMBER}, the homeassistant address is {HASS_URL}, and the program will be executed every day at {parsed_time.strftime('%H:%M')}.")

    # 添加随机延迟
    next_run_time = parsed_time + timedelta(hours=12)

    logging.info(f'Run job now! The next run will be at {parsed_time.strftime("%H:%M")} and {next_run_time.strftime("%H:%M")} every day')
    schedule.every().day.at(parsed_time.strftime("%H:%M")).do(run_task, fetcher)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher)
    
    # 每5分钟重发一次数据，防止HA重启后数据丢失
    schedule.every(5).minutes.do(updator.republish)
    
    # 启动时先尝试从缓存恢复
    # 如果缓存恢复成功，则跳过本次启动时的实时抓取，避免频繁重启导致账号被封
    if not updator.republish():
        logging.info("No valid cache found, fetching data from State Grid...")
        run_task(fetcher)
    else:
        logging.info("Data restored from cache, skipping startup fetch to protect account.")
    
    while True:
        schedule.run_pending()
        time.sleep(1)


def run_task(data_fetcher: DataFetcher):
    for retry_times in range(1, RETRY_TIMES_LIMIT + 1):
        try:
            data_fetcher.fetch()
            return
        except Exception as e:
            logging.error(f"state-refresh task failed, reason is [{e}], {RETRY_TIMES_LIMIT - retry_times} retry times left.")
            continue

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
