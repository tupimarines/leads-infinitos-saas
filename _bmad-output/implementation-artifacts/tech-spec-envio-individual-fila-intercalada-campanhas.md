---
title: 'Envio por mensagem individual + fila intercalada (campanhas Uazapi)'
slug: envio-individual-fila-intercalada-campanhas
created: '2026-04-29'
status: ready-for-dev
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
tech_stack:
  - Python 3.x
  - Flask + Flask-Login
  - PostgreSQL (psycopg2, RealDictCursor)
  - Redis + RQ (jobs; fila outbox v1 em Postgres)
  - requests (Uazapi HTTP)
files_to_modify:
  - app.py
  - worker_message_outbox.py
  - worker_cadence.py
  - services/uazapi.py
  - utils/limits.py
  - utils/campaign_send_policy.py
  - utils/sync_uazapi.py
  - utils/uazapi_pacing.py
  - utils/next_valid_uazapi_send.py
  - utils/config.py
  - templates/admin/campaigns_new.html
  - tests/test_admin_campaign_crud.py
  - tests/test_worker_stale_recovery.py
code_patterns:
  - Migrações DDL adjacentes a init_db em app.py
  - Workers com loop process_cadence e commit antes de I/O longo
  - SUPER_ADMIN_EMAILS / is_super_admin para gate admin
test_patterns:
  - pytest em tests/
  - Mocks de UazapiService em testes de campanha
---

# Tech spec: envio por mensagem individual + fila intercalada (campanhas Uazapi)

**Workflow:** BMAD quick-spec (passos 1–4 completos, incl. investigação profunda, elicitação avançada sintetizada, revisão adversarial).  
**Idioma:** pt-BR · **Estado:** ready-for-dev · **Data:** 2026-04-29.

**Fontes de verdade do produto:**

- `doc-proposta-modelo-envio-individual-fila-intercalada.md`
- `doc-sistema-atual-campanhas-uazapi-chunks-cadencia.md`
- `doc-mapeamento-codigo-nomenclatura-campanhas-uazapi.md`
- `doc-regras-comunicacao-ia-nomenclatura-app-campanhas.md`

---

## Overview (BMAD Step 1)

### Problem Statement

O modelo atual usa `UazapiService.create_advanced_campaign` (`POST /sender/advanced`), pastas remotas (`folder_id` / `uazapi_folder_id`) e `campaign_stage_sends` por chunk. Isso concentra envios por pasta, dificulta log por lead/tentativa, retomada fina e intercalação entre instâncias/utilizadores. O produto definiu substituir por **envio individual** (`POST /send/text`, `POST /send/media`), fila persistida, throttle SSOT em Postgres, cooldown aleatório persistido na criação, polling HTTP na UI admin e migração com chunks `scheduled`/`failed`/`queued` ignorados.

### Solution

Introduzir **`campaign_message_outbox`** + **`campaign_send_attempts`**, worker dedicado ou ramo integrado em `worker_cadence.py`, extensão de `UazapiService` para envio unitário com `track_id`/`track_source`, feature flag + admin-only, APIs de polling e pausa/retomar; manter legado em dual-run até deprecação.

### Scope

**In scope:** modelo de dados outbox, máquina de estados, algoritmo de fila (prioridade de etapa + FIFO + intercalação por instância), throttle/cooldown, contratos HTTP admin, worker/idempotência/retry, migração, testes, observabilidade, inventário n8n inicial.

**Out of scope:** SSE/WebSocket da app; SSE Uazapi no browser; prioridade “urgente” entre campanhas; substituição total de `sync_uazapi` nesta fase; **qualquer integração ou fluxo Chatwoot** (labels, gatilhos, cadência dependente de Chatwoot) — não faz parte desta entrega.

---

## Complementos da documentação base (auditoria)

Incorporação de pontos de `doc-proposta-modelo-envio-individual-fila-intercalada.md`, `doc-sistema-atual-campanhas-uazapi-chunks-cadencia.md` e `doc-regras-comunicacao-ia-nomenclatura-app-campanhas.md` que reforçam ou detalham a spec.

### Proposta de produto

