import mysql.connector
from mysql.connector import Error
from config.config import load_config

config = load_config()
DB_CONFIG = config['DB_CONFIG']

def get_db_connection():
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"Erro ao conectar ao MySQL: {e}")
        return None