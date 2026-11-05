import nest_asyncio; nest_asyncio.apply()
import logging
import logging.config
import os
import sys
import time
import schedule
import json
import random
import traceback
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
            import const
            const.LLM_API_KEY = os.getenv('LLM_API_KEY', '').strip()
            const.LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.siliconflow.cn/v1')
            const.LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3.5-35B-A3B')
            logging.info(f"当前以Homeassistant Add-on 形式运行.")
        except Exception as e:
            logging.error(f"读取 options.json 文件失败，程序将退出，错误信息: {e}。")
            sys.exit()

    try:
        PHONE_NUMBER = os.getenv("PHONE_NUMBER")
        logging.info(f"读取环境变量 PHONE_NUMBER : {PHONE_NUMBER}")
        PASSWORD = os.getenv("PASSWORD")
        HASS_URL = os.getenv("HASS_URL")
        JOB_START_TIME = os.getenv("JOB_START_TIME","07:00" ).strip('"').strip("'")
        LOG_LEVEL = os.getenv("LOG_LEVEL","INFO")
        VERSION = os.getenv("VERSION")
        RETRY_TIMES_LIMIT = int(os.getenv("RETRY_TIMES_LIMIT", 5))
        # 二开新增：上海燃气 / 浦东威立雅
        SHRQ_PHONE_NUMBER = os.getenv("SHRQ_PHONE_NUMBER")
        SHRQ_PASSWORD = os.getenv("SHRQ_PASSWORD")
        PDWLY_ID = os.getenv("PDWLY_ID")

        logger_init(LOG_LEVEL)
        logging.info(f"当前以Docker镜像方式运行。")
    except Exception as e:
        logging.error(f"读取 .env 文件失败，程序将退出，错误信息: {e}。")
        sys.exit()

    logging.info(f"当前仓库版本为 {VERSION}，仓库地址为 https://github.com/ARC-MX/sgcc_electricity_new.git")
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logging.info(f"当前日期为 {current_datetime}。")

    logging.info(f"开始初始化 ErrorWatcher")
    ErrorWatcher.init(root_dir='/data/errors')
    logging.info(f'ErrorWatcher 初始化完成！')
    fetcher = DataFetcher(PHONE_NUMBER, PASSWORD, SHRQ_PHONE_NUMBER, SHRQ_PASSWORD, PDWLY_ID)
    updator = SensorUpdator()

    # 生成随机延迟时间（-10分钟到+10分钟）
    random_delay_minutes = random.randint(-10, 10)
    parsed_time = datetime.strptime(JOB_START_TIME, "%H:%M") + timedelta(minutes=random_delay_minutes)
    logging.info(f"当前登录用户名为 {PHONE_NUMBER}，Home Assistant 地址为 {HASS_URL}，程序将每天在 {parsed_time.strftime('%H:%M')} 执行。")

    # 添加随机延迟
    next_run_time = parsed_time + timedelta(hours=12)

    logging.info(f'立即执行任务！下次运行时间为每天 {parsed_time.strftime("%H:%M")} 和 {next_run_time.strftime("%H:%M")}')
    schedule.every().day.at(parsed_time.strftime("%H:%M")).do(run_task, fetcher, updator)
    schedule.every().day.at(next_run_time.strftime("%H:%M")).do(run_task, fetcher, updator)

    # 重发缓存到 HA 的间隔（分钟），默认 60 分钟，用于防止 HA 重启后数据丢失
    CACHE_REPUBLISH_INTERVAL = int(os.getenv("CACHE_REPUBLISH_INTERVAL", "60"))
    schedule.every(CACHE_REPUBLISH_INTERVAL).minutes.do(republish_or_fetch, updator, fetcher)

    # 启动时先尝试从缓存恢复
    # 如果缓存恢复成功，则跳过本次启动时的实时抓取，避免频繁重启导致账号被封
    if not updator.republish():
        logging.info("未找到有效缓存，正在从国家电网获取数据...")
        run_task(fetcher, updator)
    else:
        logging.info("已从缓存恢复数据，跳过启动时抓取以保护账号。")

    while True:
        schedule.run_pending()
        time.sleep(1)


