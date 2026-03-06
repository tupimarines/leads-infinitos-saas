# Problem Solving Session: Debug envio de mensagens e logs

**Date:** 2026-03-06
**Problem Solver:** Augusto
**Problem Category:** Debug / Observabilidade

---

## 🎯 PROBLEM DEFINITION

### Initial Problem Statement

O envio de mensagens funciona corretamente e a campanha pausa quando solicitado. Porém:
1. O card de campanhas **não atualiza o quantitativo de envios** (enviados, pendentes, progresso)
2. O log "⏰ Fora do horário de envio (21:38 BRT). Aguardando janela configurada..." **sobrepõe** o log de mensagem enviada com payload nos logs do container sender

### Refined Problem Statement

**Problema A — Card não atualiza quantitativo:** O frontend (campaigns_list.html) faz polling em `/api/campaigns/<id>/stats` a cada 5s para campanhas `running`. O card exibe `sent`, `pending`, `failed`, `progress`, `success_rate`. Mesmo com envios ocorrendo, esses valores permanecem desatualizados (ex.: 0 enviados, 23 pendentes).

**Problema B — Logs sobrepostos:** O worker_sender imprime "⏰ Fora do horário de envio..." a cada 60s quando não há campanhas na janela de envio. Esse log polui o stdout e dificulta visualizar os logs de envio real (payload, resposta, status).

### Problem Context

- **Arquitetura:** worker_sender.py processa campanhas `use_uazapi_sender=false`; campanhas `use_uazapi_sender=true` são enviadas pela API Uazapi (worker não processa)
- **Stats:** Para campanhas worker → `campaign_leads` (status sent/pending/failed). Para campanhas Uazapi → API `list_messages` (Sent/Failed/Scheduled)
- **Logs:** worker_sender usa `print()`; Docker com `PYTHONUNBUFFERED=1`; stdout vai para logs do container

### Success Criteria

1. Card de campanhas exibe quantitativo correto (enviados, pendentes) em tempo real durante envio
2. Logs do container sender permitem filtrar/identificar facilmente eventos de envio sem poluição do log "Fora do horário"
3. Estrutura de debug clara para Quick Dev aplicar correções

---

## 🔍 DIAGNOSIS AND ROOT CAUSE ANALYSIS

### Problem Boundaries (Is/Is Not)

| Onde ocorre | Onde NÃO ocorre |
|-------------|------------------|
| Card na listagem de campanhas (/campaigns) | Kanban, admin, dashboard geral |
| Logs do container leads_infinitos_sender | Logs do app web, cadence |
| Campanhas running (envio em andamento) | Campanhas pausadas (stats podem atualizar no load) |

| O que É o problema | O que NÃO é |
|--------------------|-------------|
| Stats não refletem envios em tempo real | Envio em si (funciona) |
| Log "Fora do horário" polui stdout | Falha de envio ou pausa |

### Root Cause Analysis (Five Whys)

**Problema A — Card não atualiza:**

1. **Por que o card mostra 0 enviados?** → A API `/api/campaigns/<id>/stats` retorna `sent=0`
2. **Por que a API retorna 0?** → Duas hipóteses: (a) `campaign_leads` não tem status='sent' atualizado, ou (b) para Uazapi, `list_messages` retorna 0 ou falha
3. **Por que campaign_leads não teria sent?** → Se campanha é `use_uazapi_sender=true`, o worker NUNCA atualiza campaign_leads — a Uazapi envia remotamente
4. **Por que list_messages retornaria 0?** → API pode falhar silenciosamente, paginação diferente, ou folder_id/token incorretos
5. **Root cause A:** Para campanhas Uazapi, a fonte de verdade (Uazapi API) pode não estar sendo consultada corretamente ou retorna vazio. Para campanhas worker, o UPDATE em campaign_leads pode não estar commitando ou há condição de corrida.

**Problema B — Logs sobrepostos:**

1. **Por que "Fora do horário" aparece?** → `campaigns` fica vazio após filtro `is_campaign_in_send_window`
2. **Por que campaigns fica vazio?** → (a) Todas as campanhas ativas são Uazapi (excluídas da query), ou (b) horário atual fora da janela (ex.: 21:38 fora de 8h–20h)
3. **Por que imprimir a cada 60s?** → `time.sleep(60)` no loop quando `not campaigns`
4. **Root cause B:** O log é informativo mas excessivamente frequente e sem prefixo filtrável. Mistura-se com logs de envio quando há envios (em janelas alternadas) e polui a saída.

### Contributing Factors

- **Uazapi vs Worker:** Dois fluxos distintos (worker atualiza DB; Uazapi não). Stats precisam de lógica diferente.
- **Polling 5s:** Pode ser que a primeira carga já venha errada; ou o backend retorna cache/erro.
- **Log único:** Não há prefixo `[ENVIO]` nem `[HORARIO]` para filtrar — todos os prints vão para o mesmo stdout.

### System Dynamics

