import logging
import logging.config
import os
import sys
import time
import schedule
from datetime import datetime,timedelta
from const import *
from data_fetcher import DataFetcher


def main():
    if 'PYTHON_IN_DOCKER' not in os.environ: 
        # 读取 .env 文件
        dotenv.load_dotenv(verbose=True)
    global RETRY_TIMES_LIMIT
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

    logger_init(LOG_LEVEL)
    logging.info(f"The current repository version is {VERSION}, and the repository address is https://github.com/ARC-MX/sgcc_electricity_new.git")

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