| Tema | Complemento |
|------|-------------|
| **Gate Fase 1** | **Decisão (ADR-4 / Task 13):** `USE_MESSAGE_OUTBOX` **e** email do **operador** em `SUPER_ADMIN_EMAILS`; `created_by_admin_id` é só auditoria (ver `utils/config.py`, `_phase1_outbox_operator_is_superadmin`). |
| **Volume / seletor** | Regra de negócio: `daily_limit` da campanha **≤** teto da instância; UI alinha; worker revalida via Postgres. |
| **Intercalação** | Padrão ilustrativo: alternar carga entre instâncias (`user1-msg1`… intercalado) — validar em staging com log ou métrica `last_served_instance_id`. |
| **Retomada** | Combinar `campaign_leads` + outbox persistida; após reinício do processo, **nenhuma** decisão de envio fica só em memória. |
| **Sync Uazapi (futuro)** | Webhook / `message_id` / polling extra fica fora do MVP; v1: POST + BD. `sync_uazapi` permanece para **legado** em dual-run. |
| **Testes** | Estender/ramificar `test_worker_stale_recovery` e critérios `INITIAL_CHUNK_*` para **legado (flag off)** vs **outbox (flag on)**. |
| **Observabilidade** | Além de logs: **métricas** (p.ex. Prometheus, se o stack suportar) — contadores por `outcome`, latência; **alerta** se taxa de falha > limiar (definir em ops). |
| **Go-live Fase 1** | Checklist PM: **taxa de erro** e **satisfação operacional** documentadas antes de abrir a não-admins. |
| **Copy** | UI: *«ritmo aleatório definido pelo sistema»* — sem campo numérico de cooldown. |
| **Spike (opcional)** | Proposta: 1 instância, fila mínima, medir latência **antes** de alterar `_create_campaign_core` — opcional. |

### Sistema atual (as-is)

| Tema | Complemento |
|------|-------------|
| **`scheduled_start`** | Legado envia `scheduled_for` (minutos) à Uazapi; outbox: `scheduled_start` no app determina `next_run_at` mínimo **sem** depender de pasta remota. |
| **`send_batch` / `per_instance_limit`** | Hoje atribuição em `CampaignLead` para chunks — definir no PR se mantém compatibilidade reporting ou substituição pela semântica outbox (`instance_id` por linha). |
| **`uazapi_instance_sends`** | Primeiro lote legado grava aqui; campanhas só-outbox: especificar **mapping** ou exclusão para não partir dashboards. |
| **`enable_cadence`** | Follow-ups na **mesma outbox** com `step_priority` (alinhar Q7 da proposta). **Chatwoot está fora de âmbito** nesta spec — apenas cadência/envio Uazapi via outbox vs legado chunk. |
| **`INITIAL_CHUNK_ACTIVE_SEND_STATUSES`** | Aplica-se ao fluxo **chunks/pasta**; não bloquear outbox por linha legado sem política de migração explícita. |

### Regras de nomenclatura

- Usar nomes canónicos: `campaign_message_outbox`, `campaign_send_attempts` — **não** chamar o novo modelo de “fila genérica” nem confundir com `campaign_stage_sends` (pastas).
- Citar workers por nome: `worker_cadence.process_cadence` e o módulo/tick de outbox.
- **Estado remoto vs BD:** `campaign_send_attempts.outcome` = interpretação da **resposta HTTP** do envio; não assumir igual a estados `listmessages` do provedor (análogo a `running` vs `queued` no legado).
- Distinguir `SUPER_ADMIN_EMAILS` (quem pode operar) de `created_by_admin_id` (quem criou a campanha).

---

## Context for Development (BMAD Step 2 — investigação)

### Tech stack (confirmado)

Monólito Flask (`app.py`), PostgreSQL, workers `worker_cadence.py` / `worker_sender.py`, cliente `services/uazapi.py`, utilitários em `utils/`. Redis/RQ existem para outros jobs; **v1 da fila outbox = Postgres** (sem dependência obrigatória de Redis para ordenação).

### Codebase patterns

- **Criação de campanha:** `_create_campaign_core` em `app.py` (~5192+) — instâncias `instance_ids`, `rotation_mode` (`single` | `round_robin`), `use_uazapi_sender`, `delay_min_minutes` / `delay_max_minutes` em `campaigns` (nomes legados “minutes”; hoje usados como intervalo humanizado em fluxo continue-chunk — ver decisão § DDL).
- **Quota chunk:** `utils/campaign_send_policy.uazapi_initial_chunk_distribution_limits`, `utils/limits.can_create_campaign_today`, `check_daily_limit`.
- **Worker cadência:** `process_cadence()` em `worker_cadence.py` (~1363); entrypoint `if __name__ == "__main__": process_cadence()`.
- **Uazapi:** `send_text` → `timeout=15`; `send_media` → `timeout=30` (`services/uazapi.py`) — spec de mídia exige timeout maior e variável por env.
- **Admin:** `SUPER_ADMIN_EMAILS` (`utils/config.py`), `is_super_admin` / `current_user.email in SUPER_ADMIN_EMAILS` (`app.py` ~1041, rotas ~8175+).

### Files to reference

