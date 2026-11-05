import os

# 国网电力官网
LOGIN_URL = "https://www.95598.cn/osgweb/login"
ELECTRIC_USAGE_URL = "https://www.95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://www.95598.cn/osgweb/userAcc"
BILL_SUMMARY_URL = "https://www.95598.cn/osgweb/electricityCharge"
STEP_ELECTRICITY_URL = "https://www.95598.cn/osgweb/stepElectricityConsumption"
ELECTRIC_BILL_SUMMARY_URL = (
    "https://www.95598.cn/osgweb01/electricityChargeQuery/queryElectricBillSummary"
)

# Home Assistant
SUPERVISOR_URL = "http://supervisor/core"
API_PATH = "/api/states/"

BALANCE_SENSOR_NAME = "sensor.electricity_charge_balance"
DAILY_USAGE_SENSOR_NAME = "sensor.last_electricity_usage"
YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_electricity_usage"
YEARLY_CHARGE_SENSOR_NAME = "sensor.yearly_electricity_charge"
MONTH_USAGE_SENSOR_NAME = "sensor.month_electricity_usage"
MONTH_CHARGE_SENSOR_NAME = "sensor.month_electricity_charge"
MONTH_VALLEY_SENSOR_NAME = "sensor.month_valley_usage"
MONTH_FLAT_SENSOR_NAME = "sensor.month_flat_usage"
MONTH_PEAK_SENSOR_NAME = "sensor.month_peak_usage"
MONTH_TIP_SENSOR_NAME = "sensor.month_tip_usage"
PREPAY_BALANCE_SENSOR_NAME = "sensor.prepay_balance"
BALANCE_UNIT = "CNY"
USAGE_UNIT = "KWH"
USAGE_UNIT_CUBE = "m³"

# 上海燃气实体定义（二开新增）
SHRQ_BALANCE_SENSOR_NAME = "sensor.shrq_charge_balance"   # 本期消费
SHRQ_MONTHLY_USAGE_SENSOR_NAME = "sensor.last_shrq_usage"   # 本期用量 字
SHRQ_YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_shrq_usage"  # 燃气年用量 字

# 浦东威立雅水费实体定义（二开新增）
PDWLY_MONTHLY_USAGE_TOTAL_SENSOR_NAME = "sensor.last_pdwly_usage_total"   # 本期消费 m³
PDWLY_MONTHLY_USAGE_WATER_SENSOR_NAME = "sensor.last_pdwly_usage_water"   # 本期消费 生活水m³
PDWLY_MONTHLY_USAGE_DIRTY_SENSOR_NAME = "sensor.last_pdwly_usage_dirty"   # 本期消费 污水m³
PDWLY_MONTHLY_CHARGE_TOTAL_SENSOR_NAME = "sensor.last_pdwly_charge_total"   # 本期消费
PDWLY_MONTHLY_CHARGE_WATER_SENSOR_NAME = "sensor.last_pdwly_charge_water"   # 本期消费 生活水
PDWLY_MONTHLY_CHARGE_DIRTY_SENSOR_NAME = "sensor.last_pdwly_charge_dirty"   # 本期消费 污水

LLM_API_KEY = os.getenv('LLM_API_KEY', '').strip()
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.siliconflow.cn/v1')
LLM_MODEL = os.getenv('LLM_MODEL', 'Qwen/Qwen3.5-35B-A3B')


def get_data_dir() -> str:
    """获取数据存储目录：Docker 用 /data，本地用项目下的 data/"""
    if 'PYTHON_IN_DOCKER' in os.environ:
        return '/data'
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

