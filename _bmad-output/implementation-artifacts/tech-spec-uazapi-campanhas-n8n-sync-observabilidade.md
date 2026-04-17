---
title: 'UAZAPI: observabilidade de campanhas (n8n) e estratégia de sync para contadores'
slug: 'uazapi-campanhas-n8n-sync-observabilidade'
created: '2026-04-15'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4, 6, 7]
tech_stack: ['n8n', 'HTTP', 'UAZAPI', 'Python/services/uazapi.py', 'PostgreSQL', 'worker_cadence / RQ']
files_to_modify:
  - 'worker_cadence.py'
  - 'utils/limits.py'
  - 'utils/sync_uazapi.py'
  - 'services/uazapi.py'
  - 'app.py'
code_patterns:
  - 'POST /sender/advanced → folder_id em campaign_stage_sends'
  - 'GET /sender/listfolders como única fonte de agregados (polling 10 min)'
  - 'sync_campaign_leads_from_uazapi: loop por campaign_stage_sends + list_folders por send'
  - 'schedule_next_initial_chunk: skip se já existe initial scheduled|running|partial na instância'
test_patterns: ['pytest tests/test_sync_uazapi.py', 'pytest tests/test_worker_cadence_initial_chunk.py']
---

# Tech-Spec: UAZAPI: observabilidade de campanhas (n8n) e estratégia de sync para contadores

**Created:** 2026-04-15 · **Finalized:** 2026-04-15 · **Status:** ready-for-dev (quick-spec passo 4)

## Overview

### Problem Statement

Campanhas UAZAPI em blocos (`campaign_stage_sends`) podem ficar **running** com `folder_id` que **não existe** em `GET /sender/listfolders` nem em `POST /sender/listmessages` (ex.: Plano 22, `r34e1a8dbf463bc`). É necessário (1) **fonte de verdade** para contadores sem sobrecarregar a API, (2) **hipóteses e verificação** de por que a pasta “sumiu” após o `advanced`, (3) garantir que o **volume diário da campanha** se mantém quando o envio é **picotado** em vários chunks por delays/instâncias.

### Solution

1. **Métricas de campanha:** usar **apenas `GET /sender/listfolders`** por `folder_id` (agregados `log_sucess`, `log_total`, `log_failed`). **Polling a cada 10 minutos** por pasta ativa — já alinhado com `STAGE_SYNC_INTERVAL_MINUTES` e com o filtro `last_sync_at` em `_sync_active_stage_folders` no `worker_cadence.py`. **Não** usar `listmessages` como contador global (sem paginação útil; `totalRecords` pode ficar em ~10).

2. **Diagnóstico n8n:** manter `scripts/n8n-workflows/uazapi-diagnostico-campanha-advanced.json` para reproduzir I/O da API.

3. **Folder órfão (implementado em `utils/sync_uazapi.py`):** se a pasta **não** aparece em `listfolders` e `list_messages(Scheduled, page=1)` devolve **`None`** (ex. 400 *folder not found*), o `campaign_stage_sends` passa a **`failed`** com contagens 0, **sem** alterar `campaign_leads`, libertando a instância para o próximo chunk em `schedule_next_initial_chunk`. Telemetria / coluna `last_error` ficam como melhoria opcional.

### Scope

**In scope**

- Decisão de produto: **listfolders + 10 min** documentada e consistente com o worker.
- Análise de causa do folder **criado (resposta `advanced`) mas não listado** + risco de **bloqueio de chunk** no código.
- Comportamento de **persistência do total diário** em múltiplos chunks (criação inicial + `schedule_next_initial_chunk` + limites por instância).
- Tarefas e ACs para implementação da fase “órfão + continuidade”.

**Out of scope (esta spec)**

- SSE/webhooks UAZAPI (pode ser fase posterior).
- Alteração do limite de 10 registos no `listmessages` (depende do provedor).

---

## Decisões travadas (produto / ops)

| Decisão | Detalhe |
|--------|---------|
| Fonte de contagem | **`GET /sender/listfolders`** por pasta (`log_sucess`, `log_failed`, `log_total`, `status`). |
| Intervalo | **10 minutos** entre syncs por send (já: `STAGE_SYNC_INTERVAL_MINUTES = 10` e `INTERVAL '10 minutes'` no SQL de `_sync_active_stage_folders`). |
| `listmessages` | **Não** é SSOT para totais. Quando a pasta **não** está em `listfolders`: só **1** chamada `listmessages(Scheduled, page=1)` para detetar órfã (`None` → failed); **sem** batelada Sent/Failed/Scheduled em loop (removido para poupar o servidor). Com pasta em `listfolders`, `listmessages` ainda pode ser usado em ramos `needs_reconcile` / pente fino; avaliar redução futura. |

