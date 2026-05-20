import typing as typ
import os
import logging
import requests
import io

class PushplusNotify(typ.NamedTuple):

    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"检查电费余额。当余额低于 {BALANCE} 元时，将发送通知")
        if balance < BALANCE :
            PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN").split(",")
            for token in PUSHPLUS_TOKEN:
                title = "电费余额不足提醒"
                content = (f"您用户号{user_id}的当前电费余额为：{balance}元，请及时充值。" )
                url = ("http://www.pushplus.plus/send?token="+ token+ "&title="+ title+ "&content="+ content)
                resp = requests.get(url)
                logging.info(
                    f"用户 {user_id} 当前余额 {balance} 元低于 {BALANCE} 元，已发送通知，请注意查收并及时充值。"
                )
                return resp.status_code == 200
        return False

class UrlPushNotify(typ.NamedTuple):

    def __call__(self, user_id, balance):
        BALANCE = float(os.getenv("BALANCE", 10.0))
        logging.info(f"检查电费余额。当余额低于 {BALANCE} 元时，将发送通知")
        if balance < BALANCE :
            url = os.getenv("PUSH_URL")
            full_url = f"{url}"
            resp = requests.post(full_url, json={"user_id": user_id, "balance": balance})
            logging.info(
                f"用户 {user_id} 当前余额 {balance} 元低于 {BALANCE} 元，已发送通知，请注意查收并及时充值。"
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
            logging.info("推送二维码到URL")
            return resp.status_code == 200
        return False
