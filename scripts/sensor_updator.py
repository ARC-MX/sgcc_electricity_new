import logging
import os
from datetime import datetime

import requests

from const import *


class SensorUpdator:

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url[:-1] if base_url.endswith("/") else base_url
        self.token = token

    def update(self, sensorName: str, present_date: str or None, sensorState: float, sensorUnit: str, month=False):
        """
        Update the sensor state
        :param sensorName: 此为id，不是name
        :param present_date: 主要用于确定最近一次用电量所代表的日期
        :param sensorState: 传感器的状态
        :param sensorUnit: 传感器的单位
        :return:
        """
        token = os.getenv("SUPERVISOR_TOKEN") if self.base_url == SUPERVISOR_URL else self.token
        headers = {
            "Content-Type": "application-json",
            "Authorization": "Bearer " + token

        }
        if month:
            last_updated = datetime.now().strftime("%Y-%m")
        else:
            last_updated = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        if present_date:
            request_body = {
                "state": sensorState,
                "attributes": {
                    "present_date": present_date,
                    "unit_of_measurement": sensorUnit
                }
            }
        else:
            request_body = {
                "state": sensorState,
                "unique_id": sensorName,
                "attributes": {
                    "unit_of_measurement": sensorUnit
                }
            }

        url = self.base_url + API_PATH + sensorName # /api/states/<entity_id>

        try:
            response = requests.post(url, json=request_body, headers=headers)
            logging.debug(
                f"Homeassistant REST API invoke, POST on {url}. response[{response.status_code}]: {response.content}")
            logging.info(f"Homeassistant sensor {sensorName} state updated: {sensorState}{sensorUnit}")
        except Exception as e:
            logging.error(f"Homeassistant REST API invoke failed, reason is {e}")