| File | Relevance |
| ---- | --------- |
| `app.py` | `_create_campaign_core`, DDL inline, rotas admin campanhas, `Campaign` |
| `worker_cadence.py` | `process_cadence`, `schedule_next_initial_chunk`, `_materialize_scheduled_stage_sends` |
| `services/uazapi.py` | `create_advanced_campaign`, `send_text`, `send_media` |
| `utils/limits.py` | `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`, `check_daily_limit`, `get_user_daily_limit` |
| `utils/campaign_send_policy.py` | quotas initial chunk |
| `utils/uazapi_pacing.py` | `default_inter_message_delay_range_minutes`, `maybe_long_gap_minutes` |
| `utils/next_valid_uazapi_send.py` | janela BRT |
| `utils/sync_uazapi.py` | legado folder/message_find |
| `templates/admin/campaigns_new.html` | UI criação |
| `uazapi-openapi-spec (1).yaml` | `sendText`, `sendMedia`, campos `track_id` |
| `scripts/n8n-uazapi-requests.md` | consumidor `folder_id` |

### Technical decisions (investigação + ADRs)

**ADR-1 — Nova tabela vs só `campaign_stage_sends`:** usar **`campaign_message_outbox`** separada; não expandir `campaign_stage_sends` para 1 linha/msg (menos acoplamento ao legado `folder_id`).

**ADR-2 — Unidade de cooldown persistido:** colunas atuais `campaigns.delay_min_minutes` / `delay_max_minutes` são **nomes legados**. Para faixa **600–900 s** (10–15 min), preferir **novas colunas** `outbox_delay_min_seconds` / `outbox_delay_max_seconds` (ou migrar documentação e valores numéricos com comentário SQL) para não ambiguidade com fluxos que tratam os campos antigos como “minutos” em `default_inter_message_delay_range_minutes`. **Implementação deve escolher uma linha e aplicá-la em todos os leitores do worker outbox.**

**ADR-3 — `worker_sender.py`:** não usar para campanhas outbox; apenas `worker_cadence` + novo módulo — evitar dois consumidores sem coordenação (**mitiga F5 revisão adversarial**).

**ADR-4 — Gate Fase 1:** `USE_MESSAGE_OUTBOX` no ambiente **e** operador com email em `SUPER_ADMIN_EMAILS`. Coluna `created_by_admin_id` **não** participa do gate (só auditoria). Implementação: `_phase1_outbox_operator_is_superadmin`, `_require_message_outbox_phase1_api`.

**ADR-5 — Pipeline transacional worker ↔ Uazapi (F13 resolvido):** é **proibido** manter transação PostgreSQL aberta durante chamada HTTP ao Uazapi. Fluxo obrigatório em **três fases:**  
**(A) Claim curto:** uma transação que faz `SELECT … FOR UPDATE SKIP LOCKED`, revalida throttle/leituras necessárias **sem** I/O externo longo, marca o item como “em envio” (`sending` / claim), **`COMMIT`**.  
**(B) I/O rede:** fora de qualquer transação — `POST /send/text` ou `/send/media`; timeouts só aqui.  
**(C) Persistência sucesso/falha:** nova transação: insere `campaign_send_attempts`, actualiza `campaign_message_outbox`, `campaign_leads`; **se HTTP status = 200** e política interna de sucesso, na **mesma** transação aplica regra de **§6.1 Contagem pós-200**. Rollback de (C) não desfaz (B); por isso estados `unknown` / reconciliação tratados à parte.

---

## Advanced Elicitation (sintetizado — métodos do registry BMAD)

Métodos aplicados ao documento (sem menu interativo; resultados incorporados nas secções seguintes):

| Método | Uso |
|--------|-----|
| **Pre-mortem** | Falhas: timeout pós-POST, dual worker, limite diário a meio do dia, migração índice lead errado — mitigações em §6.1, §11 e ADRs. |
| **Architecture Decision Records** | ADR-1 a ADR-5 acima. |
| **Failure Mode Analysis** | Componente PostgreSQL lock, Uazapi 5xx, instância disconnected, duplo resume — estados `unknown`, advisory lock, `get_status`. |
| **Red Team vs Blue Team** | Ataque: deduplicação fraca → defesa: UNIQUE outbox + transação + política retry; ataque: PII em logs → defesa: truncar JSONB, RBAC. |

---

## Revisão adversarial — achados integrados (≥10)

