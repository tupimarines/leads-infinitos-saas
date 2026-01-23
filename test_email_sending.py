import sys
from unittest.mock import MagicMock

# Mock DB dependencies to run without Postgres
mock_pg = MagicMock()
sys.modules['psycopg2'] = mock_pg
sys.modules['psycopg2.extras'] = MagicMock()

from app import app, mail
from flask_mail import Message
import sys

def test_email():
    """Testa o envio de email usando a configuração do app"""
    with app.app_context():
        print(f"Configuração:")
        print(f"Server: {app.config['MAIL_SERVER']}")
        print(f"Port: {app.config['MAIL_PORT']}")
        print(f"Username: {app.config['MAIL_USERNAME']}")
        print(f"Sender: {app.config['MAIL_DEFAULT_SENDER']}")
        
        recipient = app.config['MAIL_USERNAME'] # Enviar para si mesmo
        
        print(f"\nTentando enviar email para {recipient}...")
        
        try:
            msg = Message(
                'Teste de SMTP - Leads Infinitos',
                recipients=[recipient]
            )
            msg.body = "Este é um email de teste para verificar a configuração SMTP."
            msg.html = "<h1>Teste Bem Sucedido!</h1><p>A configuração SMTP está funcionando.</p>"
            
            mail.send(msg)
            print("✅ Email enviado com sucesso!")
            return True
        except Exception as e:
            print(f"❌ Erro ao enviar email: {e}")
            return False

if __name__ == "__main__":
    success = test_email()
    sys.exit(0 if success else 1)
