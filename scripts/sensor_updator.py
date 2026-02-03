import json
import logging
import os
from datetime import datetime, timedelta

import requests
from const import *


class SensorUpdator:

    def __init__(self):
        HASS_URL = os.getenv("HASS_URL")
        HASS_TOKEN = os.getenv("HASS_TOKEN")
        self.base_url = HASS_URL[:-1] if HASS_URL.endswith("/") else HASS_URL
        self.token = HASS_TOKEN
        self.RECHARGE_NOTIFY = os.getenv("RECHARGE_NOTIFY", "false").lower() == "true"

    def update_one_userid(self, user_id: str, balance: float, last_daily_date: str, last_daily_usage: float, yearly_charge: float, yearly_usage: float, month_charge: float, month_usage: float, notify=True):
        self._save_to_cache(user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage)
        postfix = f"_{user_id[-4:]}"
        if balance is not None:
            if notify:
                self.balance_notify(user_id, balance)
            self.update_balance(postfix, balance)
        if last_daily_usage is not None:
            self.update_last_daily_usage(postfix, last_daily_date, last_daily_usage)
        if yearly_usage is not None:
            self.update_yearly_data(postfix, yearly_usage, usage=True)
        if yearly_charge is not None:
            self.update_yearly_data(postfix, yearly_charge)
        if month_usage is not None:
            self.update_month_data(postfix, month_usage, usage=True)
        if month_charge is not None:
            self.update_month_data(postfix, month_charge)

        logging.info(f"User {user_id} state-refresh task run successfully!")

    def _get_cache_file(self):
        if 'PYTHON_IN_DOCKER' in os.environ: 
            return '/data/sgcc_cache.json'
        return 'sgcc_cache.json'

    def _save_to_cache(self, user_id, balance, last_daily_date, last_daily_usage, yearly_charge, yearly_usage, month_charge, month_usage):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        data = {}
        try:
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
        except Exception as e:
            logging.warning(f"Failed to load cache file: {e}")

        data[user_id] = {
            "balance": balance,
            "last_daily_date": last_daily_date,
            "last_daily_usage": last_daily_usage,
            "yearly_charge": yearly_charge,
            "yearly_usage": yearly_usage,
            "month_charge": month_charge,
            "month_usage": month_usage,
            "timestamp": datetime.now().isoformat()
        }

        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logging.debug(f"Saved data to cache file: {abs_cache_file}")
        except Exception as e:
            logging.error(f"Failed to save cache file {abs_cache_file}: {e}")

    def republish(self):
        cache_file = self._get_cache_file()
        abs_cache_file = os.path.abspath(cache_file)
        if not os.path.exists(cache_file):
            logging.info(f"No cache file found at {abs_cache_file}, skipping republish.")
            return False

        data = {}
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load cache file {abs_cache_file}: {e}")
            return False
            
        try:
            for user_id, values in data.items():
                logging.info(f"Republishing data for user {user_id} from cache.")
                # Filter out 'timestamp' from values before passing to update_one_userid
                clean_values = {k: v for k, v in values.items() if k != 'timestamp'}
                self.update_one_userid(user_id, **clean_values, notify=False)
            return True
        except Exception as e:
            logging.error(f"Failed to republish data: {e}")
            return False

    def get_sensor_state(self, sensor_name):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensor_name
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logging.warning(f"Failed to get sensor state for {sensor_name}: {e}")
            return None

    def should_update(self, sensor_name, new_state, check_attributes=None):
        current_state_obj = self.get_sensor_state(sensor_name)
        if not current_state_obj:
            return True
            
        # Check state
        try:
            current_state = current_state_obj.get('state')
            if current_state in ['unknown', 'unavailable', None]:
                return True
            
            curr_val = float(current_state)
            new_val = float(new_state)
            if abs(curr_val - new_val) > 0.001:
                return True
        except (ValueError, TypeError):
            # If we can't compare as floats, assume they are different
            return True
            
        # Check attributes if requested
        if check_attributes:
            curr_attrs = current_state_obj.get('attributes', {})
            for k, v in check_attributes.items():
                # Convert both to string for comparison to avoid type mismatches
                if str(curr_attrs.get(k)) != str(v):
                    return True
                    
        return False

    def update_last_daily_usage(self, postfix: str, last_daily_date: str, sensorState: float):
        sensorName = DAILY_USAGE_SENSOR_NAME + postfix
        
        if not self.should_update(sensorName, sensorState, {"last_reset": last_daily_date}):
             logging.info(f"Skipping update for {sensorName}, state matches.")
             return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_daily_date,
                "unit_of_measurement": "kWh",
                "icon": "mdi:lightning-bolt",
                "device_class": "energy",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} kWh")

    def update_balance(self, postfix: str, sensorState: float):
        sensorName = BALANCE_SENSOR_NAME + postfix
        
        # Balance updates normally have a volatile last_reset (current time), 
        # but if the balance itself hasn't changed, we can skip updating.
        if not self.should_update(sensorName, sensorState):
             logging.info(f"Skipping update for {sensorName}, state matches.")
             return

        last_reset = datetime.now().strftime("%Y-%m-%d, %H:%M:%S")
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "CNY",
                "icon": "mdi:cash",
                "device_class": "monetary",
                "state_class": "total",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} CNY")

    def update_month_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            MONTH_USAGE_SENSOR_NAME + postfix
            if usage
            else MONTH_CHARGE_SENSOR_NAME + postfix
        )
        current_date = datetime.now()
        first_day_of_current_month = current_date.replace(day=1)
        last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)
        last_reset = last_day_of_previous_month.strftime("%Y-%m")
        
        if not self.should_update(sensorName, sensorState, {"last_reset": last_reset}):
             logging.info(f"Skipping update for {sensorName}, state matches.")
             return

        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "measurement",
            },
        }

        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} {'kWh' if usage else 'CNY'}")

    def update_yearly_data(self, postfix: str, sensorState: float, usage=False):
        sensorName = (
            YEARLY_USAGE_SENSOR_NAME + postfix
            if usage
            else YEARLY_CHARGE_SENSOR_NAME + postfix
        )
        if datetime.now().month == 1:
            last_year = datetime.now().year -1 
            last_reset = datetime.now().replace(year=last_year).strftime("%Y")
        else:
            last_reset = datetime.now().strftime("%Y")
            
        if not self.should_update(sensorName, sensorState, {"last_reset": last_reset}):
             logging.info(f"Skipping update for {sensorName}, state matches.")
             return
             
        request_body = {
            "state": sensorState,
            "unique_id": sensorName,
            "attributes": {
                "last_reset": last_reset,
                "unit_of_measurement": "kWh" if usage else "CNY",
                "icon": "mdi:lightning-bolt" if usage else "mdi:cash",
                "device_class": "energy" if usage else "monetary",
                "state_class": "total_increasing",
            },
        }
        self.send_url(sensorName, request_body)
        logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState} {'kWh' if usage else 'CNY'}")

    def send_url(self, sensorName, request_body):
        headers = {
            "Content-Type": "application-json",
            "Authorization": "Bearer " + self.token,
        }
        url = self.base_url + API_PATH + sensorName  # /api/states/<entity_id>
        try:
            response = requests.post(url, json=request_body, headers=headers)
            logging.debug(
                f"Homeassistant REST API invoke, POST on {url}. response[{response.status_code}]: {response.content}"
            )
        except Exception as e:
            logging.error(f"Homeassistant REST API invoke failed, reason is {e}")

    def balance_notify(self, user_id, balance):

        if self.RECHARGE_NOTIFY :
            BALANCE = float(os.getenv("BALANCE", 10.0))
            PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN").split(",")        
            logging.info(f"Check the electricity bill balance. When the balance is less than {BALANCE} CNY, the notification will be sent = {self.RECHARGE_NOTIFY}")
            if balance < BALANCE :
                for token in PUSHPLUS_TOKEN:
                    title = "电费余额不足提醒"
                    content = (f"您用户号{user_id}的当前电费余额为：{balance}元，请及时充值。" )
                    url = ("http://www.pushplus.plus/send?token="+ token+ "&title="+ title+ "&content="+ content)
                    requests.get(url)
                    logging.info(
                        f"The current balance of user id {user_id} is {balance} CNY less than {BALANCE} CNY, notice has been sent, please pay attention to check and recharge."
                    )
        else :
            logging.info(
            f"Check the electricity bill balance, the notification will be sent = {self.RECHARGE_NOTIFY}")
            return