### Single source of truth (clarificação)

- **SSOT para agregados** (`success_count` / progresso de chunk alinhado à API): **`listfolders`** + polling 10 min.
- **`listmessages` não foi removido** do `sync_uazapi.py`: quando a pasta **consta** em `listfolders`, ainda entra em `needs_reconcile` (pasta `done` com lacunas), `fetch_all_phones_by_status` / `get_uazapi_campaign_counts`, e no pente fino **`message_find`** (`UAZAPI_MESSAGE_FIND`). **Totais oficiais** não devem depender de `totalRecords` do `listmessages`.

### Órfão vs falha transitória (distinção explícita)

| Situação | `listfolders` contém `folder_id`? | `listmessages(Scheduled, p1)` | Ação do sync (comportamento alvo) |
|----------|-------------------------------------|-------------------------------|-----------------------------------|
| **Normal** | Sim | irrelevante para decisão | Agregados via `listfolders`; ramos `needs_reconcile` / `message_find` conforme hoje. |
| **Atraso / indexação** | Não (ainda) | Resposta JSON 200 (mesmo vazio) | **Não** é órfão: só `last_sync_at` no send; próximo ciclo (~**10 min**) + verify **~3 min** pós-create. |
| **Órfão real** | Não | **`None`** (erro API, ex. 400 *folder not found*) | `status = failed`, libertar instância; leads **pending** inalterados. |

**Importante:** `list_folders` a devolver **`None`** por **timeout/5xx/401** **não** é tratado como “pasta ausente”: o sync **adianta** (`last_sync_at`) **sem** chamar o probe nem marcar `failed` (**Task 7**). Lista **vazia** HTTP 200 `[]` continua a permitir o ramo “ausente em listfolders” + probe mínimo.

---

## Advanced Elicitation — melhorias incorporadas (Pre-mortem, FMEA, First Principles, ADR)

### Pre-mortem (o que pode correr mal e mitigação)

| Risco futuro | Mitigação na spec / backlog |
|--------------|----------------------------|
| **Falso órfão** (API instável só em `listfolders`) | **Task 7 (feito):** `list_folders` → `None` não corre probe nem `failed`; só `last_sync_at`. Órfão real: resposta lista utilizável **e** pasta ausente **e** probe `None`. |
| **Leads “perdidos”** após `failed` | Confirmar em QA que `campaign_leads` permanecem `pending` e o próximo `materialize` / chunk reatribui; Task 3. |
| **UI atrasada** após corte do fallback pesado | **Latência aceite:** até **10 min** + janela **verify ~3 min** para alinhar com `listfolders`; documentado para suporte/UX. |
| **Suporte sem contexto** | Task 4 / Task 5: log estruturado ou coluna `last_error` (ex. `orphan_listmessages_probe_null`). |

### Failure Mode Analysis (resumo)

| Componente | Falha | Efeito | Mitigação |
|------------|-------|--------|-----------|
| `GET listfolders` | Timeout / `None` / `[]` | Sem `folder_info` | Task 7; retries operacionais. |
| Probe `listmessages` | Sempre `None` (bug) | Marca `failed` em massa | Testes + monitorização. |
| Sem loop Sent/Failed quando fora da lista | Pasta lista-se tarde | Só `last_sync_at` até aparecer | Aceite com polling 10 min. |
| `needs_reconcile` + `list_messages` | Carga residual | Picos API | Backlog: env `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0`. |
| `message_find` | Rate limit | Sync lento | Já existe `UAZAPI_MESSAGE_FIND`; documentar trade-off. |
| `schedule_next_initial_chunk` | Race duplicado | Dois sends | Query `LIMIT 1` active send — manter invariante na spec. |

### First Principles (síntese)

- O produto precisa de **contagem fiável** e de **não bloquear** a instância com estado fantasma → **`listfolders`** como SSOT de agregados.  
- O fornecedor **não** oferece `listmessages` completo → não usar como total oficial.  
- **Probe mínimo** + corte de batelada quando fora da lista **respeita** as duas restrições sem negar deteção de órfão real.

