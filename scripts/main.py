import logging
import logging.config
import os
import sys
import time
import schedule
import json
from datetime import datetime,timedelta
from const import *
from data_fetcher import DataFetcher


def main():
    global RETRY_TIMES_LIMIT
    if 'PYTHON_IN_DOCKER' not in os.environ: 
        # 读取 .env 文件
        import dotenv
        dotenv.load_dotenv(verbose=True)
        try:
            PHONE_NUMBER = os.getenv("PHONE_NUMBER")
            PASSWORD = os.getenv("PASSWORD")
            HASS_URL = os.getenv("HASS_URL")
            JOB_START_TIME = os.getenv("JOB_START_TIME","07:00" )
            LOG_LEVEL = os.getenv("LOG_LEVEL","INFO")
            VERSION = os.getenv("VERSION")
            RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        except Exception as e:
            logging.error(f"Failing to read the .env file, the program will exit with an error message: {e}.")
            sys.exit()
    else:
        with open('/data/options.json') as f:
            options = json.load(f)
        try:
            PHONE_NUMBER = options.get("PHONE_NUMBER")
            PASSWORD = options.get("PASSWORD")
            HASS_URL = options.get("HASS_URL")
            JOB_START_TIME = options.get("JOB_START_TIME", "07:00")
            LOG_LEVEL = options.get("LOG_LEVEL", "INFO")
            VERSION = os.getenv("VERSION")
            RETRY_TIMES_LIMIT = int(options.get("RETRY_TIMES_LIMIT", 5))
            os.environ["HASS_URL"] = options.get("HASS_URL", "http://homeassistant.local:8123/")
            os.environ["HASS_TOKEN"] = options.get("HASS_TOKEN", "")
            os.environ["ENABLE_DATABASE_STORAGE"] = options.get("ENABLE_DATABASE_STORAGE", "false")
            os.environ["IGNORE_USER_ID"] = options.get("IGNORE_USER_ID", "xxxxx,xxxxx")
            os.environ["DB_NAME"] = options.get("DB_NAME", "homeassistant.db")
            os.environ["RETRY_TIMES_LIMIT"] = options.get("RETRY_TIMES_LIMIT", 5)
            os.environ["LOGIN_EXPECTED_TIME"] = options.get("LOGIN_EXPECTED_TIME", 10)
            os.environ["RETRY_WAIT_TIME_OFFSET_UNIT"] = options.get("RETRY_WAIT_TIME_OFFSET_UNIT", 10)
            os.environ["DATA_RETENTION_DAYS"] = options.get("DATA_RETENTION_DAYS", 7)
            os.environ["RECHARGE_NOTIFY"] = options.get("RECHARGE_NOTIFY", "false")
            os.environ["BALANCE"] = options.get("BALANCE", 5.0)
            os.environ["PUSHPLUS_TOKEN"] = options.get("PUSHPLUS_TOKEN", "")
        except Exception as e:
            logging.error(f"Failing to read the options.json file, the program will exit with an error message: {e}.")
            sys.exit()
    logger_init(LOG_LEVEL)
    logging.info(f"The current repository version is {VERSION}, and the repository address is https://github.com/ARC-MX/sgcc_electricity_new.git")
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"The current date is {current_datetime}.")

    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD)
    logging.info(f"The current logged-in user name is {PHONE_NUMBER}, the homeassistant address is {HASS_URL}, and the program will be executed every day at {JOB_START_TIME}.")

    next_run_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(hours=12)
    logging.info(f'Run job now! The next run will be at {JOB_START_TIME} and {next_run_time.strftime("%H:%M")} every day')
    schedule.every().day.at(JOB_START_TIME).do(run_task, fetcher)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher)
    run_task(fetcher)

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
