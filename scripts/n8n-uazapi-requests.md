# n8n HTTP Request - Uazapi Campanha Simples + List Messages

Use seu token: `a7f9d434-c214-44b1-8c53-4c26ead90f97`

---

## 1. Campanha Simples (POST /sender/simple)

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

**Campanha Avançada (mensagem diferente por número):**
```bash
curl -X POST "https://neurix.uazapi.com/sender/advanced" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "token: a7f9d434-c214-44b1-8c53-4c26ead90f97" \
  -d '{"delayMin":1,"delayMax":2,"info":"teste-avancada-4numeros","scheduled_for":1,"messages":[{"number":"554137984981","type":"text","text":"Teste Maria"},{"number":"554137984019","type":"text","text":"Teste Ana"},{"number":"554137984966","type":"text","text":"Teste João"},{"number":"554137984741","type":"text","text":"Teste Pedro"}]}'
```
