"""
UazapiService - Integração com Uazapi para WhatsApp.

Usado pelo superadmin para criar instâncias, conectar, verificar status,
deletar e enviar mensagens. URL base via UAZAPI_URL; admintoken via UAZAPI_ADMIN_TOKEN.
"""

import os
from typing import Any, Optional, Tuple

import requests


class UazapiService:
    """Cliente para API Uazapi (WhatsApp)."""

    def __init__(self) -> None:
        self.base_url = os.environ.get(
            "UAZAPI_URL", "https://neurix.uazapi.com"
        ).rstrip("/")
        self.admin_token = os.environ.get("UAZAPI_ADMIN_TOKEN", "")

    def create_instance(self, name: str) -> Optional[dict[str, Any]]:
        """
        Cria nova instância via POST /instance/init.
        Usa header admintoken.
        Retorna dict com token (response.get('token') or response.get('instance', {}).get('token')).
        """
        url = f"{self.base_url}/instance/init"
        headers = {
            "admintoken": self.admin_token,
            "Content-Type": "application/json",
        }
        payload = {"name": name}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(f"❌ [Uazapi] create_instance Status: {response.status_code}")
                print(f"❌ [Uazapi] create_instance Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error creating instance: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def connect(self, token: str) -> Optional[dict[str, Any]]:
        """
        Inicia conexão via POST /instance/connect.
        Usa header token. Payload vazio para gerar QR code.
        Retorna instance com qrcode (base64) se em processo de conexão.
        """
        url = f"{self.base_url}/instance/connect"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload = {}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(f"❌ [Uazapi] connect Status: {response.status_code}")
                print(f"❌ [Uazapi] connect Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error connecting: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def get_status(self, token: str) -> Optional[dict[str, Any]]:
        """
        Obtém status via GET /instance/status.
        Retorna instance.status: connected, connecting ou disconnected.
        """
        url = f"{self.base_url}/instance/status"
        headers = {"token": token}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"❌ [Uazapi] get_status Status: {response.status_code}")
                print(f"❌ [Uazapi] get_status Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error getting status: {e}")
            return None

    def delete_instance(self, token: str) -> Tuple[bool, Optional[int]]:
        """
        Deleta instância via DELETE /instance.
        Retorna (success, status_code).
        Se API retornar 404 (instância já deletada), tratar como sucesso.
        """
        url = f"{self.base_url}/instance"
        headers = {"token": token}

        try:
            response = requests.delete(url, headers=headers, timeout=15)
            if response.status_code in (200, 404):
                return True, response.status_code
            print(f"❌ [Uazapi] delete_instance Status: {response.status_code}")
            print(f"❌ [Uazapi] delete_instance Body: {response.text}")
            return False, response.status_code
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error deleting instance: {e}")
            if hasattr(e, "response") and e.response is not None:
                return False, e.response.status_code if e.response else None
            return False, None

    def send_text(
        self, token: str, number: str, text: str
    ) -> Optional[dict[str, Any]]:
        """
        Envia mensagem de texto via POST /send/text.
        number: formato 5511999999999 (sem @s.whatsapp.net).
        """
        url = f"{self.base_url}/send/text"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload = {"number": number, "text": text}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(f"❌ [Uazapi] send_text Status: {response.status_code}")
                print(f"❌ [Uazapi] send_text Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error sending text: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def check_phone(
        self, token: str, numbers: list[str]
    ) -> Optional[list[dict[str, Any]]]:
        """
        Verifica números via POST /chat/check.
        numbers: lista de números sem @s.whatsapp.net (ex: ["5511999999999"]).
        Retorna array com objetos contendo isInWhatsapp (camelCase).
        """
        url = f"{self.base_url}/chat/check"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload = {"numbers": numbers}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(f"❌ [Uazapi] check_phone Status: {response.status_code}")
                print(f"❌ [Uazapi] check_phone Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error checking phone: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None
