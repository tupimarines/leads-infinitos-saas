#!/usr/bin/env python3
"""
Script para configurar email para testes locais
"""

import os

def setup_email_config():
    """Configura variáveis de ambiente para email"""
    print("=" * 60)
    print("CONFIGURAÇÃO DE EMAIL PARA TESTES")
    print("=" * 60)
    
    print("\nPara que o sistema de reset de senha funcione, você precisa:")
    print("1. Ter uma conta Gmail")
    print("2. Ativar autenticação de 2 fatores")
    print("3. Gerar uma senha de app")
    
    print("\n📧 Configuração do Gmail:")
    print("1. Acesse: https://myaccount.google.com/security")
    print("2. Ative 'Verificação em duas etapas'")
    print("3. Vá em 'Senhas de app'")
    print("4. Gere uma senha para 'Email'")
    print("5. Use essa senha no campo MAIL_PASSWORD")
    
    print("\n🔧 Configuração no código:")
    print("Edite o arquivo app.py e altere as linhas:")
    print("app.config['MAIL_USERNAME'] = 'seu-email@gmail.com'")
    print("app.config['MAIL_PASSWORD'] = 'sua-senha-de-app'")
    
    print("\n⚠️  IMPORTANTE:")
    print("- NÃO use sua senha normal do Gmail")
    print("- Use APENAS a senha de app gerada")
    print("- Para testes locais, você pode usar um email fake")
    
    print("\n🧪 Para testes locais (sem envio real):")
    print("O sistema funcionará mesmo sem email configurado")
    print("A senha temporária será gerada e mostrada no console")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    setup_email_config()