```
[Worker Sender] → busca campanhas use_uazapi_sender=false
                → filtra por is_campaign_in_send_window
                → se vazio: print "Fora do horário" + sleep 60
                → se não vazio: loop envio → send_message → UPDATE campaign_leads

[Uazapi]       → envia via API remota; campaign_leads NUNCA atualizado

[API /stats]   → worker: SELECT campaign_leads (sent, pending, failed)
                → Uazapi: list_messages(Sent), list_messages(Failed), list_messages(Scheduled)
                → retorna JSON para frontend

[Frontend]     → polling 5s para status=running → updateCampaignStats()
```

---

## 📊 ANALYSIS

### Force Field Analysis

**Driving Forces:**
- Envio já funciona; pausa funciona
- Tech-spec de logs [ENVIO] já existe (tech-spec-logs-sender-envios.md)
- Código modular (worker_sender, app.py stats) permite correções pontuais

**Restraining Forces:**
- Dois fluxos (worker vs Uazapi) aumentam complexidade de debug
- Falta de logs estruturados dificulta diagnóstico

### Constraint Identification

- **Primary constraint:** Fonte de verdade para stats em campanhas Uazapi é a API Uazapi; se falhar ou retornar 0, o card fica desatualizado
- **Secondary:** Log "Fora do horário" não tem controle de verbosidade (ex.: imprimir 1x a cada 5 min em vez de 1x/min)

### Key Insights

1. **Campanha Uazapi:** Se a campanha "teste-imob" usa `use_uazapi_sender=true`, o worker nunca a processa. O worker imprime "Fora do horário" porque a query exclui campanhas Uazapi. Os envios acontecem na Uazapi; os stats vêm de `list_messages`.
2. **Campanha Worker:** Se `use_uazapi_sender=false`, o worker atualiza `campaign_leads`. O card deveria atualizar em até 5s. Se não atualiza, verificar: (a) commit no UPDATE, (b) polling no frontend, (c) data-status do card.
3. **Logs:** Reduzir frequência do "Fora do horário" e adicionar prefixos filtráveis resolve a poluição.

---

## 💡 SOLUTION GENERATION

### Methods Used

- **Is/Is Not Analysis** — delimitar escopo
- **Five Whys** — root cause
- **Constraint Identification** — identificar gargalo

### Generated Solutions

| # | Solução | Descrição |
|---|---------|-----------|
| 1 | Prefixo e throttling no log "Fora do horário" | Usar `[HORARIO]` e imprimir no máximo 1x a cada 5 min (cooldown) |
| 2 | Implementar tech-spec [ENVIO] | Aplicar tech-spec-logs-sender-envios.md para logs de envio identificáveis |
| 3 | Fallback stats Uazapi | Se `list_messages` retornar 0 para todos, logar warning e considerar manter DB ou retry |
| 4 | Verificar UPDATE campaign_leads | Garantir `conn.commit()` após UPDATE no worker_sender |
| 5 | Debug endpoint /api/campaigns/<id>/stats/debug | Retornar fonte (db vs uazapi), raw counts, erros da Uazapi |
| 6 | Polling mais agressivo para running | Reduzir intervalo de 5s para 3s em campanhas running |
| 7 | Log nível DEBUG para "Fora do horário" | Só imprimir se `DEBUG_SENDER=1` |

### Creative Alternatives

- **WebSocket para stats:** Push em tempo real em vez de polling (maior esforço)
- **Log separado:** Escrever logs de envio em arquivo dedicado (ex.: `/var/log/sender-envios.log`) — fora do escopo do tech-spec atual

---

## ⚖️ SOLUTION EVALUATION

### Evaluation Criteria

| Critério | Peso | Sol 1 | Sol 2 | Sol 3 | Sol 4 | Sol 5 |
|----------|------|-------|-------|-------|-------|-------|
| Efetividade | Alto | Média | Alta | Média | Alta | Alta |
| Esforço | Médio | Baixo | Médio | Baixo | Baixo | Médio |
| Risco | Baixo | Baixo | Baixo | Baixo | Baixo | Baixo |

### Solution Analysis

- **Solução 1 (throttling log):** Resolve poluição imediatamente; esforço mínimo
- **Solução 2 ([ENVIO]):** Já especificada; alta prioridade
- **Solução 3 (fallback Uazapi):** Útil se API falha; pode mascarar problema real
- **Solução 4 (commit):** Verificação de sanity; provavelmente já está correto
- **Solução 5 (debug endpoint):** Ajuda diagnóstico; não resolve diretamente

### Recommended Solution

**Pacote de correções para Quick Dev:**

1. **Log "Fora do horário"** — prefixo `[HORARIO]` + throttling (1x a cada 5 min)
2. **Logs [ENVIO]** — implementar tech-spec-logs-sender-envios.md
3. **Stats Uazapi** — adicionar log de warning quando `list_messages` retorna 0 para campanha running com folder_id
4. **Endpoint debug** (opcional) — `/api/campaigns/<id>/stats?debug=1` retorna `source`, `uazapi_raw`, `uazapi_error`

### Rationale

Prioriza correções de baixo esforço e alto impacto. O throttling + prefixo resolve a poluição de logs. O tech-spec [ENVIO] já está pronto. O warning em stats Uazapi ajuda a identificar se o problema é na API. O endpoint debug é opcional para diagnóstico futuro.

