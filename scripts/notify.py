import typing as typ
import os
import logging
import requests
import io

class PushplusNotify(typ.NamedTuple):
    
    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"Check the electricity bill balance. When the balance is less than {BALANCE} CNY, the notification will be sent")
        if balance < BALANCE :
            PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN").split(",")
            for token in PUSHPLUS_TOKEN:
                title = "电费余额不足提醒"
                content = (f"您用户号{user_id}的当前电费余额为：{balance}元，请及时充值。" )
                url = ("http://www.pushplus.plus/send?token="+ token+ "&title="+ title+ "&content="+ content)
                resp = requests.get(url)
                logging.info(
                    f"The current balance of user id {user_id} is {balance} CNY less than {BALANCE} CNY, notice has been sent, please pay attention to check and recharge."
                )
                return resp.status_code == 200
        return False
        
class UrlPushNotify(typ.NamedTuple):
    
    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"Check the electricity bill balance. When the balance is less than {BALANCE} CNY, the notification will be sent")
        if balance < BALANCE :
            url = os.getenv("PUSH_URL")
            full_url = f"{url}"
            resp = requests.post(full_url, json={"user_id": user_id, "balance": balance})
            logging.info(
                f"The current balance of user id {user_id} is {balance} CNY less than {BALANCE} CNY, notice has been sent, please pay attention to check and recharge."
            )
            return resp.status_code == 200
        return False

class UrlLoginQrCodeNotify(typ.NamedTuple):

    def __call__(self, qrcode) -> bool:
        url = os.getenv("PUSH_QRCODE_URL")

        if url:
            files = {
                'file': ("qrcode.png", io.BytesIO(qrcode), 'image/png')
            }
            resp = requests.post(url, files=files)
            logging.info("push qrcode to url")
            return resp.status_code == 200
        return False