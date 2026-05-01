# n8n HTTP Request - Uazapi Campanha Simples + List Messages

Use seu token: `a7f9d434-c214-44b1-8c53-4c26ead90f97`

---

## Inventário: legado (pasta / `folder_id`) vs fila outbox (app)

Este ficheiro documenta chamadas **diretas ao Uazapi** que assumem **campanha em pasta** (`folder_id` devolvido por `/sender/simple` ou `/sender/advanced`). Trate tudo o que está abaixo como **modelo legado folder** — aplica-se quando a campanha no SaaS ainda passa por pastas remotas (ex.: `create_advanced_campaign`, chunks em `campaign_stage_sends` com `uazapi_folder_id`).

| O quê | Legado folder (este doc) | Outbox (fila Postgres no app) |
|--------|---------------------------|-------------------------------|
| Disparo em massa | `POST /sender/simple`, `POST /sender/advanced` | Não usa estas rotas para a campanha; o worker chama `POST /send/text` ou `POST /send/media` por mensagem |
| Correlacionar envio | `folder_id` + `listmessages` / `listfolders` | Estado em BD (`campaign_message_outbox`, `campaign_send_attempts`); UI/admin usa **polling** à API do próprio app (ex. `GET …/outbox-state`), não `folder_id` da pasta |
| n8n / jobs externos | Podem encadear: campanha → `folder_id` → listagens Uazapi | Se a campanha foi criada no ramo outbox (`USE_MESSAGE_OUTBOX` + fluxo admin), **não há** `folder_id` útil para esse encadeamento — o job precisa de **ramo alternativo** (ler app) ou ficar **desligado** para esse tipo de campanha, senão falha silenciosa |

**Quando aplicável cada nota neste ficheiro:** todas as secções numeradas (Campanha Simples, List Messages, List Folders, cURLs e fluxo sugerido) referem-se **apenas ao legado folder**. Para inventário de workflows em produção (lista de nós n8n que consomem `folder_id` / webhooks do app), ver sprint final da spec de campanhas; ao migrar campanhas para outbox, validar cada workflow linha a linha.

---

## 1. Campanha Simples (POST /sender/simple)

> **Legado folder:** resposta inclui `folder_id` para encadear com `listmessages` / `listfolders` (não aplicável ao caminho outbox).

**URL:** `https://neurix.uazapi.com/sender/simple`  
**Method:** POST  
**Headers:**
```
Accept: application/json
Content-Type: application/json
token: a7f9d434-c214-44b1-8c53-4c26ead90f97
```

**Body (JSON):**
```json
{
  "numbers": [
    "554137984981@s.whatsapp.net",
    "554137984019@s.whatsapp.net",
    "554137984966@s.whatsapp.net",
    "554137984741@s.whatsapp.net"
  ],
  "type": "text",
  "folder": "Campanha n8n",
  "delayMin": 1,
  "delayMax": 2,
  "scheduled_for": 1,
  "text": "Teste n8n - 4 números",
  "linkPreview": false
}
```

> **Nota:** Campanha simples envia a mesma mensagem para todos. Para mensagens diferentes por número, use campanha avançada (POST /sender/advanced).

**Resposta esperada:** `{"folder_id":"r...", "count":4, "status":"queued"}` — guarde o `folder_id` para o próximo request.

---

## 2. List Messages (POST /sender/listmessages)

> **Legado folder:** corpo e exemplos dependem de `folder_id` retornado pela campanha em pasta (secção 1 ou avançada). Campanhas **outbox** não alimentam este endpoint a partir do mesmo contrato de criação de campanha no app.

**URL:** `https://neurix.uazapi.com/sender/listmessages`  
**Method:** POST  
**Headers:**
```
Accept: application/json
Content-Type: application/json
token: a7f9d434-c214-44b1-8c53-4c26ead90f97
```

**Body (JSON) — use o `folder_id` retornado pela campanha:**
```json
{
  "folder_id": "{{ $json.folder_id }}",
  "messageStatus": "Sent",
  "page": 1,
  "pageSize": 50
}
```

**No n8n:** Se o Node 1 (Campanha Simples) retornar `folder_id`, use na expressão:
- `{{ $('Campanha Simples').item.json.folder_id }}` (ajuste o nome do node)

**Ou fixo para teste:**
```json
{
  "folder_id": "COLE_O_FOLDER_ID_AQUI",
  "messageStatus": "Sent",
  "page": 1,
  "pageSize": 50
}
```

**Status possíveis:** `Scheduled` | `Sent` | `Failed`

---

## 3. List Folders (GET /sender/listfolders) — contadores reais

> **Legado folder:** agrega métricas por pasta no Uazapi. **Outbox:** contagens operacionais e auditoria por tentativa ficam no Postgres do app; não substituir por só `listfolders` sem saber qual modelo a campanha usa.

**Melhor fonte para saber quantos enviaram:** `listfolders` retorna `log_sucess`, `log_total`, `log_failed` por folder — mais confiável que `list_messages` (que pode retornar só 1 item).

