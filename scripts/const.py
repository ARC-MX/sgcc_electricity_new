# 填写普通参数 不要填写密码等敏感信息
# 国网电力官网
LOGIN_URL = "https://95598.cn/osgweb/login"
ELECTRIC_USAGE_URL = "https://95598.cn/osgweb/electricityCharge"
BALANCE_URL = "https://95598.cn/osgweb/userAcc"


# Home Assistant
SUPERVISOR_URL = "http://supervisor/core"
API_PATH = "/api/states/" # https://developers.home-assistant.io/docs/api/rest/

BALANCE_SENSOR_NAME = "sensor.electricity_charge_balance"
DAILY_USAGE_SENSOR_NAME = "sensor.last_electricity_usage"
YEARLY_USAGE_SENSOR_NAME = "sensor.yearly_electricity_usage"
YEARLY_CHARGE_SENSOR_NAME = "sensor.yearly_electricity_charge"
MONTH_USAGE_SENSOR_NAME = "sensor.month_electricity_usage"
MONTH_CHARGE_SENSOR_NAME = "sensor.month_electricity_charge"
BALANCE_UNIT = "CNY"
USAGE_UNIT = "KWH"