| ID | Problema | Resolução na spec |
|----|----------|-------------------|
| F1 | Ambiguidade minutos vs segundos em `delay_*` | ADR-2: colunas novas ou contrato explícito de unidade só para outbox. |
| F2 | UNIQUE `(campaign, lead, stage)` bloqueia linhas de retry legítimas | UNIQUE só para fila ativa **ou** chave inclui `attempt_id`; tentativas em `campaign_send_attempts`. |
| F3 | Fairness multi-tenant pouco formalizado | §5.2: candidatos globais + round-robin entre instâncias com “top” por `(step_priority, queued_at)`. |
| F4 | Redis vs Postgres para fila | v1 Postgres apenas; Redis opcional futuro. |
| F5 | Conflito com `worker_sender.py` | ADR-3: outbox só no worker cadence/outbox dedicado. |
| F6 | Resolução de caminho de mídia não detalhada | Tarefa explícita: reutilizar resolução já usada em `_create_campaign_core` / `campaign_steps`. |
| F7 | `idempotency_key` estável vs nova tentativa | Usar chave estável por **intenção de envio**; retries incrementam `attempt_no` em `campaign_send_attempts`, não nova linha outbox sem política. |
| F8 | Falta `.env.example` para novas envs | Tarefa: documentar `USE_MESSAGE_OUTBOX` em artefacto de deploy (não criar `.md` extra sem pedido; pode ser comentário em `utils/config.py`). |
| F9 | `rotation_mode` valor canónico | Código usa `round_robin` (underscore); spec e UI devem usar o mesmo token. |
| F10 | Testes de propriedade sem estratégia de BD | Usar transações rollback ou DB de teste descartável (pytest fixture existente). |
| F11 | PII em `uazapi_response` | Política: truncar telefone/texto; opcional coluna só hash. |
| F12 | `worker_cadence` sleep 20–40s vs cooldown 600–900s | Caminhos diferentes (Mega/API genérico vs outbox); outbox **não** reutiliza esse sleep para cadência Uazapi individual. |

---

# Corpo da especificação (design)

## 1. Resumo executivo

Substituir **pastas** (`create_advanced_campaign`, `folder_id`) por **envio individual** (`POST /send/text`, `POST /send/media`), fila **Postgres** com ordenação **etapa (initial > follow1 > …)** + **FIFO** + **intercalação por `instance_id`**. Throttle: Postgres + interpretação no worker (`check_daily_limit`, `utils.limits`, `campaign_send_policy`). Cooldown: aleatório na **criação** da campanha, **persistido**, não editável pelo user; pausa longa alinhada a `uazapi_pacing`. UI admin: **polling** apenas. Fase 1: **admin-only** + feature flag. Migração: ignorar chunks `scheduled`/`failed`/`queued`; retomar no próximo lead coerente (ex. lead 19). Observabilidade: evento por tentativa; inventário n8n/`folder_id`.

## 2. Fora de âmbito

- SSE app dashboard; SSE Uazapi no browser com token cliente.
- Prioridade entre campanhas do mesmo utilizador (v1 não).
- Eliminação completa de `sync_uazapi` nesta fase.
- **Chatwoot** (qualquer menção: labels, status, matriz de decisão em `process_cadence`, etc.) — **fora de âmbito**; não especificar nem implementar ramos Chatwoot nesta feature.

## 3. Modelo de dados

### 3.1 `campaign_message_outbox`

Campos principais: `id`, `campaign_id`, `campaign_lead_id`, `instance_id`, `stage`, `step_priority`, `status`, `queued_at`, `next_run_at`, `idempotency_key` (único por política F2), `uazapi_track_id`, `payload_summary` (JSONB sem PII), timestamps.

Índices: `(status, next_run_at)` filtrado; `(instance_id, next_run_at)`; `(campaign_id, updated_at)` para polling; UNIQUE parcial conforme ADR F2.

### 3.2 `campaign_send_attempts`

Por POST: `outbox_id`, `attempt_no`, `http_status`, `uazapi_response` (truncado), `outcome`, latência, `started_at`/`finished_at`.

### 3.3 Legado

`campaign_stage_sends` permanece para dual-run; novo fluxo não grava `uazapi_folder_id` para envios outbox.

## 4. Máquina de estados (outbox)

`pending` → `locked` → `sending` → `sent` | `failed` | (policy) `unknown`; `cancelled` por pausa; retries conforme política sem violar invariante “um `sent` por (lead, stage)”.

## 5. Algoritmo da fila

