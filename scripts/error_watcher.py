"""
This script provides a wrapper to save screenshots of errors.
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
        Initialize the ErrorWatcher singleton instance.
        This method should be called once before using the ErrorWatcher.
        It can take the following keyword arguments:
        - root_dir: The root directory for saving screenshots (default is current working directory).
        - screenshot_dir: The directory where screenshots will be saved (default is 'screenshots' in the root directory).
        - driver: The driver instance used for taking screenshots (default is None).
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
        Decorator to wrap a function and catch exceptions.
        If an error occurs, it will take a screenshot.

        Usage:
        1. @ErrorWatcher.watch
        2. @ErrorWatcher.watch(driver=my_driver)
        3. @ErrorWatcher.watch(error_type=ValueError)
        """

        def decorator(f):
            @functools.wraps(f)
            def wrapped(*args, **kwargs):
                instance = cls.instance()
                return instance._watch_impl(f, *args, **kwargs, **kwargs)
            return wrapped
        
        if func is not None:
            # If the function is provided directly, return the wrapped function
            return decorator(func)
        else:
            # If no function is provided, return the decorator
            return decorator

    def set_driver(self, driver):
        """
        Set the driver for taking screenshots.
        """
        self.driver = driver
    
    def watch_this(self, func, **options):
        """
        Decorator to wrap a function and catch exceptions.
        """
        error_type = options.get('error_type', Exception)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except error_type as e:
                self.__handle_error(e, options)
                raise
        return wrapper
                

    # private methods below

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
        error is not used now, may be used in the future.
        """
        driver = options.get('driver', self.driver)
        if not driver:
            logging.error("No driver set for taking screenshots.")
            return

        error_message = str(error)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        screenshot_path = os.path.join(self.screenshot_dir, f'error_{timestamp}.png')
        
        try:
            self.driver.save_screenshot(screenshot_path)
            logging.error(f"Error occurred: {error_message}. Screenshot saved to {screenshot_path}")
        except Exception as e:
            logging.error(f"Failed to save screenshot: {e}")
            # do not raise the exception here to avoid masking the original error
        finally:
            pass