---

## 🚀 IMPLEMENTATION PLAN

### Implementation Approach

Abordagem incremental: (1) logs primeiro (throttling + [ENVIO]), (2) depois stats/warning. Quick Dev pode aplicar em uma única sessão.

### Action Steps

#### Task 1: Throttling e prefixo no log "Fora do horário" ✅
- **Arquivo:** `worker_sender.py`
- **Ação:** 
  - Adicionar variável `last_horario_log = None` (ou dict com timestamp)
  - Antes do `print("⏰ Fora do horário...")`, verificar: se `last_horario_log` existe e `(now - last_horario_log) < 300` (5 min), fazer `continue` sem imprimir
  - Alterar print para: `print(f"[HORARIO] Fora do horário de envio ({now_brazil.strftime('%H:%M')} BRT). Aguardando janela...", flush=True)`
  - Atualizar `last_horario_log = now` após imprimir

#### Task 2: Implementar tech-spec [ENVIO] ✅
- **Arquivo:** `worker_sender.py`
- **Referência:** `_bmad-output/implementation-artifacts/tech-spec-logs-sender-envios.md`
- **Ação:** Seguir Tasks 1–6 do tech-spec (LOG_PREFIX, log_envio, INICIANDO/OK/FALHA, flush)

#### Task 3: Warning quando stats Uazapi retornam 0 ✅
- **Arquivo:** `app.py`, função `get_campaign_stats`
- **Ação:** Após bloco Uazapi, se `campaign.status == 'running'` e `uazapi_sent == 0 and uazapi_failed == 0 and uazapi_scheduled == 0` e `total_leads > 0`, adicionar: `print(f"⚠️ [Stats] Campanha {campaign_id} Uazapi: list_messages retornou 0 para todos os status. Verificar API/token.")`

#### Task 4 (opcional): Endpoint stats com debug ✅
- **Arquivo:** `app.py`
- **Ação:** Se `request.args.get('debug') == '1'`, incluir no JSON de retorno: `"debug": {"source": "uazapi"|"db", "uazapi_sent": X, "uazapi_failed": Y, "uazapi_error": "..."}`

### Timeline and Milestones

- Milestone 1: Tasks 1 e 2 (logs) — prioridade máxima
- Milestone 2: Task 3 (warning stats)
- Milestone 3: Task 4 (opcional)

### Resource Requirements

- Acesso a `worker_sender.py`, `app.py`
- Tech-spec `tech-spec-logs-sender-envios.md`

### Responsible Parties

- Quick Dev (Barry) — implementação

---

## 📈 MONITORING AND VALIDATION

### Success Metrics

- Logs do container: `grep "[ENVIO]"` retorna apenas linhas de envio; `grep "[HORARIO]"` retorna no máximo 1 linha a cada 5 min
- Card: durante envio ativo, `sent` e `pending` atualizam em até 5s

### Validation Plan

1. Criar campanha worker (use_uazapi_sender=false), iniciar envio, observar card em tempo real
2. Criar campanha Uazapi (use_uazapi_sender=true), iniciar envio, observar card; verificar logs do app por warning
3. Fora do horário: verificar que "[HORARIO]" aparece no máximo 1x a cada 5 min

### Risk Mitigation

- Throttling: garantir que não mascara problema real (ex.: se não há campanhas por 10 min, ainda assim deve logar 2x)
- Stats Uazapi: warning não deve quebrar resposta da API

### Adjustment Triggers

- Se card ainda não atualizar após implementação: investigar polling, data-status, e resposta da API /stats
- Se list_messages falhar consistentemente: considerar webhook ou sync periódico Uazapi → DB

---

## 📝 ESTRUTURA DE DEBUG PARA QUICK DEV

### Checklist de diagnóstico (antes de implementar)

- [ ] Confirmar se a campanha usa `use_uazapi_sender=true` ou `false` (query: `SELECT id, name, use_uazapi_sender, uazapi_folder_id FROM campaigns WHERE name = 'teste-imob'`)
- [ ] Se Uazapi: verificar se `list_messages` retorna dados (testar manualmente ou via curl)
- [ ] Se worker: verificar se `campaign_leads` tem `status='sent'` após envio (query: `SELECT status, COUNT(*) FROM campaign_leads WHERE campaign_id = X GROUP BY status`)
- [ ] Verificar se o card tem `data-status="running"` no HTML (inspecionar elemento)
- [ ] Verificar se o polling está ativo (DevTools Network: requisições a `/api/campaigns/<id>/stats` a cada 5s)

### Arquivos a modificar

| Arquivo | Alterações |
|---------|------------|
| `worker_sender.py` | Throttling log, prefixo [HORARIO], tech-spec [ENVIO] |
| `app.py` | Warning stats Uazapi, opcional debug |

### Ordem de implementação

1. Task 1 (throttling + [HORARIO]) — rápido, impacto imediato
2. Task 2 (tech-spec [ENVIO]) — seguir spec
3. Task 3 (warning) — 1 linha
4. Task 4 (debug) — opcional

---

_Generated using BMAD Creative Intelligence Suite - Problem Solving Workflow_