### ADR (Architecture Decision Record — registo curto)

| Decisão | Opções consideradas | Escolha | Racional |
|---------|---------------------|---------|----------|
| SSOT de contagem | `listmessages` vs `listfolders` | **`listfolders`** | Agregados oficiais; `listmessages` incompleto. |
| Pasta ausente na lista | Fallback pesado vs tempo vs probe | **Probe + `last_sync`**; `failed` se probe `None` | Menos carga; órfão libertado. |
| Estado terminal órfão | `failed` vs `orphan` | **`failed`** (atual) | Sem migração de enum em BD. |
| Observabilidade | — | Log / futuro `last_error` | Suporte e pre-mortem. |

**Consequência aceite:** possível **atraso de até ~10 min** (e verify ~3 min) para o Kanban refletir pasta que a API ainda não listou; distinto de órfão (probe erro).

### Backlog pós-elicitação

- **Task 7 (feito):** Diferenciar **`list_folders` com erro (`None`)** vs **lista utilizável sem a pasta** antes do probe de órfão (evitar falso `failed`).  
- **Task 8 (opcional):** Coluna `last_error` ou JSON em `campaign_stage_sends` para motivo (`orphan_probe`, `listfolders_timeout`, …).  
- **Task 9 (opcional):** Variável de ambiente `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` para desligar ramos `needs_reconcile` que ainda chamam `list_messages` em massa (avaliar impacto em campanhas `done` com lacunas).

### Elicitação — ronda 2 (Challenge, War room, Lições) — aplicada 2026-04-15

#### Challenge from Critical Perspective (síntese)

| Crítica | Risco | Mitigação na spec / backlog |
|--------|-------|------------------------------|
| Só um probe `Scheduled 1×1` | Falso **OK** (200 vazio com pasta órfã) deixa instância presa outra vez. | Monitorizar; se houver casos, avaliar **segundo probe** opcional `Sent 1×1` antes de `failed` (documentar como follow-up técnico, não obrigatório). |
| `listfolders` a falhar por rede | Confundir com “pasta ausente”. | **Task 7** + **AC5** implementados (`None` → só `last_sync_at`). |
| `failed` sem motivo estruturado | Suporte não distingue órfão de outras falhas. | **Task 8** (`last_error` / JSON). |
| Leads `pending` após chunk `failed` | Reenvio duplicado fora do app. | Documentar: **só** o próximo chunk/materialização do próprio fluxo deve reenviar; idempotência na UAZAPI é com o fornecedor. |
| Menos `listmessages` na ausência de pasta | Pasta `done` com lacunas vs `planned` reconcilia mais devagar. | Trade-off aceite; **Task 9** se quiserem cortar ainda mais carga nos ramos `needs_reconcile`. |

#### Cross-Functional War Room (trade-offs)

| Voz | Prioridade | Tensão |
|-----|------------|--------|
| **PM** | Progresso previsível no Kanban | Atraso até ~**10 min** + verify **~3 min** após mudanças na API. |
| **Eng** | Menos 429 / menos `failed` falsos | **Task 7** aumenta lógica no `sync_uazapi.py`. |
| **UX / Suporte** | Estados legíveis (“órfão”, “à espera da API”) | Até **Task 8**, usar logs + `failed` genérico. |

**Conclusão da ronda:** manter **SSOT `listfolders` + probe mínimo**; priorizar **Task 7** (fiabilidade) e **Task 8** (clareza) antes de expandir probes ou voltar à batelada `listmessages`.

#### Lessons learned (acionáveis)

1. Documentar internamente cedo: **`listfolders` = contagem**; **`listmessages` ≠ total** (teto ~10) — evita debates longos sobre “paginação”.  
2. **Workflow n8n** com payloads reais acelera conversas com o fornecedor (pasta inexistente vs token vs timing).  
3. **Não misturar** tique único no WhatsApp com métricas API ao analisar discrepâncias.  
4. **`failed` no send sem alterar `campaign_leads`** mantém o **teto diário picotado** coerente — referência para onboarding de engenharia.  
5. **Antes de mais automação** de `failed`, fechar **Task 7** para não gerar incidentes por falha transitória de `listfolders`.

