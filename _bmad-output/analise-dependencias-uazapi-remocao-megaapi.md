# Análise de Dependências: Campanhas Uazapi vs Remoção MegaAPI

**Objetivo:** Garantir que a remoção do MegaAPI não quebre nenhuma dependência das campanhas Uazapi.

---

## 1. Fluxo das Campanhas Uazapi (o que DEVE continuar funcionando)

### 1.1 Criação de campanha (app.py)
- `use_uazapi_sender=true` → `UazapiService.create_advanced_campaign`
- Instâncias: `COALESCE(api_provider, 'megaapi') = 'uazapi'`
- **Não usa:** WhatsappService, MEGA_API_*

### 1.2 Envio inicial
- Campanhas Uazapi são enviadas **no servidor Uazapi** (create_advanced_campaign envia para a API)
- **worker_sender NÃO processa** campanhas com `use_uazapi_sender=true` (query exclui na linha 768)

### 1.3 Sync de status (worker_sender)
- `_sync_uazapi_usage` (linhas 700–740) roda a cada 10 min
- Usa: `uazapi_service`, `sync_campaign_leads_from_uazapi`
- Chama: `sync_campaign_leads_from_uazapi(conn, campaign_id, apikey, uazapi_folder_id, uazapi_service)`
- **Depende de:** UazapiService, utils/sync_uazapi.py
- **NÃO depende de:** MEGA_API_*, WhatsappService, send_message, check_phone

### 1.4 Rotas QR / Status / Delete (app.py)
- Quando `api_provider == 'uazapi'`: usa `UazapiService` (connect, get_status, delete_instance)
- **NÃO usa:** WhatsappService (só no branch `api_provider == 'megaapi'`)

### 1.5 Pause/Start/Delete campanha (app.py)
- `_uazapi_control_campaign` usa `UazapiService.edit_campaign`
- **NÃO usa:** WhatsappService

### 1.6 Follow-up cadência (worker_cadence)
- `process_campaign_sends`: quando `api_provider == 'uazapi'` usa `uazapi_service.send_text` e `uazapi_service.send_media`
- Rollover: `sync_campaign_leads_from_uazapi`, `get_uazapi_campaign_counts`, `fetch_all_phones_by_status`
- Criação de campanhas follow-up: `uazapi_service.create_advanced_campaign`
- **NÃO usa:** send_text_message, send_media_message (MegaAPI) quando api_provider=uazapi

### 1.7 Stats e sync manual (app.py)
- `get_campaign_stats`: usa `uazapi.list_folders`, `uazapi.list_messages`
- `sync_uazapi_stats`: usa `sync_campaign_leads_from_uazapi`
- **NÃO usa:** WhatsappService

---

## 2. O que pode ser removido (sem afetar Uazapi)

| Componente | Usado por Uazapi? | Ação |
|------------|-------------------|------|
| **WhatsappService** | Não | Remover |
| **MEGA_API_URL, MEGA_API_TOKEN** | Não | Remover |
| **get_instance_status_api (branch MegaAPI)** | Não | Remover branch MegaAPI; manter Uazapi |
| **restart_instance_api** | Não (só MegaAPI) | Remover inteiro |
| **check_phone_on_whatsapp (branch MegaAPI)** | Não | Remover branch MegaAPI; manter Uazapi |
| **send_message (branch MegaAPI)** | Não | Remover branch MegaAPI; manter Uazapi |
| **send_text_message (worker_cadence)** | Não | Remover (só MegaAPI) |
| **send_media_message (worker_cadence)** | Não | Remover (só MegaAPI) |

---

## 3. O que NÃO pode ser removido (dependências Uazapi)

| Componente | Usado por | Manter |
|------------|-----------|--------|
| **UazapiService** (services/uazapi.py) | app.py, worker_sender, worker_cadence, utils | ✅ |
| **sync_campaign_leads_from_uazapi** (utils/sync_uazapi.py) | worker_sender, worker_cadence, app.py | ✅ |
| **get_uazapi_campaign_counts** | worker_cadence, app.py, utils/sync_uazapi | ✅ |
| **fetch_all_phones_by_status** | utils/sync_uazapi, worker_cadence | ✅ |
| **uazapi_service** (worker_sender) | _sync_uazapi_usage, send_message Uazapi path | ✅ |
| **uazapi_service** (worker_cadence) | process_campaign_sends, rollover, create_advanced_campaign | ✅ |
| **format_jid, jid_to_number** | worker_sender, worker_cadence (helpers compartilhados) | ✅ |
| **_is_media_path_safe** | worker_sender, worker_cadence | ✅ |
| **_sync_uazapi_usage** | worker_sender (sync periódico) | ✅ |

---

## 4. Código compartilhado (cuidado ao remover)

### worker_sender: funções com branch dual (MegaAPI + Uazapi)
- **get_instance_status_api**: branch Uazapi (111–123) — **manter**; branch MegaAPI (125–166) — remover
- **check_phone_on_whatsapp**: branch Uazapi (408–426) — **manter**; branch MegaAPI (428–533) — remover
- **send_message**: branch Uazapi (545–561) — **manter**; branch MegaAPI (563–602) — remover
- **verify_and_recover_instance**: chama get_instance_status_api e restart_instance_api. Para Uazapi não chama restart. Ao remover restart_instance_api, o branch MegaAPI de verify_and_recover_instance ficará quebrado — **remover o branch MegaAPI** (linhas 239–256) ou fazer `if api_provider != 'uazapi': return False`

### worker_cadence: process_campaign_sends
- Branch Uazapi (1620–1641): `uazapi_service.send_media`, `uazapi_service.send_text` — **manter**
- Branch MegaAPI (1633–1636, 1642–1643): `send_media_message`, `send_text_message` — **remover**
- Ao remover send_text_message e send_media_message, o branch `api_provider != 'uazapi'` precisa ser tratado: logar e pular, ou retornar erro.

---

## 5. Testes que precisam de ajuste

| Arquivo | Teste | Problema | Ação |
|---------|-------|----------|------|
| **test_sender_mock.py** | test_check_phone_exists | Mocka requests.get (MegaAPI) | Atualizar para mockar uazapi_service.check_phone ou passar api_provider='uazapi' |
| **test_sender_mock.py** | test_send_message | Mocka requests.post (MegaAPI) | Atualizar para mockar uazapi_service.send_text ou passar api_provider='uazapi' |
| **test_format_jid** | — | Usa worker_sender.format_jid | Sem mudança (helper genérico) |

---

## 6. Conclusão

**Seguro remover:**
- WhatsappService (app.py)
- MEGA_API_URL, MEGA_API_TOKEN
- Branches MegaAPI em get_instance_status_api, check_phone_on_whatsapp, send_message (worker_sender)
- restart_instance_api (worker_sender)
- send_text_message, send_media_message (worker_cadence)
- Branch MegaAPI em process_campaign_sends (worker_cadence)

**Manter intacto:**
- UazapiService, utils/sync_uazapi.py
- _sync_uazapi_usage e sua chamada
- Branches Uazapi em get_instance_status_api, check_phone_on_whatsapp, send_message (worker_sender)
- format_jid, jid_to_number, _is_media_path_safe
- Toda a lógica de campanhas use_uazapi_sender em app.py

**Ajustar:**
- verify_and_recover_instance: remover ou simplificar branch MegaAPI
- test_sender_mock.py: atualizar mocks para Uazapi