1. Filtrar `pending` com `next_run_at <= now()` e janela (`is_campaign_send_window` / `next_valid_send_utc_naive`).  
2. Ordenar por `step_priority` asc, `queued_at` asc.  
3. Por `instance_id`, manter fila interna; entre instâncias, **round-robin** ou menor `next_run_at` para intercalar.  
4. **Claim (ADR-5-A):** transação **curta** — `SELECT … FOR UPDATE SKIP LOCKED`, validações só com dados já lidos ou leituras rápidas no mesmo `BEGIN…COMMIT`, marcar item como `sending`, commit. **Não** chamar Uazapi dentro desta transação.  
5. **HTTP (ADR-5-B):** `POST` Uazapi sem cursor/transação Postgres activa.  
6. **Persistência (ADR-5-C):** nova transação para tentativa + estado terminal + §6.1 se `HTTP 200`.  
7. Cooldown: ler faixa persistida (ADR-2); sorteio após conclusão de (C) para `next_run_at` do ciclo seguinte — **persistente após criação** da campanha.

## 6. Throttle e cooldown

Antes do **claim (A):** avaliar campanha activa, janela BRT, `check_daily_limit` / quotas (`utils.limits`, `utils/campaign_send_policy`) com base em contagens **atuais** em Postgres — **sem** consumir cota antes do `200` (ver §6.1).

**Entre (A) e (B):** montar payload (texto/mídia, `track_id`) **em memória** a partir de `campaign_steps` / lead — **sem** transação longa.

Após **HTTP (B):** na fase **(C)** gravar tentativa (`http_status`, corpo como evidência), estado do outbox, lead, e **§6.1** se `HTTP 200`.

### 6.1 Contagem de sucesso (HTTP 200) e visão master superadmin

**Regra de produto:** cada **`POST` Uazapi que retorna HTTP `200`** (aceite como envio bem-sucedido pelo cliente HTTP, após `raise_for_status` ou equivalente) **actualiza o contador da campanha** na **mesma transação** em que se persiste o sucesso do envio (fase **C** de ADR-5).

**Persistência alinhada à listagem master:**

1. **Fonte analítica já existente:** `get_sent_today_count` / `get_sent_today_campaign_initial_count` em `utils/limits.py` derivam de **`campaign_leads`** (`status`, `sent_at`, etapa inicial). Por isso, em caso de `200`, na transação **(C)** deve actualizar-se **`campaign_leads`** (p.ex. `status='sent'`, `sent_at=now()`, campos de etapa coerentes com o `stage` enviado) de forma que essas funções **reflectem imediatamente** o envio no dia BRT.

2. **Coluna `campaigns.sent_today`:** se a **visão master** do superadmin (templates/API de listagem) lê **`campaigns.sent_today`**, então na **mesma** transação **(C)** fazer `UPDATE campaigns SET sent_today = COALESCE(sent_today,0) + 1 WHERE id = …` (ou política equivalente) para **não divergir** do que o admin vê na lista. Se a listagem usar só contagens derivadas de leads, documentar e **manter uma única fonte** — mas o pedido de produto exige **paridade explícita** listagem master ↔ estado persistido; o incremento em `campaigns.sent_today` satisface “contador da campanha” ao lado da derivação por leads.

**Não** incrementar contadores diários **antes** do `200`. Falhas e timeouts **não** incrementam; retries só contam um `200` final por política de idempotência.

### 6.2 Cooldown operacional

Após **(C)** bem-sucedida, calcular próximo `next_run_at` com faixa persistida na campanha (ADR-2) e pausa longa (`uazapi_pacing`) quando aplicável.

## 7. Contratos HTTP (admin, Fase 1)

- Criar campanha: estender fluxo que hoje chama `create_advanced_campaign` para ramo outbox: enfileirar sem pasta.  
- `GET /api/admin/campaigns/<campaign_id>/outbox-state?since_id=&since_attempt_id=&updated_after=`  
- `POST /api/admin/campaigns/<campaign_id>/outbox/pause`, `POST …/outbox/resume`  
- 403 se não admin / flag off; 409 concorrência; 429 rate limit.

Rotas implementadas em `app.py` com prefixo `/api/admin/campaigns/` (alinhar com `admin_campaign_detail_api`). Fase 1: **superadmin** + `USE_MESSAGE_OUTBOX`; POST exige CSRF (`X-CSRF-Token` ou JSON `csrf_token`).

## 8. Worker

- Novo ficheiro sugerido: `worker_message_outbox.py` **ou** funções privadas em `worker_cadence.py` chamadas no início/fim de `process_cadence`.  
- Frequência: tick 5–15 s; **um envio global por tick** (simplicidade) salvo pool por instância documentado.  
- Idempotência: BD + `track_id`; confirmar com Uazapi semântica exata (TODO OpenAPI).

## 9. Migração e feature flag

- `USE_MESSAGE_OUTBOX` (default off).  
- Algoritmo lead 19 documentado na proposta; implementar script ou passo SQL documentado em migração.

## 10. Testes (CA detalhados abaixo)

