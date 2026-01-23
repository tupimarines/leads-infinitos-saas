import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Configuração simples sem depender do contexto total do Flask para o worker
# Isso evita problemas de circular import e complexidade desnecessária
SMTP_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('MAIL_PORT', 587))
SMTP_USERNAME = os.environ.get('MAIL_USERNAME')
SMTP_PASSWORD = os.environ.get('MAIL_PASSWORD')
SMTP_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', SMTP_USERNAME)

def send_email_task(recipient, subject, html_body):
    """
    Função Worker para enviar email via SMTP de forma assíncrona (via Redis Queue)
    """
    print(f"[{datetime.now()}] Iniciando envio de email para {recipient}...")
    
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("Erro: Credenciais de email não configuradas no worker.")
        return False

    try:
        # Configurar mensagem
        msg = MIMEMultipart()
        msg['From'] = SMTP_SENDER
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html'))

        # Conectar e enviar
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_SENDER, recipient, text)
        server.quit()
        
        print(f"[{datetime.now()}] Email enviado com sucesso para {recipient}")
        return True
        
    except Exception as e:
        print(f"[{datetime.now()}] Erro critico ao enviar email: {str(e)}")
        return False
