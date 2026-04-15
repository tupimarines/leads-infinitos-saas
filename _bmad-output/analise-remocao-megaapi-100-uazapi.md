# Análise: Remoção do MegaAPI e Migração 100% Uazapi

## Estado Atual

### Já migrado para Uazapi
| Componente | Status |
|------------|--------|
| **init_whatsapp** (app.py) | Criação de instâncias sempre via Uazapi |
| **admin_create_user** | Cria instâncias via Uazapi |
| **Superadmin** | Instâncias MegaAPI removidas (migração) |
| **Campanhas iniciais** | `use_uazapi_sender=true` → envio via Uazapi (create_advanced_campaign) |
| **Pause/Start/Delete** | Campanhas Uazapi usam edit_campaign na API |
| **QR, Status, Delete** | Rotas verificam `api_provider`; Uazapi usa UazapiService |

### Ainda usa MegaAPI
| Componente | Uso |
|------------|-----|
| **worker_sender** | Campanhas com `use_uazapi_sender=false` — envia via MegaAPI |
| **worker_cadence** | Follow-ups de campanhas com instância MegaAPI — send_text_message, send_media_message |
| **WhatsappService** (app.py) | QR, status, delete quando `api_provider='megaapi'` |
| **Formulário de campanha** | Checkbox `use_uazapi_sender` — permite criar campanhas MegaAPI |

---

## O que precisa mudar para 100% Uazapi

### 1. Forçar Uazapi em novas campanhas

**Arquivo:** `templates/campaigns_new.html`, `app.py`

- **Opção A:** Checkbox `use_uazapi_sender` sempre marcado e oculto (default true, não editável)
- **Opção B:** Remover checkbox; backend sempre usa `use_uazapi_sender=true`
- **Validação:** Exigir pelo menos uma instância Uazapi ao criar campanha (já existe)

### 2. worker_sender — simplificar ou desativar envio MegaAPI

**Arquivo:** `worker_sender.py`

- Query atual: `use_uazapi_sender IS NULL OR use_uazapi_sender = false` → só processa MegaAPI
- Se todas as campanhas forem Uazapi, essa query retorna vazio e o worker não envia nada
- **Manter:** `_sync_uazapi_usage` (sync de campanhas Uazapi a cada 10 min)
- **Remover:** Lógica de envio MegaAPI (send_message, check_phone MegaAPI, get_instance_status MegaAPI, restart MegaAPI)
- **Variáveis:** `MEGA_API_URL`, `MEGA_API_TOKEN` — podem ser removidas do worker_sender

### 3. worker_cadence — remover path MegaAPI

**Arquivo:** `worker_cadence.py`

- `get_campaign_instance` já prioriza Uazapi
- `process_campaign_sends` usa `send_text_message`/`send_media_message` (MegaAPI) quando `api_provider != 'uazapi'`
- **Ação:** Remover `send_text_message` e `send_media_message`; usar apenas Uazapi
- **Nota:** Campanhas com cadência e `use_uazapi_sender=true` já usam Uazapi no worker_cadence. O path MegaAPI serve para campanhas antigas com instância MegaAPI.

### 4. WhatsappService — remover ou manter como fallback

**Arquivo:** `app.py`

- Usado em: `get_whatsapp_qr`, `get_whatsapp_status`, `delete_whatsapp_instance` quando `api_provider != 'uazapi'`
- **Se não houver instâncias MegaAPI:** O branch MegaAPI nunca é executado
- **Ação:** Migrar/remover instâncias MegaAPI do banco; depois remover WhatsappService e branches MegaAPI das rotas

### 5. Instâncias MegaAPI existentes

**Banco:** `instances` com `api_provider='megaapi'` ou `NULL`

- Migração já removeu MegaAPI do superadmin
- **Verificar:** `SELECT COUNT(*) FROM instances WHERE COALESCE(api_provider, 'megaapi') = 'megaapi';`
- **Se > 0:** Decidir: migrar usuários para Uazapi ou avisar que precisam criar novas instâncias

### 6. Campanhas existentes com use_uazapi_sender=false

**Banco:** `campaigns` com `use_uazapi_sender=false` ou `NULL`

- Essas campanhas dependem do worker_sender (MegaAPI)
- **Se remover MegaAPI:** Ficam órfãs (não enviam mais)
- **Ações possíveis:**
  - Migrar em massa: `UPDATE campaigns SET use_uazapi_sender=true` onde há instância Uazapi vinculada (complexo — exige recriar folder na Uazapi)
  - Ou: manter worker_sender por um período de transição até campanhas antigas terminarem
  - Ou: marcar como "legacy" e não processar (campanhas paradas)

---

## Compatibilidade necessária

### Pré-requisitos para remoção completa

1. **Nenhuma instância MegaAPI ativa**
   ```sql
   SELECT id, name, user_id FROM instances 
   WHERE COALESCE(api_provider, 'megaapi') = 'megaapi';
   ```

2. **Nenhuma campanha ativa MegaAPI**
   ```sql
   SELECT id, name, status FROM campaigns 
   WHERE (use_uazapi_sender IS NULL OR use_uazapi_sender = false)
   AND status IN ('pending', 'running');
   ```

3. **UAZAPI_FOR_ALL_USERS_ENABLED=true** (ou remover o check — já que será 100% Uazapi)

### Ordem sugerida de implementação

| Fase | Ação | Risco |
|------|------|-------|
| 1 | Verificar se há instâncias/campanhas MegaAPI ativas | — |
| 2 | Forçar `use_uazapi_sender=true` em novas campanhas (checkbox default + oculto ou removido) | Baixo |
| 3 | Migrar/remover instâncias MegaAPI restantes | Médio |
| 4 | Remover lógica MegaAPI do worker_sender (manter só sync Uazapi) | Baixo |
| 5 | Remover send_text_message/send_media_message do worker_cadence; usar só Uazapi | Médio |
| 6 | Remover WhatsappService e branches MegaAPI das rotas QR/status/delete | Baixo |
| 7 | Remover MEGA_API_* do docker-compose (opcional) | Baixo |

---

## Arquivos a modificar (resumo)

| Arquivo | Alterações |
|---------|------------|
| `templates/campaigns_new.html` | use_uazapi_sender sempre true (checkbox oculto ou removido) |
| `app.py` | Validação: só aceitar instâncias Uazapi; remover WhatsappService e branches MegaAPI |
| `worker_sender.py` | Remover envio MegaAPI; manter _sync_uazapi_usage; simplificar query (ou remover loop de envio) |
| `worker_cadence.py` | Remover send_text_message, send_media_message; usar só Uazapi |
| `docker-compose*.yml` | Opcional: remover MEGA_API_URL, MEGA_API_TOKEN |
| `utils/validate_job_csv.py` | Já filtra Uazapi; sem mudança |
| `migrate_bootstrap_cadence.py` | Se ainda usado: migrar para Uazapi ou marcar como deprecated |

---

## Conclusão

**É viável** remover o MegaAPI e usar apenas Uazapi, desde que:

1. Não existam instâncias MegaAPI em uso
2. Não existam campanhas ativas dependentes do worker MegaAPI
3. O formulário de campanha passe a criar apenas campanhas Uazapi
4. O worker_cadence passe a enviar follow-ups apenas via Uazapi

O worker_sender pode ser mantido apenas para o sync Uazapi (`_sync_uazapi_usage`), e o loop de envio MegaAPI pode ser removido ou deixado "morto" (query vazia).
