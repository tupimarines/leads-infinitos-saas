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
        Trata como sucesso: 200, 404, ou erro cujo body indique instância já deletada.
        """
        url = f"{self.base_url}/instance"
        headers = {"token": token}

        try:
            response = requests.delete(url, headers=headers, timeout=15)
            if response.status_code in (200, 404):
                return True, response.status_code
            # Instância já deletada na Uazapi? Tratar como sucesso para remover do nosso DB
            body_lower = (response.text or "").lower()
            if any(
                x in body_lower
                for x in ("not found", "não encontrad", "deleted", "deletada", "404", "instance not found")
            ):
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

    def send_media(
        self,
        token: str,
        number: str,
        media_type: str,
        file: str,
        caption: str = "",
    ) -> Optional[dict[str, Any]]:
        """
        Envia mídia (imagem ou vídeo) via POST /send/media.
        number: formato 5511999999999 (sem @s.whatsapp.net).
        media_type: 'image' ou 'video'.
        file: path local (será convertido para base64), URL ou string base64.
        caption: legenda opcional.
        """
        import base64

        url = f"{self.base_url}/send/media"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }

        file_value = file
        if not file.startswith(("http://", "https://", "data:")):
            # Path local: ler e converter para base64
            if os.path.isfile(file):
                with open(file, "rb") as f:
                    data = f.read()
                b64 = base64.b64encode(data).decode("utf-8")
                ext = os.path.splitext(file)[1].lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".mp4": "video/mp4",
                    ".webm": "video/webm",
                }
                mime = mime_map.get(ext, "application/octet-stream")
                file_value = f"data:{mime};base64,{b64}"
            else:
                print(f"❌ [Uazapi] send_media: arquivo não encontrado: {file}")
                return None

        payload: dict[str, Any] = {
            "number": number,
            "type": media_type,
            "file": file_value,
            "text": caption or "",
        }

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=30
            )
            if response.status_code != 200:
                print(f"❌ [Uazapi] send_media Status: {response.status_code}")
                print(f"❌ [Uazapi] send_media Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error sending media: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def check_phone(
        self, token: str, numbers: list[str], timeout: int = 15
    ) -> Optional[list[dict[str, Any]]]:
        """
        Verifica números via POST /chat/check.
        numbers: lista de números sem @s.whatsapp.net (ex: ["5511999999999"]).
        Retorna array com objetos contendo isInWhatsapp (camelCase).
        timeout: segundos (default 15; validate-leads usa 90 para batches grandes).
        """
        url = f"{self.base_url}/chat/check"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload = {"numbers": numbers}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=timeout
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

    # --- Métodos de campanha (envio em massa avançado) ---

    def create_advanced_campaign(
        self,
        token: str,
        delay_min_sec: int,
        delay_max_sec: int,
        messages: list[dict[str, Any]],
        info: Optional[str] = None,
        scheduled_for: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Cria campanha de envio em massa via POST /sender/advanced.
        messages: array de {number, type, text} (number sem @s.whatsapp.net).
        Retorna dict com folder_id, count, status.
        """
        url = f"{self.base_url}/sender/advanced"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "delayMin": delay_min_sec,
            "delayMax": delay_max_sec,
            "messages": messages,
        }
        if info is not None:
            payload["info"] = info
        if scheduled_for is not None:
            payload["scheduled_for"] = scheduled_for

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=30
            )
            if response.status_code != 200:
                print(
                    f"❌ [Uazapi] create_advanced_campaign Status: {response.status_code}"
                )
                print(f"❌ [Uazapi] create_advanced_campaign Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error creating advanced campaign: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def edit_campaign(
        self, token: str, folder_id: str, action: str
    ) -> Optional[dict[str, Any]]:
        """
        Controla campanha via POST /sender/edit.
        action: "stop" | "continue" | "delete".
        Retorna dict com status.
        """
        if action not in ("stop", "continue", "delete"):
            print(f"❌ [Uazapi] edit_campaign invalid action: {action}")
            return None
        url = f"{self.base_url}/sender/edit"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload = {"folder_id": folder_id, "action": action}

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(
                    f"❌ [Uazapi] edit_campaign Status: {response.status_code}"
                )
                print(f"❌ [Uazapi] edit_campaign Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error editing campaign: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def list_folders(
        self, token: str, status: Optional[str] = None
    ) -> Optional[list[dict[str, Any]]]:
        """
        Lista campanhas via GET /sender/listfolders.
        status: "Active" | "Archived" (opcional).
        Retorna array de pastas/campanhas.
        """
        url = f"{self.base_url}/sender/listfolders"
        headers = {"token": token}
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status

        try:
            response = requests.get(
                url, headers=headers, params=params or None, timeout=15
            )
            if response.status_code != 200:
                print(
                    f"❌ [Uazapi] list_folders Status: {response.status_code}"
                )
                print(f"❌ [Uazapi] list_folders Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error listing folders: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None

    def list_messages(
        self,
        token: str,
        folder_id: str,
        message_status: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Lista mensagens de campanha via POST /sender/listmessages.
        message_status: "Scheduled" | "Sent" | "Failed" (opcional).
        Retorna dict com messages (array) e pagination.
        """
        url = f"{self.base_url}/sender/listmessages"
        headers = {
            "token": token,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"folder_id": folder_id}
        if message_status is not None:
            payload["messageStatus"] = message_status
        if page is not None:
            payload["page"] = page
        if page_size is not None:
            payload["pageSize"] = page_size

        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=15
            )
            if response.status_code != 200:
                print(
                    f"❌ [Uazapi] list_messages Status: {response.status_code}"
                )
                print(f"❌ [Uazapi] list_messages Body: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ [Uazapi] Error listing messages: {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"❌ [Uazapi] Response: {e.response.text}")
            return None