**Follow-up técnico (opcional):** segundo probe (`Sent`, 1×1) só se métricas mostrarem falsos negativos no probe `Scheduled`; ligar a **Task 8** para codificar o motivo no registo (`orphan_probe_scheduled_ok_sent_fail`, etc.).

---

## Root cause analysis: pasta “criada” (Plano 22) mas não encontrada

**Evidência:** com o token da instância que criou o chunk, `listfolders` **não** inclui `r34e1a8dbf463bc`; `listmessages` devolve **400** *folder not found or access denied*.

**Hipóteses (por ordem de verificação prática)**

1. **Ciclo de vida no UAZAPI** — A API devolveu `folder_id` no `POST /sender/advanced`, mas a pasta foi **removida ou nunca indexada** na listagem (delete assíncrono, TTL, falha interna da fila, limpeza). O nosso DB ficou com o id **válido na altura da resposta HTTP**, inválido **depois** na consulta GET. *Ação:* pedir ao provedor **correlação server-side** para esse `folder_id` + horário do create.

2. **Estado órfão + UX “running”** — O registo `campaign_stage_sends` mantém `running` com `success_count` baixo enquanto a API já não tem pasta. O sync em `utils/sync_uazapi.py` usa **probe** `list_messages(Scheduled, 1×1)`: se **`None`**, marca **`failed`** e liberta a instância; se responde 200, só atualiza **`last_sync_at`** (sem batelada Sent/Failed) até o próximo `listfolders`.

3. **Outro token / instância** na consulta manual — descartado no teu teste controlado; manter checklist em suporte.

4. **`POST /sender/edit` delete** noutro fluxo (admin, retry, bug) — auditar logs da conta UAZAPI.

**Não é explicado por:** “paginação em falta” no `listfolders` — o id ausente é **ausência global** da pasta, não segunda página.

**Observação (Campanha 187 / Plano 22):** relatório de que **não houve envios** nesse chunk — em tese a pasta deveria permanecer **`queued`/`scheduled`** na UAZAPI até haver envio ou cancelamento. O facto de `listfolders` + `listmessages` negarem a pasta sugere **remoção ou falha interna** do lado do provedor (ou `delete` noutro canal), não um estado “normal” de fila; convém cruzar com suporte UAZAPI com timestamp do `POST /sender/advanced`.

---

## Persistência do volume diário picotado (3–4 chunks)

**Criação inicial** (`_create_campaign_core` em `app.py`):

- `total_limit = daily_limit` ao buscar leads pendentes (`LIMIT total_limit`).
- `per_instance_limit = min(30, ceil(daily_limit / n_inst))` com `n_inst = len(allowed_instances)`.
- Chunks: `_chunk(leads, per_instance_limit)`; loop `for idx, chunk in enumerate(lead_chunks): if idx >= len(allowed_instances): break` — na **primeira onda** só há **uma pasta por instância** elegível naquele momento.
- Leads que não entraram num chunk da primeira onda permanecem **`pending`** (cadência) ou dependem do fluxo sem cadência.

**Continuação** (`schedule_next_initial_chunk` em `worker_cadence.py`):

- Com leads `pending` + `current_step = 1`, insere novas linhas `campaign_stage_sends` com `status = 'scheduled'` para materialização posterior — isto **espalha o mesmo teto diário** (`campaigns.daily_limit` + distribuição por instância) ao longo do tempo e janelas. **Não** confundir com `can_create_campaign_today` (sempre `True` no código; ver `tech-spec-recuperacao-scheduled-stale-worker-cadence-uazapi.md`, glossário §4).

**Requisito de produto:** o **limite diário da campanha** (`campaigns.daily_limit`) deve continuar a ser o **teto** agregado de envios iniciais no dia, **mesmo** com vários chunks por causa de delays — o desenho atual já corta leads com `LIMIT daily_limit` na query inicial; chunks seguintes consomem o restante **pendente** até esgotar o funil inicial.

---

## Risco crítico: chunk `running` fantasma bloqueia o próximo

Em `schedule_next_initial_chunk`, **por instância**, se já existir um send `initial` em `scheduled`, `running` ou `partial`, **não** se cria outro chunk:

```1243:1254:worker_cadence.py
                SELECT id FROM campaign_stage_sends
                WHERE campaign_id = %s AND stage = 'initial' AND instance_id = %s
                  AND status IN ('scheduled', 'running', 'partial')
                LIMIT 1
                """,
                (cid, inst['instance_id']),
            )
            if cur.fetchone():
                continue  # Instância já tem chunk ativo — evita duplicação
```

