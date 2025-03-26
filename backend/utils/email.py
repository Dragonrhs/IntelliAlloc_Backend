import smtplib
from email.mime.text import MIMEText
from config.config import load_config

config = load_config()
EMAIL_CONFIG = config['EMAIL_CONFIG']

def send_verification_email(email, token):
    subject = "Código de Redefinição de Senha"
    body = f"Seu código de verificação é: {token}\nEste código expira em 10 minutos."
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_CONFIG['sender_email']
    msg['To'] = email

    try:
        with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
            server.starttls()
            server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False