Casos: propriedade um `sent`/par; duplo clique; disconnect; limite diário BRT; intercalação; prioridade etapa; migração; cooldown persistido; auth não admin.

## 11. Riscos (top 5)

Timeout ambíguo; idempotência Uazapi; volume `campaign_send_attempts`; concorrência legado+outbox; n8n quebrado — ver ADRs e anexos.

## 12. Uazapi (anexo contrato)

| Uso | HTTP | operationId |
|-----|------|-------------|
| Texto | `POST …/send/text` | `sendText` |
| Mídia | `POST …/send/media` | `sendMedia` |
| Legado | `POST …/sender/advanced` | `sendAdvancedCampaign` |

**TODO:** validar idempotência real com fornecedor para mesmo `track_id`.

## Anexo n8n

`scripts/n8n-uazapi-requests.md` depende de `folder_id`; inventariar workflows em produção na Sprint final.

---

## Implementation Plan (BMAD Step 3)

### Tasks

- [x] **Task 1:** Adicionar DDL `campaign_message_outbox` e `campaign_send_attempts` + índices em `app.py` (`init_db` / migração idempotente existente).  
  - **File:** `app.py`  
  - **Action:** `CREATE TABLE IF NOT EXISTS`, comentários de unidade (ADR-2); UNIQUE conforme F2.

- [x] **Task 2:** Acrescentar colunas de cooldown outbox **ou** documentar mapeamento seguro para colunas existentes.  
  - **Files:** `app.py` (DDL), comentário em `_create_campaign_core`  
  - **Action:** Na criação (admin/outbox), sortear faixa aprovada e gravar.

- [x] **Task 3:** Implementar `UazapiService.send_text_idempotent` / `send_media_campaign` (nomes finais a critério) com `track_id`, `track_source`, timeout configurável via env para mídia.  
  - **File:** `services/uazapi.py`  
  - **Action:** Não alterar assinaturas existentes sem necessidade; adicionar métodos.

- [x] **Task 4:** Implementar ciclo **ADR-5** (claim → HTTP → persistência) em `process_message_outbox_tick`; integrar throttle antes do claim; na transação **(C)** após **HTTP 200**, actualizar `campaign_leads`, **`campaigns.sent_today`** se a listagem master usar essa coluna, e garantir coerência com `get_sent_today_*` (§6.1).  
  - **Files:** novo `worker_message_outbox.py` **ou** `worker_cadence.py`  
  - **Action:** Integrar no loop principal; **não** usar `worker_sender.py`; **nunca** `POST` Uazapi dentro de `BEGIN` sem `COMMIT` prévio.

- [x] **Task 5:** Feature flag `USE_MESSAGE_OUTBOX` em `utils/config.py` + leitura em worker e `_create_campaign_core`.  
  - **Files:** `utils/config.py`, `app.py`, `worker_cadence.py`

- [x] **Task 6:** Ramo em `_create_campaign_core`: se flag + admin + campanha elegível, **não** chamar `create_advanced_campaign`; enfileirar outbox e definir `next_run_at` inicial respeitando `scheduled_start`.  
  - **File:** `app.py`

- [x] **Task 7:** Endpoints GET polling + POST pause/resume; `@admin_required` / superadmin conforme Fase 1.  
  - **File:** `app.py`

- [x] **Task 8:** Template admin polling JS (intervalo 2–5 s, backoff).  
  - **File:** `templates/admin/campaigns_new.html` ou partial dedicado.

- [x] **Task 9:** Script ou função de migração “lead índice” para campanhas existentes (chunks ignorados).  
  - **File:** novo `scripts/migrate_campaign_to_outbox.py` ou secção documentada em migração.

- [x] **Task 10:** Testes pytest — estender `tests/test_admin_campaign_crud.py`, novos casos outbox; recovery pode alinhar a `tests/test_worker_stale_recovery.py`.  
  - **Files:** `tests/…`

- [x] **Task 11:** Logging estruturado (evento por tentativa): campo `event`, `campaign_id`, `outbox_id`, `instance_id`, latência, outcome — sem PII (F11).

- [x] **Task 12:** Atualizar inventário `scripts/n8n-uazapi-requests.md` com nota “legado folder” vs outbox quando aplicável.

- [x] **Task 13:** Documentar e implementar **gate Fase 1** (superadmin vs `created_by_admin_id`) conforme linha escolhida na secção “Complementos”; actualizar templates admin com copy canónica (*ritmo aleatório definido pelo sistema*) onde o utilizador vê o cooldown.

- [x] **Task 14:** **Observabilidade ops:** expor métricas de tentativa (`outcome`, latência) no stack existente (Prometheus ou equivalente) e definir limiar de alerta de taxa de falha em conjunto com ops.