def republish_or_fetch(updator: SensorUpdator, fetcher: DataFetcher):
    if not updator.republish():
        logging.info("缓存数据已过期或不存在，正在从国家电网获取数据...")
        run_task(fetcher, updator)


def run_task(data_fetcher: DataFetcher, sensor_updator: SensorUpdator):
    # 二开新增：浦东威立雅水费数据（失败不影响国网/燃气）
    try:
        user_id_list, month_usage_total_list, month_usage_water_list, month_usage_dirty_list, month_charge_total_list, month_charge_water_list, month_charge_dirty_list, month_list = data_fetcher.fetch_pdwly()
        for i in range(0, len(user_id_list)):
            profix = f"_{user_id_list[i]}" if len(user_id_list) > 1 else ""
            if month_usage_total_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_USAGE_TOTAL_SENSOR_NAME + profix, month_list[i],
                                      month_usage_total_list[i], USAGE_UNIT_CUBE, month=True)
            if month_usage_water_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_USAGE_WATER_SENSOR_NAME + profix, month_list[i],
                                      month_usage_water_list[i], USAGE_UNIT_CUBE, month=True)
            if month_usage_dirty_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_USAGE_DIRTY_SENSOR_NAME + profix, month_list[i],
                                      month_usage_dirty_list[i], USAGE_UNIT_CUBE, month=True)
            if month_charge_total_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_CHARGE_TOTAL_SENSOR_NAME + profix, month_list[i],
                                      month_charge_total_list[i], BALANCE_UNIT, month=True)
            if month_charge_water_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_CHARGE_WATER_SENSOR_NAME + profix, month_list[i],
                                      month_charge_water_list[i], BALANCE_UNIT, month=True)
            if month_charge_dirty_list[i] is not None:
                sensor_updator.update(PDWLY_MONTHLY_CHARGE_DIRTY_SENSOR_NAME + profix, month_list[i],
                                      month_charge_dirty_list[i], BALANCE_UNIT, month=True)
    except Exception as e:
        logging.error(f"浦东威立雅水费数据刷新任务失败，原因是 [{e}]")
        traceback.print_exc()

    # 二开新增：上海燃气数据（失败不影响国网/水费）
    try:
        user_id_list, yearly_usage_list, month_usage_list, month_charge_list, month_list = data_fetcher.fetch_shrq()
        for i in range(0, len(user_id_list)):
            profix = f"_{user_id_list[i]}" if len(user_id_list) > 1 else ""
            if month_usage_list[i] is not None:
                sensor_updator.update(SHRQ_BALANCE_SENSOR_NAME + profix, None, month_usage_list[i], USAGE_UNIT_CUBE)
            if month_charge_list[i] is not None:
                sensor_updator.update(SHRQ_MONTHLY_USAGE_SENSOR_NAME + profix, month_list[i], month_charge_list[i],
                                      BALANCE_UNIT, month=True)
            if yearly_usage_list[i] is not None:
                sensor_updator.update(SHRQ_YEARLY_USAGE_SENSOR_NAME + profix, month_list[i], yearly_usage_list[i],
                                      USAGE_UNIT_CUBE, month=True)
    except Exception as e:
        logging.error(f"上海燃气数据刷新任务失败，原因是 [{e}]")
        traceback.print_exc()

    # 国网电力（上游逻辑，含重试）
    for retry_times in range(1, RETRY_TIMES_LIMIT + 1):
        try:
            data_fetcher.fetch()
            return
        except RuntimeError as e:
            # LLM 配置错误等不可恢复错误，立即退出
            logging.error(f"致命错误，程序退出: {e}")
            sys.exit(1)
        except Exception as e:
            logging.error(f"状态刷新任务失败，原因是 [{e}]，还剩 {RETRY_TIMES_LIMIT - retry_times} 次重试机会。")
            continue

def logger_init(level: str):
    logger = logging.getLogger()
    logger.setLevel(level.strip().strip('\'" '))  # 兼容 .env 中带引号和空格的 LOG_LEVEL
    # 清除已有 handler（避免 basicConfig 自动添加的默认 handler 导致日志重复）
    logger.handlers.clear()
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    format = logging.Formatter("%(asctime)s  [%(levelname)-8s] ---- %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(format)
    logger.addHandler(sh)


if __name__ == "__main__":
    main()