Se o `folder_id` for **órfão** na API mas o send continuar **`running`**, a instância **fica bloqueada**: não agenda novo chunk — alinhado com o sintoma “campanha não continua”. **Correção implementada:** probe `list_messages` = `None` → **`failed`** e libertação da instância; **Task 7** evita falso órfão quando `list_folders` devolve **`None`** (falha de rede/HTTP), sem probe nem `failed`.

---

## Investigation Findings (Step 2 — resumo)

*(Detalhe completo mantido nas secções anteriores da conversão e abaixo enxuto.)*

- `r34e1…`: API não lista a pasta; `listmessages` 400 — **órfão confirmado** no lado UAZAPI para aquele token.
- `listmessages` **Sent** com `totalRecords` 10 vs **13** no `listfolders` — **não** usar `listmessages` para total.
- Wait ajuda só quando a pasta **existe** e ainda está em transição; **não** recupera ids removidos.

---

## Context for Development

### Codebase Patterns

- `sync_campaign_leads_from_uazapi` (`utils/sync_uazapi.py`): para cada `campaign_stage_sends`, chama `list_folders`, localiza `folder_id`; se encontrada, agregados + `needs_reconcile` / `message_find` como antes; se **não** encontrada, **probe** leve `list_messages(Scheduled, 1)` → órfão `failed` se `None`, senão só `last_sync_at` até ao próximo ciclo.
- `_sync_active_stage_folders`: a cada 10 min, por campanhas com sends ativos UAZAPI, chama `sync_campaign_leads_from_uazapi` (uma vez por `campaign_id`; a função interna percorre **todos** os sends da campanha).

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `worker_cadence.py` | `STAGE_SYNC_INTERVAL_MINUTES`, `_sync_active_stage_folders`, `_process_verify_folder_queue`, `schedule_next_initial_chunk` |
| `utils/sync_uazapi.py` | `sync_campaign_leads_from_uazapi`, probe órfão, `last_sync` sem batelada |
| `app.py` | `_create_campaign_core`, `_create_stage_campaign`, limites e chunks |
| `services/uazapi.py` | `list_folders`, `list_messages`, `create_advanced_campaign` |
| `scripts/n8n-workflows/uazapi-diagnostico-campanha-advanced.json` | Diagnóstico manual |

---

## Implementation Plan

### Tasks

- [x] **Task 1:** Documentar em comentário único ou README interno (opcional, 5 linhas) que **contadores UAZAPI** = `listfolders` apenas, intervalo **10 min** — já implementado; evita regressões futuras.
  - File: `worker_cadence.py` (cabeçalho de `_sync_active_stage_folders` ou constante `STAGE_SYNC_INTERVAL_MINUTES`)
  - Action: reforçar comentário alinhado à decisão de produto.

- [x] **Task 2:** Política **“pasta órfã”** no sync periódico: pasta ausente em `listfolders` **e** `list_messages(Scheduled, 1)` = `None` → `campaign_stage_sends.status = 'failed'`, `success_count = failed_count = 0`, `uazapi_folder_id` mantido; leads **inalterados** (continuam `pending` para novo chunk).
  - File: `utils/sync_uazapi.py` (antes do fallback pesado a `list_messages`).
  - Teste: `tests/test_sync_uazapi.py::test_sync_orphan_folder_listmessages_error_marks_send_failed`.

- [x] **Task 3:** Garantir que **`schedule_next_initial_chunk`** volte a poder criar chunk na instância após Task 2 (send já não `running`/`partial`).
  - File: `worker_cadence.py`
  - Action: validado — a query já usava só `scheduled`/`running`/`partial`; constante `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` em `utils/limits.py` + `status = ANY(%s)` em `worker_cadence.py` + docstring deixam explícito que `failed`/`done` não bloqueiam (2026-04-15). Teste: `tests/test_worker_cadence_initial_chunk.py`.

- [x] **Task 4:** Log estruturado (uma linha) quando marcar órfão: `campaign_id`, `send_id`, `instance_id`, `folder_id`.
  - File: `utils/sync_uazapi.py` — `print(json.dumps({...}))` com `event=uazapi_stage_send_orphan_probe_null` no ramo probe `None` antes do `UPDATE ... failed`.
  - Teste: `tests/test_sync_uazapi.py::test_sync_orphan_folder_listmessages_error_marks_send_failed` valida o JSON na saída.