### Acceptance Criteria

*Checkboxes `[x]` abaixo: evidência da suite pytest `tests/test_outbox_spec_acceptance.py`, `tests/test_admin_campaign_crud.py`, `tests/test_outbox_prometheus.py`, `tests/test_worker_stale_recovery.py` (**22 passed**). **AC7:** há teste de 403 em `GET .../outbox-state` (`test_ac7_outbox_state_forbidden_for_plain_admin`), mas o texto do AC7 refere «criação outbox» — mantém-se `[ ]` até alinhar spec ou acrescentar teste explícito de criação.*

- [ ] **AC1:** Dado **feature flag off**, quando se cria campanha Uazapi admin, então o fluxo continua a usar `create_advanced_campaign` e `campaign_stage_sends` como hoje.
- [x] **AC2:** Dado **flag on** e utilizador **superadmin**, quando cria campanha, então **não** é chamado `create_advanced_campaign` para essa campanha e existem linhas `campaign_message_outbox` `pending` para leads elegíveis.
- [x] **AC3:** Dado item `pending` e limites OK, quando o worker processa, então existe exactamente **uma** linha `campaign_send_attempts` com `outcome` coerente e outbox em estado terminal.
- [ ] **AC4:** Dado mesmo `(campaign_lead_id, stage)` com sucesso, quando retry ou segundo worker corre, então **não** há segundo `sent` (propriedade QA).
- [x] **AC5:** Dado limite diário esgotado (BRT), quando o worker avalia, então `next_run_at` avança para janela futura/dia seguinte sem `POST`.
- [x] **AC6:** Dado `GET outbox-state` com `since_id`, quando há novas tentativas, então resposta inclui apenas alterações posteriores ao cursor.
- [ ] **AC7:** Dado utilizador **não** superadmin (Fase 1), quando chama criação outbox, então **403**.
- [x] **AC8:** Dado migração exemplo (10 done + 8 scheduled ignorados), quando rotina corre, então primeiro outbox corresponde ao lead **19** (ordem consistente com ordenação de leads do sistema).

- [ ] **AC9:** Dado **pedido de saída da Fase 1** (abrir a utilizadores não-admin), when a equipa faz go-live review, then existem **métricas de erro** e **critério de satisfação operacional** registados (checklist PM na proposta).

- [ ] **AC10:** Dado UI de criação/edição de campanha outbox, when o utilizador consulta o ritmo entre envios, then **não** há campo editável de segundos e o texto segue a copy canónica (*ritmo aleatório definido pelo sistema*).

- [x] **AC11:** Dado **HTTP 200** do Uazapi para um envio outbox, quando a transação **(C)** commita, então **`campaign_leads`** reflecte `sent` no BRT de forma que `get_sent_today_campaign_initial_count` (e equivalentes usados em quotas) aumenta em conformidade **e** **`campaigns.sent_today`** é incrementado se a visão master superadmin depender dessa coluna — **sem** incremento em falhas ou antes do `200`.

- [x] **AC12:** Dado campanha em estado **pausado** (ou equivalente persistido), quando o worker executa tick outbox, então **nenhum** novo `POST` Uazapi é emitido para essa campanha até **resume**.

### Dependencies

- API Uazapi estável; confirmação idempotência `track_id`.  
- Postgres migrações aplicadas antes do worker novo.

### Testing Strategy

- Unit: sorteio cooldown, ordenação fila, UNIQUE outbox.  
- Integração: mock `requests.post` para Uazapi; worker + BD transacional.  
- Manual: staging admin, 2 instâncias, verificar intercalação em logs.

### Notes

- **project-context.md:** alterações incrementais; não modularizar monólito além do necessário.  
- Revisitar **ADRs** após primeira implementação se surgirem conflitos com `delay_min_minutes` legado.

---

## Quick Summary (BMAD Step 4)

| Métrica | Valor |
|---------|------|
| Tasks | 14 |
| Acceptance criteria | 12 |
| Files touched (planeado) | ver frontmatter `files_to_modify` |

**Próximo passo recomendado:** implementar num **contexto fresco** com prompt `quick-dev` apontando para este ficheiro.

---

## Nota sobre “FIFO” vs prioridade de etapa

Ver fecho da proposta de produto: ordenador composto `(step_priority, queued_at)` — não contradição com fairness entre utilizadores dentro da mesma prioridade de etapa.

---

## Revisão adversarial — segunda passagem (2026-04-29)

Objetivo: stress-test da spec já revista (F1–F12). Severidade: **C** crítica · **A** alta · **M** média · **B** baixa. Validade: todas tratadas como reais até prova em código.

