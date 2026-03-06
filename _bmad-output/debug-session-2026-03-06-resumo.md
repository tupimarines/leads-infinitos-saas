# Resumo da sessão de debug — envio de mensagens e logs

**Data:** 2026-03-06  
**Projeto:** leads-infinitos-saas

---

## Contexto inicial

- **Problema 1:** Card de campanhas não atualiza quantitativo (enviados, pendentes, progresso)
- **Problema 2:** Log "Fora do horário de envio" poluía stdout e sobrepunha logs de envio
- **Problema 3:** Campanhas Uazapi — sem visibilidade de payload/logs como MegaAPI

---

## O que foi implementado

### 1. worker_sender.py
- **Prefixo [HORARIO] + throttling 5 min** — log "Fora do horário" no máximo 1x a cada 5 min
- **Prefixo [ENVIO]** — logs estruturados: INICIANDO, OK, FALHA (campaign_id, lead_id, phone)
- **MegaAPI verbose** — condicionada a `DEBUG_SENDER=1`
- **Tech-spec aplicada:** `_bmad-output/implementation-artifacts/tech-spec-logs-sender-envios.md`

### 2. app.py
- **Throttling warning stats Uazapi** — 1x a cada 5 min por campanha (evita spam no polling)
- **Endpoint debug:** `GET /api/campaigns/<id>/stats?debug=1` — retorna `source`, `uazapi_folder_id`, `uazapi_sent`, `uazapi_failed`, `uazapi_scheduled`, `_raw_*` quando zerados
- **POST /api/login** — login via JSON para n8n/curl: `{"email":"...","password":"..."}`
- **Logs [UAZAPI]** — ao criar campanha: payload summary, OK/falha com folder_id; `DEBUG_SENDER` mostra amostra das 3 primeiras mensagens

---

## Fluxo de campanhas

| Tipo | use_uazapi_sender | Quem envia | Onde aparecem logs |
|------|-------------------|------------|---------------------|
| **MegaAPI** | false | worker_sender | Container sender — `[ENVIO]` |
| **Uazapi** | true | API Uazapi (remoto) | Container web — `[UAZAPI]` (criação) |

---

## Como debugar

### cURL (login + stats)
```bash
# 1. Login
curl.exe -k -X POST "https://leads.app.neurix.com.br/api/login" \
  -H "Content-Type: application/json" -c cookies.txt \
  -d '{"email":"SEU_EMAIL","password":"SUA_SENHA"}'

# 2. Stats com debug
curl.exe -k -X GET "https://leads.app.neurix.com.br/api/campaigns/93/stats?debug=1" -b cookies.txt
```

### Logs por container
```bash
# Sender (MegaAPI, campanhas worker)
docker logs leads_infinitos_sender 2>&1 | grep "\[ENVIO\]"
docker logs leads_infinitos_sender 2>&1 | grep "\[HORARIO\]"

# Web (Uazapi, criação de campanha)
docker logs leads_infinitos_web 2>&1 | grep "\[UAZAPI\]"
```

### Debug stats
- `?debug=1` retorna: `source`, `campaign_status`, `uazapi_folder_id`, `uazapi_sent`, `uazapi_failed`, `uazapi_scheduled`
- Quando todos zerados: `_raw_sent`, `_raw_failed`, `_raw_scheduled` (resposta bruta da API)

---

## Arquivos modificados (commits)

- `worker_sender.py` — logs [ENVIO]/[HORARIO], throttling
- `app.py` — API login, throttling warning, debug stats, logs [UAZAPI]
- `_bmad-output/problem-solution-2026-03-06.md`
- `_bmad-output/implementation-artifacts/tech-spec-logs-sender-envios.md`

---

## Próximos passos (se necessário)

1. **list_messages retorna uazapi_sent=0** — campanha pausada pode não listar Sent; verificar com campanha running
2. **Sync periódico Uazapi → DB** — se list_messages continuar inconsistente, job que atualiza campaign_leads a partir da API
3. **Envio um a um** — só se for essencial ter logs por mensagem no worker (perde batch/delays da Uazapi)

---

## Documentos de referência

- `_bmad-output/problem-solution-2026-03-06.md` — análise completa
- `_bmad-output/implementation-artifacts/tech-spec-logs-sender-envios.md` — spec de logs [ENVIO]
- `_bmad-output/implementation-artifacts/tech-spec-campanhas-uazapi-api.md` — spec Uazapi