**URL:** `https://neurix.uazapi.com/sender/listfolders`  
**Method:** GET  
**Headers:**
```
Accept: application/json
token: a7f9d434-c214-44b1-8c53-4c26ead90f97
```

**Query (opcional):** `?status=Active` ou `?status=Archived`

**cURL:**
```bash
curl -X GET "https://neurix.uazapi.com/sender/listfolders?status=Active" \
  -H "Accept: application/json" \
  -H "token: a7f9d434-c214-44b1-8c53-4c26ead90f97"
```

**Exemplo de resposta:**
```json
[
  {
    "id": "r03d3044cabb430",
    "info": "",
    "status": "done",
    "log_delivered": 3,
    "log_failed": 0,
    "log_sucess": 4,
    "log_total": 4,
    "owner": "554195802989",
    "created": "2026-03-08T03:23:29.356Z",
    "updated": "2026-03-08T03:24:42.861Z"
  }
]
```

| Campo | Significado |
|-------|-------------|
| `log_sucess` | Mensagens enviadas com sucesso |
| `log_total` | Total de mensagens na campanha |
| `log_failed` | Falhas |
| `log_delivered` | Entregues |
| `status` | `scheduled` \| `done` \| `ativo` \| `paused` |

### Comparação: listfolders vs list_messages

| Endpoint | Contadores | Números por mensagem |
|----------|------------|------------------------|
| **listfolders** | ✅ `log_sucess`, `log_total` corretos (ex: 4/4) | ❌ Não retorna |
| **list_messages** | ❌ `totalRecords` pode ser 1 mesmo com 4 enviados | ✅ Retorna `chatid` por mensagem |

**Exemplo real (folder `re3e49beeabb2c3`, teste-avancada-4numeros):**
- **listfolders:** `log_sucess: 4`, `log_total: 4` ✅
- **list_messages:** `totalRecords: 1`, só 1 mensagem (chatid 554137984966) ❌

**Conclusão:** Use **listfolders** para contagens; **list_messages** tem limitação (retorna só 1 item por folder).

### list_messages com status=Failed

Retorna números que falharam, com campo `error` útil — mas **mesma limitação: só 1 item** mesmo com `log_failed > 1`.

**Exemplo (log_failed: 2, totalRecords: 1):**
```json
{
  "chatid": "554133630283@s.whatsapp.net",
  "status": "Failed",
  "error": "the number 554133630283@s.whatsapp.net is not on WhatsApp"
}
```

**Estratégia de sync:** Chamar `list_messages` com `messageStatus=Failed` e marcar no DB os números retornados como `failed`. Mesmo que só 1 de N falhas seja retornada, já evita reenviar esse número no Follow-up 1.

---

## Fluxo n8n sugerido

**Apenas legado folder:** válido quando o gatilho cria campanha no Uazapi com pasta e `folder_id`. Para campanhas outbox, desenhar fluxo paralelo contra a API admin do app (estado persistido), não esta cadeia só com token Uazapi.

1. **HTTP Request** (Campanha Simples ou Avançada) → salva output
2. **Wait** (15–30 segundos) → aguarda envio
3. **HTTP Request** (List Folders) → ver contadores `log_sucess`/`log_total` por folder  
   *ou* **List Messages** com `folder_id` do passo 1 → ver mensagens individuais

---

## cURL para teste manual

**Campanha:**
```bash
curl -X POST "https://neurix.uazapi.com/sender/simple" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "token: a7f9d434-c214-44b1-8c53-4c26ead90f97" \
  -d '{"numbers":["554137984981@s.whatsapp.net","554137984019@s.whatsapp.net","554137984966@s.whatsapp.net","554137984741@s.whatsapp.net"],"type":"text","folder":"Campanha n8n","delayMin":1,"delayMax":2,"scheduled_for":1,"text":"Teste n8n - 4 números","linkPreview":false}'
```

**List Messages (substitua FOLDER_ID):**
```bash
curl -X POST "https://neurix.uazapi.com/sender/listmessages" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "token: a7f9d434-c214-44b1-8c53-4c26ead90f97" \
  -d '{"folder_id":"FOLDER_ID","messageStatus":"Sent","page":1,"pageSize":50}'
```

**Campanha Avançada (mensagem diferente por número)** — *legado folder; não é o envio unitário outbox (`/send/text` / `/send/media` disparado pelo worker da app):*

```bash
curl -X POST "https://neurix.uazapi.com/sender/advanced" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "token: a7f9d434-c214-44b1-8c53-4c26ead90f97" \
  -d '{"delayMin":1,"delayMax":2,"info":"teste-avancada-4numeros","scheduled_for":1,"messages":[{"number":"554137984981","type":"text","text":"Teste Maria"},{"number":"554137984019","type":"text","text":"Teste Ana"},{"number":"554137984966","type":"text","text":"Teste João"},{"number":"554137984741","type":"text","text":"Teste Pedro"}]}'
```
