---
title: 'Ordem de disparos alinhada ao CSV + log JSON de envios por campanha'
slug: ordem-disparos-csv-log-json-campanhas
created: '2026-05-01'
status: ready-for-dev
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  - Python 3.x
  - Flask + Flask-Login
  - PostgreSQL (psycopg2, RealDictCursor)
  - Worker `worker_message_outbox.py` / legado UAZAPI `create_advanced_campaign`
files_to_modify:
  - app.py
  - worker_message_outbox.py
  - worker_cadence.py
  - scripts/migrate_campaign_to_outbox.py
  - scripts/debug_uazapi_initial_flow.py
  - services/uazapi.py
  - templates/admin/
  - tests/test_admin_campaign_crud.py
  - tests/test_outbox_spec_acceptance.py
code_patterns:
  - Coluna `campaign_leads.csv_row_order` já existe; UI já ordena por `COALESCE(csv_row_order, id)`
  - Envio legado usa `ORDER BY COALESCE(send_batch, 999), id` e batches por `send_batch` derivado de `ORDER BY id ASC`
  - Outbox ordena worker por `step_priority, queued_at` e aplica `_pick_round_robin` entre instâncias
test_patterns:
  - pytest com mocks de `UazapiService`; asserts de ordenação em queries e ordem de dequeue simulada
---

# Tech-spec: ordem de disparos (CSV) + arquivo JSON de auditoria de envios

**Idioma:** pt-BR · **Autor do fluxo:** BMAD quick-spec · **Data:** 2026-05-01.

---

## Overview

### Por que a sequência de disparos parece “desordenada”?

Hoje coexistem **três causas** independentes (qualquer uma já quebra a correspondência visual entre a tabela “Leads da Campanha” e a ordem cronológica dos envios):

1. **Modo legado UAZAPI (`create_advanced_campaign`)**  
   Em `_force_uazapi_initial_chunk_no_cadence` e no ramo não-outbox de `_create_campaign_core`, os leads são agrupados em chunks por instância e enviados como **pasta/campanha avançada** com `delay_min_sec`–`delay_max_sec` **aleatórios por mensagem no provedor**. Vários chunks podem rodar **em paralelo** em instâncias diferentes. O horário “Enviado em” reflete a conclusão no provedor, não a linha da UI.

2. **`send_batch` não segue a ordem do CSV**  
   Na criação da campanha (`app.py`), `send_batch` é calculado com `SELECT … ORDER BY id ASC`. O **`id`** nem sempre coincide com a ordem das linhas do CSV pós-validação. A lista na admin usa **`csv_row_order`** (`ORDER BY COALESCE(csv_row_order, id)`), ou seja: **UI = ordem CSV**, **pipeline de envio inicial (limite/chunk) = ordem `id`** → divergência perceptível após importações, mesclas ou edições.

3. **Fila outbox (`USE_MESSAGE_OUTBOX`)**  
   - Enfileiramento: mesma query `ORDER BY COALESCE(send_batch, 999), id`.  
   - Worker (`process_message_outbox_tick`): `ORDER BY o.step_priority ASC, o.queued_at ASC` — para linhas inseridas no mesmo commit, **`queued_at` é quase idêntico**, empates são **não determinísticos** no Postgres.  
   - Depois, **`_pick_round_robin`** escolhe entre candidatos de **instâncias diferentes**, o que pode enviar o lead “da linha 10” antes do “da linha 1” se ambos estão `pending` com o mesmo `next_run_at`.

### Problem Statement

Operadores esperam que o disparo siga **estritamente a ordem das linhas do arquivo CSV gerado na extração/validação**, para poderem **excluir contatos pela edição da campanha** e saber que os envios respeitam essa lista de cima para baixo. Além disso, precisam de **auditoria legível** (humanos e agentes de IA) com **payloads e respostas** das chamadas `send/text` e `send/media`, sem depender só de logs stdout como no fluxo legado MegaAPI.

### Solution

