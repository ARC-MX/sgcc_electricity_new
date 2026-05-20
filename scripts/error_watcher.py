"""
本脚本提供错误截图保存的封装功能。
"""

import os
import logging
import functools
from datetime import datetime
from typing import Callable, Optional

class ErrorWatcher:

    @classmethod
    def init(cls, **kwargs):
        """
        初始化 ErrorWatcher 单例实例。
        在使用 ErrorWatcher 之前应先调用此方法。
        可接受以下关键字参数：
        - root_dir: 保存截图的根目录（默认为当前工作目录）。
        - screenshot_dir: 截图保存的目录（默认为根目录下的 'screenshots' 目录）。
        - driver: 用于截图的驱动实例（默认为 None）。
        """
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def instance(cls):
        if cls._instance is None:
            raise ValueError("ErrorWatcher has not been initialized. Call init() first.")
        return cls._instance

    @classmethod
    def watch(cls, func: Optional[Callable] = None, **options) -> Callable:
        """
        装饰器，用于包装函数并捕获异常。
        如果发生错误，将截取屏幕截图。

        用法：
        1. @ErrorWatcher.watch
        2. @ErrorWatcher.watch(driver=my_driver)
        3. @ErrorWatcher.watch(error_type=ValueError)
        """

        def decorator(f):
            @functools.wraps(f)
            def wrapped(*args, **kwargs):
                instance = cls.instance()
                return instance._watch_impl(f, *args, **kwargs)
            return wrapped

        if func is not None:
            # 如果直接传入了函数，则返回包装后的函数
            return decorator(func)
        else:
            # 如果没有传入函数，则返回装饰器
            return decorator

    def set_driver(self, driver):
        """
        设置用于截图的驱动。
        """
        self.driver = driver

    def watch_this(self, func, **options):
        """
        装饰器，用于包装函数并捕获异常。
        """
        error_type = options.get('error_type', Exception)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_type as e:
                self.__handle_error(e, options)
                raise
        return wrapper


    # 以下为私有方法

    def __init__(self, **kwargs):
        self.root_dir = kwargs.get('root_dir', os.getcwd())
        self.screenshot_dir = kwargs.get('screenshot_dir', os.path.join(self.root_dir, 'screenshots'))
        if not os.path.exists(self.screenshot_dir):
            os.makedirs(self.screenshot_dir)
        self.driver = kwargs.get('driver', None)

    _instance = None

    def _watch_impl(self, func, *args, **options):
        error_type = options.get('error_type', Exception)
        try:
            return func(*args, **options)
        except error_type as e:
            self.__handle_error(e, **options)
            raise e

    def __handle_error(self, error, **options):
        """
        error 参数当前未使用，保留以备将来使用。
        """
        driver = options.get('driver', self.driver)
        if not driver:
            logging.error("未设置截图驱动。")
            return

        error_message = str(error)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        screenshot_path = os.path.join(self.screenshot_dir, f'error_{timestamp}.png')

        try:
            self.driver.save_screenshot(screenshot_path)
            logging.error(f"发生错误: {error_message}。截图已保存至 {screenshot_path}")
        except Exception as e:
            logging.error(f"保存截图失败: {e}")
            # 此处不抛出异常，避免掩盖原始错误
        finally:
            pass
