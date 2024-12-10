import logging
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta

import requests

from const import *


class SensorUpdator:

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url[:-1] if base_url.endswith("/") else base_url
        self.token = token

    def update(self, sensorName: str, present_date: str or None, sensorState: float, month=False):
        """
        Update the sensor state
        :param sensorName: 此为id，不是name
        :param present_date: 主要用于确定最近一次用电量所代表的日期
        :return:
        """
        token = os.getenv("SUPERVISOR_TOKEN") if self.base_url == SUPERVISOR_URL else self.token
        headers = {
            "Content-Type": "application-json",
            "Authorization": "Bearer " + token
        }
        if present_date is None:    #年数据
            request_body = {
                "state": sensorState,
                "unique_id": sensorName,
            }
        elif month:             #月数据
            last_updated = datetime.now()-relativedelta(months=1)
            last_reset = last_updated.strftime("%Y-%m")
            request_body = {
                "state": sensorState,
                "unique_id": sensorName,
                "attributes": {
                    "last_reset": last_reset
                }
            }
        else:             #日数据
            request_body = {
                "state": sensorState,
                "attributes": {
                    "last_reset": present_date
                }
            }

        url = self.base_url + API_PATH + sensorName
        try:
            response = requests.post(url, json=request_body, headers=headers)
            logging.info(f"Home Assistant REST API 调用成功: POST {url}, 响应状态码: {response.status_code}, 响应内容: {response.content.decode('utf-8')}")
            logging.info(f"传感器 {sensorName} 状态已更新: {sensorState}")
        except Exception as e:
            logging.error(f"Home Assistant REST API 调用失败: {str(e)}")