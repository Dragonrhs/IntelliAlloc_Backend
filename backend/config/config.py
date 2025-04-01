from dotenv import load_dotenv
import os

def load_config():
    load_dotenv()  # Carrega variáveis do .env
    return {
        'DB_CONFIG': {
            'host': os.getenv('DB_HOST'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'database': os.getenv('DB_NAME'),
            'port': int(os.getenv('DB_PORT'))
        },
        'EMAIL_CONFIG': {
            'smtp_server': os.getenv('SMTP_SERVER'),
            'smtp_port': int(os.getenv('SMTP_PORT')),
            'sender_email': os.getenv('SENDER_EMAIL'),
            'sender_password': os.getenv('SENDER_PASSWORD')
        },
        'SECRET_KEY': os.getenv('SECRET_KEY')
    }