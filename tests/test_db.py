
from db import MysqlDB
import os


def test_mysql() -> None:
    os.environ["MYSQL_HOST"] = "192.168.28.68"
    os.environ["MYSQL_USER"] = "test"
    os.environ["MYSQL_PASSWORD"] = "test-123.a"
    os.environ["MYSQL_DATABASE"] = "test"
    os.environ["MYSQL_PORT"] = "3306"
    db = MysqlDB()
    assert db.connect_user_db("test_user") is True
    assert db.insert_data({"date": "2023-10-02", "usage": 123.45}) is True
    assert db.insert_expand_data({"name": "test_key", "value": "test_value"}) is True
    # Clean up environment variables
    del os.environ["MYSQL_HOST"]
    del os.environ["MYSQL_USER"]
    del os.environ["MYSQL_PASSWORD"]
    del os.environ["MYSQL_DATABASE"]
    del os.environ["MYSQL_PORT"]
    