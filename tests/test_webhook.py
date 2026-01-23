import requests
import json
import os

# Config
URL = "http://localhost:8000/api/webhooks/hotmart"
HOTTOK = "seu_hottok_aqui"  # Deve bater com o .env

# Payload
with open("tests/mock_hotmart_payload.json", "r") as f:
    payload = json.load(f)

# Headers
headers = {
    "Content-Type": "application/json",
    "X-Hotmart-Hottok": HOTTOK
}

try:
    print(f"Enviando POST para {URL}...")
    response = requests.post(URL, json=payload, headers=headers)
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
    
    if response.status_code == 200:
        print("\n✅ Webhook enviado com sucesso! Verifique o banco de dados.")
    else:
        print("\n❌ Falha ao enviar webhook.")

except Exception as e:
    print(f"\n❌ Erro de conexão: {e}")
    print("Certifique-se de que o servidor Flask está rodando (python app.py).")