1. **Fonte única de ordenação:** usar **`csv_row_order`** (com fallback **`id`**) em **todas** as seleções de leads para envio inicial, atribuição de **`send_batch`**, enfileiramento outbox e migrações/scripts que hoje usam só `id`.  
2. **Comportamento do worker outbox:** ordenar candidatos por **`(step_priority, csv_row_order, lead_id)`** e **remover ou condicionar** o round-robin quando o objetivo for sequência estrita CSV (ver decisão abaixo). Garantir **`next_run_at` / sequência explícita** para eliminar empates em `queued_at`.  
3. **Legado `create_advanced_campaign`:** documentar que **ordem estrita não é garantida** pelo provedor; a correção “forte” é **envio unitário** (outbox já existente) ou chunk único + uma instância — incluir na spec o caminho recomendado por flag.  
4. **Auditoria JSON por campanha:** persistir **append-only** (ex.: **JSON Lines** `.jsonl`) com um objeto por tentativa de envio: metadados (campaign_id, lead_id, `csv_row_order`, stage, timestamps), **request** (sanitizado), **response**, outcome; expor **download/visualização admin** + **endpoint API** autenticado superadmin.

### Scope

**In scope**

- Alinhar queries e batches a `csv_row_order`.  
- Ajustar worker outbox para dequeue determinístico compatível com “primeiro ao último” na lista CSV.  
- Arquivo(s) de auditoria por campanha + API admin.  
- Testes de regressão em ordenação e um teste de append no arquivo de log.

**Out of scope**

- Alterar o comportamento da API UAZAPI remota (pastas / filas internas deles).  
- Retenção legal/GDPR completa além de sanitização de token e orientação de armazenamento.  
- SSE/WebSocket na UI (polling existente pode ser reutilizado para “última linha do log”).  
- Refatoração grande de `app.py` além dos pontos tocados.

---

## Context for Development

### Codebase patterns (âncoras verificadas)

| Área | Comportamento atual |
|------|---------------------|
| Lista de leads admin | `ORDER BY … COALESCE(csv_row_order, id)` (~`app.py` 3283, 4870, 9084) |
| `send_batch` | Atribuído com `ORDER BY id ASC` (~6329–6336) |
| Leads para chunk / outbox | `ORDER BY COALESCE(send_batch, 999), id` (~6411–6413, ~7376–7378) |
| Worker outbox | `ORDER BY step_priority, queued_at` + `_pick_round_robin` (~298–352 em `worker_message_outbox.py`) |
| Tentativas | `campaign_send_attempts.uazapi_response` truncado; não substitui arquivo dedicado por campanha |

### Files to reference

| File | Purpose |
| ---- | ------- |
| `app.py` | `_create_campaign_core`, `_force_uazapi_initial_chunk_no_cadence`, DDL `campaign_message_outbox`, rotas admin/API |
| `worker_message_outbox.py` | `process_message_outbox_tick`, `_persist_outcome` |
| `worker_cadence.py` | Queries com `send_batch` / follow-up |
| `services/uazapi.py` | `send_text_idempotent`, `send_media_campaign`, `create_advanced_campaign` |
| `scripts/migrate_campaign_to_outbox.py` | Ordem de `to_enqueue` |

### Technical decisions (ADR resumido)

| ID | Decisão |
|----|---------|
| ADR-O1 | **SSOT de ordem:** `csv_row_order`; `id` só como desempate. |
| ADR-O2 | **Exclusão de leads:** mantém ordem relativa dos sobreviventes; buracos em `csv_row_order` são aceitáveis. Novos leads na edição devem receber `csv_row_order` maior que o máximo existente (verificar fluxo de INSERT na edição ~9377+). |
| ADR-O3 | **Round-robin vs ordem:** para cumprir “estrito CSV”, o worker não deve escolher um lead posterior só porque a instância alternou. Opções: (a) desativar round-robin quando `campaigns.rotation_mode == 'single'`; (b) nova flag `outbox_strict_csv_order` em config; (c) sempre FIFO por `csv_row_order` no MVP — **recomendação:** (b) ou (a) documentado para não surpreender operadores multi-instância que dependiam de intercalação. |
| ADR-O4 | **Log JSON:** formato **JSONL** em disco (`storage/{user_id}/campaigns/{campaign_id}/dispatch_audit.jsonl` ou pasta `audit/`); rotação por tamanho opcional fase 2. Sanitizar `apikey`/Authorization; telefone pode permanecer para suporte — alinhar com política interna. |

---

## Implementation Plan

### Tasks

- [ ] **Task 1:** Atribuir `send_batch` usando ordem CSV  
  - File: `app.py`  
  - Action: Na seção 5b (~6329), trocar `ORDER BY id ASC` por `ORDER BY COALESCE(csv_row_order, id) ASC, id ASC` ao listar `pending_ids` antes do `UPDATE send_batch`.