| ID | Sev | Achado | Mitigação / follow-up na implementação |
|----|-----|--------|----------------------------------------|
| **F13** | ~~C~~ **Resolvido** | Ver **ADR-5**: claim curto (A) → HTTP sem tx (B) → persistência (C). | **Fechado na spec** — implementar sem desvio. |
| **F14** | A | **Lock órfão / `sending` eterno:** Worker morre após `UPDATE … sending` e antes de gravar `outcome`. Itens ficam invisíveis ou bloqueados. A spec não fixa `locked_until` / **reaper** (job que repõe `sending` → `pending` após TTL) nem idempotência na retomada. | Tarefa: coluna `locked_at` / `sending_started_at` + reaper no tick ou processo separado; teste de kill -9. |
| **F15** | M | **“Lead 19” é frágil:** AC8 supõe ordenação estável de leads. Se a query de migração usar critério diferente do runtime (`ORDER BY id` vs `created_at`), o índice 19 **não** é o mesmo lead. | Fechar **uma** função canónica `ordered_campaign_lead_ids(campaign_id)` usada em migração e em enqueue. |
| **F16** | M | **1 envio / tick global (§8) + cooldown 600–900s:** O gargalo do sistema fica **subdeclarado**; com muitas instâncias, a intercalação pode ser teoricamente inútil se o tick serializa tudo. | Documentar **meta de throughput** ou limites aceitáveis de fila; considerar N workers com `SKIP LOCKED` (já aludido) **sem** tick global único se o produto exigir. |
| **F17** | ~~A~~ **Fechado (Task 13)** | Gate Fase 1: **`USE_MESSAGE_OUTBOX` + operador em `SUPER_ADMIN_EMAILS`** (`is_super_admin` / `_phase1_outbox_operator_is_superadmin`). **`created_by_admin_id` só auditoria.** Documentado em `utils/config.py`; copy UI *ritmo aleatório definido pelo sistema* nos templates de criação. |
| **F18** | B | **Rotas ainda literais** (`GET …/outbox-state`) — grep do repositório não encontra; onboarding lento. | Na Task 7, fechar paths reais (p.ex. prefixo `/admin/api/...`) e listar no corpo §7. |
| **F19** | M | **Pausa:** falta AC explícito “campanha pausada → zero POST”. | **AC12** (pausa); ver §Implementation Plan. |
| **F20** | M | **Legado remoto:** Campanha em dual-run com `folder_id` ativo no Uazapi — migrar para outbox **sem** chamar `edit_campaign` / `delete` no provedor pode deixar **filas duplicadas** (pasta a correr + outbox). | Checklist migração: `stop`/`delete` em pastas legado **ou** congelar campanha até drenar; documentar. |
| **F21** | M | **Segurança** (CSRF, rate limit por `user_id`) citada na proposta de produto mas **absente dos AC** — regressão fácil. | Incluir em Task 7/10 verificação de padrão Flask existente; AC de fumo para POST mutáveis. |
| **F22** | ~~A~~ **Fora de âmbito** | Qualquer cruzamento **`enable_cadence` + Chatwoot** não entra nesta spec (**Chatwoot OOS**). Para esta entrega, especificar apenas **outbox vs legado chunk** para envios Uazapi e follow-ups na mesma fila outbox (prioridade de etapa). | Ver §2 Fora de âmbito; sem matriz Chatwoot. |
| **F23** | M | **Múltiplas réplicas de worker** (K8s): `SKIP LOCKED` ajuda, mas dois processos a chamar `get_status` + POST na mesma instância podem contornar intercalação “suave” se a política for só em DB. | Declarar: **1 réplica** para outbox v1 **ou** lock distribuído / fila por `instance_id` com prova. |
| **F24** | ~~A~~ **Resolvido** | Momento do incremento das contagens diárias / master. | Ver **§6.1**: incremento **apenas** na transação **(C)** após **HTTP 200**, com `campaign_leads` + `campaigns.sent_today` conforme listagem master. |

### Decisões mínimas antes do primeiro merge

1. ~~**ADR-4** gate Fase 1~~ → fechado (Task 13).  
2. ~~**Pipeline transacional F13**~~ → cumprido por **ADR-5** (implementação obrigatória).  
3. **Reaper** F14 ou equivalente.  
4. ~~**Cadência vs Chatwoot F22**~~ → **Chatwoot fora de âmbito**; tratar só **outbox Uazapi** vs legado chunk nesta feature.

**Estado da spec:** `ready-for-dev`; **bloqueadores de PR** restantes típicos: **F14**. **F13**, **F24**, **F17/ADR-4** fechados na implementação e na spec (ADR-5 / §6.1 / Task 13).
