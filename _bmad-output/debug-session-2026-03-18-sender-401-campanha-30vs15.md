# Debug Session 2026-03-18: Erros 401 Uazapi e Campanha 30 vs 15 envios

## Resumo

- **Erros 401**: Token Uazapi inválido/expirado nas instâncias. O sync chama `list_folders`/`list_messages` e recebe `{"code":401,"message":"Invalid token."}`.
- **30 vs 15 envios**: Campanha 140/141 Uazapi — possível interrupção, números inválidos ou distribuição entre instâncias. Sem restrição WhatsApp.

---

## 1. Erros 401 no log do Sender

### Origem

Os erros `[Uazapi] list_messages Status: 401` e `[Uazapi] list_folders Status: 401` vêm do **worker_sender**, que executa `_sync_uazapi_usage()` a cada 10 minutos.

Fluxo:

1. `worker_sender.process_campaigns()` chama `_sync_uazapi_usage(conn)` a cada 10 min.
2. `_sync_uazapi_usage` busca campanhas com `use_uazapi_sender=true` e chama `sync_campaign_leads_from_uazapi()` para cada uma.
3. `sync_campaign_leads_from_uazapi` usa `campaign_stage_sends` e, para cada send, chama:
   - `uazapi.list_folders(send_token)`
   - `uazapi.list_messages(send_token, folder_id, ...)`
4. O token vem de `instances.apikey` (por instância). Se o token estiver inválido/expirado, a API retorna 401.

### Causa

O `apikey` de uma ou mais instâncias Uazapi está inválido ou expirado. Possíveis motivos:

- Token revogado ou regenerado no painel Uazapi/Neurix.
- Instância desvinculada ou recriada.
- Token incorreto no banco.

### Ação recomendada

1. Conferir no painel Uazapi/Neurix quais instâncias (70, 71, 72) estão ativas e com token válido.
2. Atualizar o `apikey` em `instances` para cada instância Uazapi usada.
3. Verificar se o token está no formato esperado (ex.: UUID).

---

## 2. Campanha 30 vs 15 envios

### Contexto

- Logs do web mostram **campaign_id=141** criada com 30 leads e 3 instâncias (70, 71, 72).
- Logs do sender mostram **campaign_id=90** (MegaAPI) — campanha diferente.
- Campanha 140/141 é Uazapi e roda no servidor Uazapi, não no worker_sender.

### Distribuição de leads (campanha 141)

- `per_instance_limit = 30`
- `total_limit = 30 * 3 = 90`
- Com 30 leads totais: `lead_chunks = [[leads 1–30]]` → apenas 1 chunk.
- Resultado: só a **instância 70** recebe leads; 71 e 72 ficam sem leads.
- Total enviado para a Uazapi: 30 mensagens (todas pela instância 70).

### Possíveis motivos para só 15 envios

1. **Números inválidos**: números não cadastrados no WhatsApp são marcados como failed pela Uazapi; isso não explica envios parciais, mas pode reduzir o total efetivo.
2. **Interrupção no servidor Uazapi**: campanha pausada, erro interno ou timeout.
3. **Redeploy do container**: o envio é feito no servidor Uazapi; redeploy do app não deveria afetar, mas pode haver outro componente envolvido.
4. **Limite ou rate limit interno da Uazapi**: possível, mas você indicou que não houve restrição do WhatsApp.

### Como verificar

1. **list_folders** na Uazapi para a campanha 141:
   - Conferir `log_sucess`, `log_failed`, `log_total` por folder.
2. **list_messages** com `messageStatus=Sent` e `messageStatus=Failed`:
   - Ver quantas mensagens foram enviadas e quantas falharam.
3. **Banco de dados**:
   - `SELECT status, COUNT(*) FROM campaign_leads WHERE campaign_id = 141 GROUP BY status;`
   - Conferir quantos `sent` vs `failed` vs `pending`.

---

## 3. Melhorias implementadas

- Logs de erro Uazapi passam a incluir `campaign_id` e `instance_id` para facilitar o diagnóstico de 401 e outros erros.