- [ ] **Task 5 (opcional):** Painel/admin — badge “chunk órfão” quando `status=failed` com último erro ou coluna `last_error`.
  - Files: templates/API conforme padrão existente.

- [x] **Task 6:** Spec finalizada neste ficheiro (`tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md`); `tech-spec-wip.md` removido após finalize.

- [x] **Task 7:** Órfão **só** quando o probe `list_messages` devolve `None` **e** `list_folders` devolveu lista utilizável (HTTP 200, array — pasta ausente na lista). Se `list_folders` → `None` ou corpo não-lista, só `last_sync_at` (sem probe, sem `failed`). Teste: `tests/test_sync_uazapi.py::test_sync_listfolders_none_does_not_mark_failed_even_if_probe_would_be_none`.
  - Files: `utils/sync_uazapi.py`, docstring `services/uazapi.py` (`list_folders`).

- [ ] **Task 8 (opcional):** Coluna `last_error` (texto curto) ou campo JSON em `campaign_stage_sends` para motivos (`orphan_probe_null`, …) + exibição mínima em admin.

- [ ] **Task 9 (opcional):** Env `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` para desativar ramos que ainda disparam `list_messages` em massa em `needs_reconcile` (avaliar impacto em pastas `done` com lacunas vs planeado).

### Acceptance Criteria

- [x] **AC1:** Given `campaign_stage_sends` em `running` com `folder_id` ausente de `listfolders` **e** `list_messages(Scheduled, page=1)` a devolver **`None`**, when `sync_campaign_leads_from_uazapi` corre, then o send passa a **`failed`** e **não** bloqueia `schedule_next_initial_chunk` na mesma instância. *(Com `list_folders` com sucesso — lista utilizável; ver AC5 se `list_folders` falhar.)*

- [ ] **AC2:** Given campanha com `daily_limit = 30` e vários chunks `initial` ao longo do dia, when os chunks completam com sucesso, then o número de leads iniciais enviados no dia **não excede** o teto definido por `LIMIT daily_limit` na criação + política de cota TD-12 (`check_initial_chunk_daily_quota_for_campaign` / plano; **não** `can_create_campaign_today`, que é sempre `True` no código). Regressão coberta por teste manual ou integração leve se existir harness.

- [ ] **AC3:** Given sync normal com pasta existente, when `listfolders` devolve `log_sucess`, then `campaign_stage_sends.success_count` e UI refletem agregados alinhados a `min(log_sucess, planned_count)` conforme lógica existente em `sync_uazapi.py`.

- [ ] **AC4:** Given `STAGE_SYNC_INTERVAL_MINUTES`, when o worker corre, then não há segundo full sync do mesmo send antes de decorridos ~10 min desde `last_sync_at` (comportamento SQL atual mantido).

- [x] **AC5:** Given `list_folders` a falhar por rede (retorno `None` / exceção tratada) **sem** evidência de que o `folder_id` foi apagado na UAZAPI, when o sync corre, then **não** se marca o send como `failed` por esse motivo sozinho *(Task 7: só `last_sync_at`; sem probe).*

---

## Additional Context

### Dependencies

- UAZAPI `neurix.uazapi.com` estável; confirmação do provedor sobre remoção de pastas e `listmessages` cap.

### Testing Strategy

- Manual: simular send `running` com `folder_id` inválido na BD de staging; verificar transição para `failed` e novo chunk agendado.
- `pytest tests/test_sync_uazapi.py` após alterações em `sync_uazapi.py`.

### Notes

- **listmessages** com teto ~10: confirmado com o provedor; não contar com paginação até lá.
- **Latência UX:** até ~**10 min** entre syncs completos + **~3 min** verify pós-create — aceite para alinhar UI com a API sem martelar `listmessages`.
- **Ficheiro canónico:** `tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md` (quick-spec finalize).
- **Advanced Elicitation** aplicada em 2026-04-15: secções *Órfão vs falha transitória*, *Pre-mortem / FMEA / ADR* e *Backlog* Tasks 7–9 incorporadas acima; **ronda 2** (*Challenge, War room, Lições*) incorporada na subsecção homónima antes de *Root cause analysis*.

---

**Artefato n8n:** `scripts/n8n-workflows/uazapi-diagnostico-campanha-advanced.json`