- [ ] **Task 2:** Selecionar leads para outbox e legado chunk com ordem CSV  
  - File: `app.py`  
  - Action: Substituir `ORDER BY COALESCE(send_batch, 999) ASC, id ASC` por `ORDER BY COALESCE(send_batch, 999) ASC, COALESCE(csv_row_order, id) ASC, id ASC` em todas as queries de leads para primeiro disparo (incl. ~6411 e ~7376 e equivalentes em continue-chunk / estágios se aplicável).

- [ ] **Task 3:** Worker outbox — dequeue determinístico  
  - File: `worker_message_outbox.py`  
  - Action: No `SELECT` principal (~298–319), `JOIN campaign_leads cl` já existe; acrescentar na `ORDER BY`: `o.step_priority ASC, COALESCE(cl.csv_row_order, cl.id) ASC, cl.id ASC, o.next_run_at ASC, o.id ASC` (ajustar ordem exata conforme produto: priorizar janela/throttle).  
  - Action: Ao inserir na outbox (`app.py`), definir **`queued_at`** incremental por campanha **ou** introduzir coluna opcional `enqueue_sequence SERIAL`/INTEGER preenchida na aplicação para eliminar empates restantes.

- [ ] **Task 4:** Round-robin compatível com CSV  
  - File: `worker_message_outbox.py`  
  - Action: Implementar decisão ADR-O3 (flag ou `rotation_mode`): se estrito, **`chosen = candidates[0]`** após ordenar `candidates` por `(csv_row_order, lead_id)`; se não estrito, manter round-robin atual.

- [ ] **Task 5:** Worker cadência / scripts  
  - Files: `worker_cadence.py`, `scripts/migrate_campaign_to_outbox.py`, `scripts/debug_uazapi_initial_flow.py`  
  - Action: Onde houver `ORDER BY COALESCE(send_batch…), id`, alinhar com `csv_row_order` como segundo critério após `send_batch`.

- [ ] **Task 6:** Módulo de auditoria JSON  
  - New file sugerido: `utils/campaign_dispatch_audit.py`  
  - Action: Função `append_dispatch_event(campaign_id, user_id, event_dict)` que: sanitiza segredos; faz **append** JSONL com lock de arquivo leve ou escrita via DB queue (preferir arquivo simples na Fase 1); inclui `attempt_no`, `outbox_id`, tipo `text|media`, payload request (campos UAZAPI), response body parseado ou string truncada (limite configurável).

- [ ] **Task 7:** Integrar auditoria nos envios  
  - File: `worker_message_outbox.py`  
  - Action: Após HTTP em `process_message_outbox_tick`, chamar append com request/response (antes de truncar para `campaign_send_attempts`).  
  - File: `app.py` ou worker legado (se ainda criar `create_advanced_campaign`): registrar pelo menos **criação de pasta** + lista de `lead_ids` na ordem enviada (nível campanha); envios unitários futuros cobrem o detalhe completo.

- [ ] **Task 8:** API + Admin + UI
  - File: `app.py`  
  - Action: `GET /api/admin/campaigns/<int:campaign_id>/dispatch-audit` (superadmin + flag); `GET /api/admin/users/<user_id>/campaigns-active` (campanhas `running`/`pending` para o dropdown); `GET /admin/dispatch-audit` (página com dois seletores + botões carregar/baixar).  
  - Action: Na subida do app, `os.makedirs` no diretório absoluto de `STORAGE_DIR` / `storage`.
  - Files: `templates/admin/dispatch_audit.html`, links em `templates/admin/dashboard.html` e `templates/admin/campaigns.html`.

- [ ] **Task 9:** Testes  
  - Files: `tests/test_outbox_spec_acceptance.py`, `tests/test_admin_campaign_crud.py`  
  - Action: Teste que cria 3 leads com `csv_row_order` invertido vs `id` e verifica ordem da query de enqueue e ordem do `SELECT` do worker (mock DB ou integração com transação).  
  - Action: Teste de append JSONL (tempdir) com sanitização de token.

### Acceptance Criteria

- [ ] **AC1:** Dado uma campanha com três leads onde `csv_row_order` é `(3,1,2)` e `id` crescente diferente, quando o sistema monta o lote inicial para envio/outbox, então a ordem de processamento é **1 → 2 → 3** segundo `csv_row_order`, não segundo `id`.

