from notify import UrlPushNotify
import os

def test_push_notify() -> None:
    os.environ["PUSH_URL"] = "http://192.168.28.56:1880/sg/balanceNotify"
    url_notify = UrlPushNotify()
    assert url_notify is not None
    assert url_notify("test_user", 5.0) is True