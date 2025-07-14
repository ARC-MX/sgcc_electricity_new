from webdrivermanager_cn import GeckodriverManagerAliMirror

driver_path = GeckodriverManagerAliMirror().install()
# 测试安装
if __name__ == "__main__":
    print(f"{driver_path}")