- [ ] **AC2:** Dado operador que remove linhas intermediárias na edição da campanha, quando os leads restantes são disparados, então a ordem relativa entre os sobreviventes segue **`csv_row_order` crescente** (buracos numéricos permitidos).

- [ ] **AC3:** Dado `USE_MESSAGE_OUTBOX=1` e modo estrito CSV habilitado (conforme ADR-O3), quando duas linhas `pending` pertencem à mesma campanha com `csv_row_order` distintos e ambas elegíveis, então **a menor `csv_row_order` é sempre escolhida antes** da maior, independentemente da instância.

- [ ] **AC4:** Dado um envio `send_text` bem-sucedido pelo worker, quando o fluxo completa, então existe **nova linha** no JSONL da campanha com `outcome`, `latency_ms`, corpo da resposta (ou truncado) e **sem token de API em claro**.

- [ ] **AC5:** Dado superadmin autenticado, quando chama `GET …/dispatch-audit`, então recebe os eventos da campanha com código HTTP 200; dado usuário não admin, então 403.

- [ ] **AC6:** Dado campanha usando apenas legado `create_advanced_campaign`, quando o produto está documentado como “sem ordem estrita no provedor”, então a UI ou doc admin **alerta** que a ordem cronológica pode divergir da lista até migração para outbox/unitário.

---

## Additional Context

### Dependencies

- Coluna `campaign_leads.csv_row_order` populada na criação/import; garantir migração/backfill para leads antigos (`UPDATE` por `ROW_NUMBER` já existe parcialmente em `init_db` ~787).

### Testing Strategy

- Unitário: ordenação SQL construída via fixture de leads.  
- Integração reduzida: transação + rollback com Postgres de teste se disponível.  
- Manual: campanha com 13 linhas como no screenshot; confirmar que timestamps **não invertem** a ordem da lista quando em modo outbox estrito.

### Notes / riscos

- **Multi-instância + estrito CSV:** pode reduzir paralelismo real; compensar com operação sequencial por campanha (aceitável para requisito atual).  
- **Volume do JSONL:** monitorar tamanho; rotação futura.  
- **PII:** telefone/nome no log facilitam suporte mas aumentam sensibilidade — combinar com política de retenção.

---

## Quick Summary

- **9 tasks** focadas em `app.py`, `worker_message_outbox.py`, util novo de auditoria e testes.  
- **6 acceptance criteria** cobrindo ordem CSV, edição/exclusão, outbox estrito, JSONL sanitizado e RBAC admin.

---

## Registro de implementação (código — 2026-05-01)

- **Ordem CSV:** `send_batch` e seleções de leads / outbox inicial em `app.py` usam `ORDER BY … COALESCE(csv_row_order, id) …`; worker outbox ordena por `step_priority` + `csv_row_order` + id do lead; round-robin removido — usa-se o primeiro candidato após ordenação.
- **Follow-up na outbox:** `enqueue_missing_cadence_outbox_rows` em `worker_message_outbox.py` — quando `enable_cadence`, lead em `snoozed` com `snooze_until <= NOW()` e etapa configurada em `campaign_steps`, enfileira `follow1` / `follow2` / `breakup` na ordem `csv_row_order`, com `step_priority` 1–3 (inicial permanece 0).
- **Legado cadência:** `worker_cadence.py` não chama `process_campaign_sends` se existir linha em `campaign_message_outbox` para a campanha e `USE_MESSAGE_OUTBOX`; query legacy de follow ordena por `csv_row_order`.
### UI Admin — consulta auditoria

- Rota de página: ``GET /admin/dispatch-audit`` (admin logado + superadmin + ``USE_MESSAGE_OUTBOX``).
- Componentes: seletor de usuário (API existente ``GET /api/admin/users/list``), seletor de campanha **ativa** do usuário (``GET /api/admin/users/<user_id>/campaigns-active`` — apenas ``running`` e ``pending``), campo ``tail``, botão **Carregar auditoria** (JSON formatado) e **Baixar NDJSON** (abre ``dispatch-audit?format=ndjson``).
- Links no painel: dashboard admin e lista de campanhas.

### Storage no deploy

- Na importação do ``app``: ``os.makedirs(os.path.abspath(STORAGE_ROOT), exist_ok=True)`` com ``STORAGE_DIR`` opcional (default ``storage``).
- Caminho do JSONL: ``utils/campaign_dispatch_audit.dispatch_audit_jsonl_path`` usa o mesmo ``STORAGE_DIR``; escrita cria subpastas sob demanda.

