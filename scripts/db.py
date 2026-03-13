import logging
import os

import sqlite3
import mysql.connector

class DB:
    def connect_user_db(self, user_id):
        # Logic to connect to the user's database
        pass
    def insert_data(self, data:dict):
        # Logic to insert data into the database
        pass
    def insert_expand_data(self, data:dict):
        # Logic to insert expanded data into the database
        pass
    def close_connect(self):
        # close connect
        pass
    
class SqliteDB(DB):
    def connect_user_db(self, user_id):
        """创建数据库集合，db_name = electricity_daily_usage_{user_id}
        :param user_id: 用户ID"""
        try:
            # 创建数据库
            DB_NAME = os.getenv("DB_NAME", "homeassistant.db")
            if 'PYTHON_IN_DOCKER' in os.environ: 
                DB_NAME = "/data/" + DB_NAME
            self.connect = sqlite3.connect(DB_NAME)
            self.connect.cursor()
            logging.info(f"Database of {DB_NAME} created successfully.")
            # 创建表名
            self.table_name = f"daily{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS {self.table_name} (
                    date DATE PRIMARY KEY NOT NULL, 
                    usage REAL NOT NULL)'''
            self.connect.execute(sql)
            logging.info(f"Table {self.table_name} created successfully")
			
			# 创建data表名
            self.table_expand_name = f"data{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS {self.table_expand_name} (
                    name TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL)'''
            self.connect.execute(sql)
            logging.info(f"Table {self.table_expand_name} created successfully")
			
        # 如果表已存在，则不会创建
        except sqlite3.Error as e:
            logging.debug(f"Create db or Table error:{e}")
            return False
        return True
    
    def insert_data(self, data:dict):
        if self.connect is None:
            logging.error("Database connection is not established.")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_name} VALUES(strftime('%Y-%m-%d','{data['date']}'),{data['usage']});"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"Data update failed: {e}")
    
    def insert_expand_data(self, data:dict):
        if self.connect is None:
            logging.error("Database connection is not established.")
            return
        # 创建索引
        try:
            sql = f"INSERT OR REPLACE INTO {self.table_expand_name} VALUES('{data['name']}','{data['value']}');"
            self.connect.execute(sql)
            self.connect.commit()
        except BaseException as e:
            logging.debug(f"Data update failed: {e}")
    
    def close_connect(self):
        if self.connect:
            self.connect.close()
            self.connect = None
            logging.info("Database connection closed.")
            
class MysqlDB(DB):
    def connect_user_db(self, user_id):
        try:
            host = os.getenv("MYSQL_HOST")
            user = os.getenv("MYSQL_USER")
            password = os.getenv("MYSQL_PASSWORD")
            database = os.getenv("MYSQL_DATABASE")
            port = int(os.getenv("MYSQL_PORT", 3306))
            self.connect = mysql.connector.connect(
                host=host,
                user=user,
                password=password,
                database=database,
                port=port
            )

            if self.connect.is_connected():
                logging.info(f"connect mysql.")
                return self.create_tabe(user_id)
            else:
                logging.error("Failed to connect to MySQL database.")
                return False
        except BaseException as e:
            logging.error(f"Missing MySQL configuration: {e}")
            return False
    
    def create_tabe(self, user_id):
        try:
            cursor = self.connect.cursor()
            # 创建表名
            self.table_name = f"sg_daily_{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS `{self.table_name}` (
                    `date` DATE PRIMARY KEY NOT NULL, 
                    `usage` REAL NOT NULL)'''
            cursor.execute(sql)
            logging.info(f"Table {self.table_name} created successfully")
            
            # 创建data表名
            self.table_expand_name = f"sg_data_{user_id}"
            sql = f'''CREATE TABLE IF NOT EXISTS `{self.table_expand_name}` (
                    `name` varchar(100) PRIMARY KEY NOT NULL,
                    `value` TEXT NOT NULL)'''
            cursor.execute(sql)
            logging.info(f"Table {self.table_expand_name} created successfully")
            self.connect.commit()
        except BaseException as e:
            logging.error(f"Create Table error:{e}")
            return False
        finally:
            if cursor:
                cursor.close()
        return True

    def insert_data(self, data:dict):
        if self.connect is None:
            logging.error("Database connection is not established.")
            return
        try:
            cursor = self.connect.cursor()
            sql = f"REPLACE INTO `{self.table_name}` VALUES(str_to_date('{data['date']}', '%Y-%m-%d'),{data['usage']});"
            cursor.execute(sql)
            self.connect.commit()
            return True
        except BaseException as e:
            logging.error(f"Data update failed: {e}")
        finally:
            if cursor:
                cursor.close()
        return False

    def insert_expand_data(self, data:dict):
        if self.connect is None:
            logging.debug("Database connection is not established.")
            return
        try:
            cursor = self.connect.cursor()
            sql = f"REPLACE INTO `{self.table_expand_name}` VALUES('{data['name']}','{data['value']}');"
            cursor.execute(sql)
            self.connect.commit()
            return True
        except BaseException as e:
            logging.error(f"Data update failed: {e}")
        finally:
            if cursor:
                cursor.close()
        return False
    
    def close_connect(self):
        if self.connect and self.connect.is_connected():
            self.connect.close()
            self.connect = None
            logging.info("MySQL database connection closed.")