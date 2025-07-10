import os
from webdriver_manager.firefox import GeckoDriverManager

driver_path = GeckoDriverManager().install()
# 测试安装
if __name__ == "__main__":
    print(f"{driver_path}")