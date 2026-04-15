---
title: 'UAZAPI: find + reconciliação antes de follow/retomada e classificação correta de leads'
slug: 'uazapi-find-reconciliacao-follow-retomada-classificacao'
created: '2026-04-15'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4, 5]
bmad_workflow: 'bmm/quick-spec'
bmad_workflow_file: '_bmad/bmm/workflows/bmad-quick-flow/quick-spec/workflow.md'
product_rules_source: '_bmad-output/implementation-artifacts/product-rules-reconciliacao-uazapi-find-retomada.md'
wip_renamed_to: 'tech-spec-uazapi-find-reconciliacao-follow-retomada-classificacao.md'
tech_stack: ['Python', 'Flask', 'PostgreSQL', 'RQ/worker_cadence', 'UAZAPI HTTP', 'Jinja2/JS (Kanban + edit campanha)']
files_to_modify:
  - 'utils/sync_uazapi.py'
  - 'worker_cadence.py (`_materialize_scheduled_stage_sends`, `schedule_next_initial_chunk`, `process_uazapi_initial_stage_rollovers`, `process_rollover_fu_next`)'
  - 'app.py (`campaign_kanban_data`, `GET /api/campaigns/<id>/leads`, `GET /api/admin/campaigns/<id>/leads`; rotas que chamam materialize/sync)'
  - 'services/uazapi.py (`message_find` — limit/offset)'
  - 'tests/test_sync_uazapi.py'
  - 'tests/test_worker_cadence_initial_chunk.py (se existir; ou novo teste de rollover)'
code_patterns:
  - 'campaign_stage_sends + listfolders para agregados e status da pasta'
  - 'message_find (POST) por chat com send_folder_id como evidência por lead'
  - 'Não usar list_messages para SSOT de lead; alinhar com product-rules-reconciliacao-uazapi-find-retomada.md'
  - 'Rollover/FU: filtros em campaign_leads.status + current_step'
test_patterns:
  - 'pytest tests/test_sync_uazapi.py'
  - 'Testes de integração leves com mocks de UazapiService.message_find e list_folders'
---

# Tech-Spec: UAZAPI — `message_find`, reconciliação, follow-up, retomada e classificação de leads

**Criado:** 2026-04-15 · **Idioma:** pt-BR · **Regras de produto:** `_bmad-output/implementation-artifacts/product-rules-reconciliacao-uazapi-find-retomada.md` · **Cruzamento:** `tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md`

## BMAD quick-spec — rastreabilidade do workflow

Execução alinhada a `_bmad/bmm/workflows/bmad-quick-flow/quick-spec/workflow.md` e aos passos em `_bmad/bmm/workflows/bmad-quick-flow/quick-spec/steps/`:

| Passo | Ficheiro de instrução | Resultado neste artefato |
|-------|------------------------|---------------------------|
| 1 | `step-01-understand.md` | Delta requisito/código, problema, solução, âmbito; WIP inicializado a partir de `tech-spec-template.md` (campos YAML + Overview). |
| 2 | `step-02-investigate.md` | Secção **Context for Development** (padrões, ficheiros, decisões técnicas); `tech_stack`, `files_to_modify`, `code_patterns`, `test_patterns`. |
| 3 | `step-03-generate.md` | **Implementation Plan** (tasks), **Acceptance Criteria** (Given/When/Then), **Additional Context**. |
| 4 | `step-04-review.md` | `status: ready-for-dev`, `stepsCompleted: [1, 2, 3, 4]`; rename `tech-spec-wip.md` → `tech-spec-{slug}.md` (slug no frontmatter). |

*Os menus intermédios do workflow ([A] Advanced Elicitation, [P] Party Mode, [C] Continue) foram consolidados numa única sessão após confirmação do utilizador para concluir o quick-spec.*

---

## Adversarial review (F1–F14) — respostas e patches da spec

Esta secção **fecha** os achados da revisão adversarial com decisões verificadas na codebase (2026-04-15).

| ID | Patch na spec / produto |
|----|-------------------------|
| **F1** | **Conflito `UAZAPI_MESSAGE_FIND=0` vs D1:** Em produção para campanhas `campaign_stage_sends` + evidência por lead, **`UAZAPI_MESSAGE_FIND` deve permanecer ativo (default `1`)**. Se `0`, o código **não** pode promover `sent` nem desbloquear rollover só com find: registar evento estruturado `uazapi_message_find_disabled_blocking_reconcile` (`campaign_id`, `send_id`) e manter leads em `pending` até reativar find **ou** operador aceitar modo degradado explícito (ver F11). Documentar no README/env: *find off = reconciliação por lead desligada*. |
| **F2** | **`message_find` e janela de histórico:** `UazapiService.message_find` (`services/uazapi.py`) aceita `limit` 1–200 e `offset`. Hoje `reconcile_leads_via_message_find` chama com `limit=50`, `offset=0` apenas. **Spec:** introduzir env `UAZAPI_MESSAGE_FIND_LIMIT` (default `100`, máx `200`) e, se nenhuma mensagem casar `send_folder_id`, **opcional** segunda chamada com `offset=limit` (e terceira até cap configurável `UAZAPI_MESSAGE_FIND_MAX_PAGES`, default `2`) antes de concluir “find negativo”. Logar `message_find_pages_used` no evento de observabilidade. |
| **F3** | **Estado `failed` no lead e loops de find:** Alinhar às regras de produto §5: após find **negativo** para o `folder_id` do send, aplicar política **uma vez** (`pending` para retry de chunk vs `failed` terminal). Leads já `failed` **com** `last_sent_folder_id` = folder atual e sem match após páginas F2 → **não** reentrar em find infinito; leads `failed` **sem** evidência de tentativa neste folder (ex.: só agregado) mantêm-se candidatos. Documentar SQL em Task 1 com esta partição. |
| **F4** | **AC3 e “timeout”:** Na **v1** da implementação, **não** introduzir exceção de timeout genérica; remover a cláusula vaga “exceto política de timeout” dos AC e substituir por: *se após N ciclos de sync (configurável, ex. 20) o send continua `done` na API e ainda há `pending` no escopo com find negativo em todas as páginas F2, marcar como `failed` terminal ou manter `pending` conforme §5 do product-rules — decisão única documentada no código*. Isto mantém AC testável. |
| **F5** | **Concorrência:** O worker de cadência é tipicamente um processo RQ; risco residual = dois workers. **Spec:** antes de `UPDATE campaign_stage_sends SET fu_rollover_done=TRUE` ou mover leads em rollover, usar transação com `SELECT … FROM campaign_stage_sends WHERE id=%s FOR UPDATE` (ou `FOR UPDATE SKIP LOCKED` + retry) no mesmo `conn` já usado pelo worker. Documentar que `process_uazapi_initial_stage_rollovers` deve ser **idempotente** se reexecutado (mesmos leads não duplicam FU). |
| **F6** | **Materialização do próximo chunk:** Confirmado na codebase — **`worker_cadence._materialize_scheduled_stage_sends`** (≈ L382+) chama `create_advanced_campaign`; **`schedule_next_initial_chunk`** (≈ L1157+) agenda linhas `scheduled`; **`app.py`** (≈ L6109 com `wc._materialize_scheduled_stage_sends`, ≈ L6709 `create_advanced_campaign`) para fluxos UI/admin. Task 6 deve citar estes três âncoras. |
| **F7** | **Exclusão explícita do probe órfão:** Task 4 (`UAZAPI_SYNC_RECONCILE_LISTMESSAGES`) aplica-se **apenas** a ramos que usam `list_messages` para **enumeração Sent/Failed** e marcação de `campaign_leads`. **Não** desliga o **probe** `list_messages(Scheduled, page=1)` usado quando a pasta **não** aparece em `listfolders` (deteção órfã — `tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md`). Adicionar linha explícita na Task 4. |
| **F8** | **Matching telefone:** `reconcile_leads_via_message_find` já usa `phone` ou `whatsapp_link` com `_normalize_phone_for_api`. **Spec:** se `whatsapp_link` normalizado ≠ `phone` normalizado, **tentar find com o chatid derivado de cada** (máx 2 chamadas por lead por ciclo) e documentar em Task 1. AC dedicado abaixo. |
| **F9** | **Agregados `succ+fail` vs evidência:** Se após passo de find o número de leads `sent` confirmados **exceder** `success_count` persistido do send, **atualizar** `success_count` no `campaign_stage_sends` para `min(planned_count, count(sent confirmados))` antes de avaliar `succ+fail>=planned` para rollover — ou exigir que **todos** os `lead_ids` estejam em estado terminal (`sent`/`failed`/retry `pending` explícito) **em vez de** confiar só no agregado da API. Escolha **recomendada na spec:** gate de rollover = `(todos lead_ids terminais conforme D3/D4) OU (API done + find completo + contagem coerente)`; ajustar texto em User story 1 e Fluxo (i). |
| **F10** | **Precedência de evidências:** Tabela: (1) `message_find` com `send_folder_id` do send; (2) webhooks/ACK futuros documentados; (3) **nunca** `list_messages` Sent/Failed para `sent` de lead neste fluxo. D1 passa a referenciar esta ordem. |
| **F11** | **Ordem de implementação:** Num **único PR** ou com flag **`UAZAPI_LEAD_RECONCILE_V2=1`**: (a) ativar caminho find-orquestrador + candidatos; (b) desativar prefixo `listfolders` para leads; (c) default `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0`. Enquanto `V2=0`, comportamento legado inalterado. Evita janela “sem list_messages e sem find alargado”. |
| **F12** | **Task 9 concreta:** Substituir “provavelmente app.py” por: `GET /api/campaigns/<campaign_id>/kanban-data` (sync opcional antes de retornar — já existe), `GET /api/campaigns/<campaign_id>/leads`, `GET /api/admin/campaigns/<campaign_id>/leads` — todos leem `campaign_leads` da BD; confirmar que **não** recalculam `sent` a partir de `listfolders`. |
| **F13** | **SLO:** NFR documental — primeiro ciclo de find para **até 30 leads** por send (limite típico de chunk em `_materialize`) com `UAZAPI_MESSAGE_FIND_SLEEP_SEC=0.05` e 1 página: ordem de grandeza **&lt; ~90s** API-permitindo; acima disso, find continua no **próximo** tick de sync (10 min) com **idempotência**. AC opcional: não bloquear request HTTP do utilizador — sync pesado já ocorre em worker/kanban-data em background conforme código actual. |
| **F14** | **Persistência a meio do find:** **v1 sem migração** — find por send corre dentro da mesma transação que atualiza leads **ou** em batches commitados por lead com reexecução segura (UPDATE idempotente). Se o processo morrer a meio, o próximo sync **repete** find só para candidatos ainda `pending` (barato). Opcional backlog: coluna `last_reconcile_find_at` no send para métricas. |

---

## Overview

### Problem Statement

1. **Follow-up e próximas etapas** usam `campaign_leads.status` / `current_step`, mas o sync atual pode marcar como `sent` os **primeiros N** `lead_ids` com base só em `log_sucess` de `listfolders` (`_sync_folder_via_listfolders`), **sem** evidência por destinatário — gera falso “enviado”, follow para quem não recebeu a inicial (e o inverso: pendente quando já houve entrega).
2. **`message_find`** existe e reconcilia por chat + `send_folder_id`, mas só corre quando `_should_reconcile_via_message_find` (pasta final e ≥ ~80% do planejado, ou `log_success > planned`) — **fora** disso, leads ficam sem evidência.
3. O ramo **`needs_reconcile`** em `sync_campaign_leads_from_uazapi` ainda usa `fetch_all_phones_by_status` / `list_messages` — as regras de produto **proíbem** usar `list_messages` para marcar leads, reconciliar ou provar follow/retomada.
4. **Desconexão / reconexão** WhatsApp: ao retomar, é preciso **find no escopo completo** do `campaign_stage_sends.lead_ids` pendente/não confirmado **antes** de novo `create_advanced_campaign`, próximo chunk ou rollover — senão há risco de **duplicidade** ou **omissão**.
5. **Kanban** (`templates/campaigns_kanban.html`: `current_step`, `cadence_status` / `status`) e **aba editar campanha** (`templates/campaigns_edit.html`, `templates/admin/campaigns_edit.html`: labels Pendente/Enviado a partir de `lead.status`) refletem o mesmo `campaign_leads` — a “perfeição” do **sent** depende do backend passar a gravar `sent` só com evidência coerente com a etapa.

### Solution (alto nível)

1. **SSOT por lead para “recebeu mensagem da etapa X”:** passar a tratar **`message_find`** como **prova primária** para promover `campaign_leads` a `sent` (e `current_step` / `last_sent_*` coerentes com a etapa do send), **alinhado** ao `send_folder_id` do chunk; precedência completa em **F10**. Requer `UAZAPI_MESSAGE_FIND` ativo em produção (ver **F1** / D7).
2. **`listfolders`:** manter apenas para **agregados** (`success_count` / `failed_count` / `status` do `campaign_stage_sends`) e estado da pasta — **não** para atribuir “quem é o N-ésimo enviado” na lista de leads.
3. **Gatilhos obrigatórios de find** (escopo = todos os `lead_ids` do send que estejam **pendentes de confirmação** para aquele envio/etapa, conforme §3 das regras de produto): **antes** de (a) novo `create_advanced_campaign` em retomada, (b) rollover / avanço de etapa (incl. `process_uazapi_initial_stage_rollovers` e cadeia FU), (c) marcar send “fechado” para efeito de FU (`fu_rollover_done`).
4. **Desligar por padrão** ramos que marcam leads via `list_messages` (`UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` default ou remoção controlada), substituindo lacunas por **`message_find` em lote** no escopo do send.
5. **Classificação na UI:** sem mudança obrigatória de contrato de API se `campaign_leads.status` / `current_step` forem corrigidos na origem; validar endpoints que alimentam Kanban e tabela de leads.

### Scope

**In scope**

- Refatoração da lógica em `utils/sync_uazapi.py`: critérios de quando rodar `reconcile_leads_via_message_find`; fim da promoção `sent` só por prefixo de `lead_ids` + `log_sucess`; substituição do pente-fino `needs_reconcile` baseado em `list_messages` por find + política de falha documentada.
- `worker_cadence.py`: garantir sync + reconciliação completa no escopo antes de rollover inicial→FU1 e, por simetria de produto, revisar pontos que avançam FU1→FU2→Despedida para **não** depender de evidência frágil.
- Testes em `tests/test_sync_uazapi.py` (e complementares se necessário).
- Logs estruturados mínimos (event, `campaign_id`, `send_id`, `folder_id`, contagens de escopo find).

**Out of scope**

- Migração total para envio unitário só pela VPS.
- Webhooks UAZAPI (fase posterior).
- Redesign de UI do Kanban (só garantir consistência dos dados).

---

## Context for Development

### Codebase Patterns

- Sync principal: `sync_campaign_leads_from_uazapi` em `utils/sync_uazapi.py`, iterando `campaign_stage_sends` com `uazapi_folder_id` em estados `scheduled` / `running` / `partial` / `done`.
- Marcação agregada por posição hoje: `_sync_folder_via_listfolders` (primeiros `min(log_success, len(lead_ids))`).
- Find hoje: `reconcile_leads_via_message_find` + gate `_should_reconcile_via_message_find`.
- Rollover UAZAPI inicial: `process_uazapi_initial_stage_rollovers` — só move leads com `current_step = 1` **e** `status = 'sent'` dentro de `lead_ids` do send, quando `succ + fail >= planned` no registro do send.
- Rollover legado por horário: `process_rollover` ainda usa `list_messages` / `get_uazapi_campaign_counts` — documentar se continua só para campanhas **sem** `campaign_stage_sends`; não misturar com fluxo `use_uazapi_sender` + stage sends.

### Files to Reference

| Ficheiro | Função |
| -------- | ------ |
| `utils/sync_uazapi.py` | `_sync_folder_via_listfolders`, `reconcile_leads_via_message_find`, `_should_reconcile_via_message_find`, `needs_reconcile` + `fetch_all_phones_by_status`, `sync_campaign_leads_from_uazapi` |
| `worker_cadence.py` | `process_uazapi_initial_stage_rollovers`, `process_rollover_fu_next`, `schedule_next_initial_chunk` / materialização de chunks (procurar chamadas a sync e criação de `create_advanced_campaign`) |
| `services/uazapi.py` | `message_find`, `list_folders`, `create_advanced_campaign` |
| `templates/campaigns_kanban.html` | Colunas por `current_step` / estado do lead |
| `templates/campaigns_edit.html`, `templates/admin/campaigns_edit.html` | Tabela de leads: `pending` / `sent` / `failed` |
| `tests/test_sync_uazapi.py` | Padrões de mock para folders e sync |

### Technical Decisions

| ID | Decisão |
|----|---------|
| D1 | **Promover `sent` por lead** com evidência na ordem **F10**: (1) `message_find` positivo (`send_folder_id` = folder do send); (2) webhooks/ACK futuros; (3) nunca `list_messages` Sent/Failed neste fluxo. Aplica-se a `create_advanced_campaign` + `campaign_stage_sends`. |
| D2 | **`listfolders`:** atualizar só contagens e `status` do send; **não** chamar `_sync_folder_via_listfolders` para marcar leads (ou manter chamada **desligada** por default via flag até remoção). |
| D3 | **`list_messages`:** default **off** para marcação/reconcile de leads (`UAZAPI_SYNC_RECONCILE_LISTMESSAGES`, default `0`); código legado atrás de flag `1` só para suporte emergencial, com comentário de deprecação. |
| D4 | **Escopo do find:** todos os leads em `lead_ids` do send com `status` `pending` ou `failed` **ainda** a reconciliar para **aquela** etapa (`last_sent_stage` / `current_step` guards já usados em updates) **antes** de avanços listados nas regras de produto §3. |
| D5 | **Rate limit / tempo:** manter `UAZAPI_MESSAGE_FIND_SLEEP_SEC`; opcional `UAZAPI_MESSAGE_FIND_MAX_CONCURRENT` (backlog) se necessário; documentar trade-off. |
| D6 | **Reconexão:** no próximo ciclo de sync com API OK, executar o mesmo pipeline de find no escopo pendente; **não** criar novo chunk nem `fu_rollover_done` até find concluir para candidatos definidos em D4. |
| D7 | **`UAZAPI_MESSAGE_FIND=0`:** tratado como **modo incompatível** com D1 para stage sends — ver tabela F1; obrigatório log estruturado e documentação de env. |
| D8 | **Janela `message_find`:** usar `UAZAPI_MESSAGE_FIND_LIMIT` + paginação opcional (`offset`, `UAZAPI_MESSAGE_FIND_MAX_PAGES`) conforme F2 e `services/uazapi.py` (cap 200). |
| D9 | **Rollover / gates:** preferir “todos os `lead_ids` em estado terminal **ou** find esgotado conforme F3/F4” em conjunto com coerência de contagens (F9); não depender só de `succ+fail>=planned` da API se isso divergir dos leads confirmados. |
| D10 | **Concorrência:** `FOR UPDATE` (ou equivalente) no send antes de `fu_rollover_done` / movimentação FU; rollover idempotente. |
| D11 | **Rollout:** flag `UAZAPI_LEAD_RECONCILE_V2` para ativar bloco find+flags num único deploy (F11). |

---

## User stories

1. **Como** operador de campanha **quero** que só leads que **realmente receberam** a mensagem inicial avancem para Follow-up 1 **para que** o funil reflita a realidade do WhatsApp.
2. **Como** operador **quero** que a mesma lógica valha para FU1, FU2 e Despedida **para que** nenhuma etapa avance sem evidência da etapa anterior.
3. **Como** operador **quero** que, após queda de sessão e reconexão, a campanha **continue de onde parou** sem reenviar a quem já recebeu nem abandonar quem ainda está pendente **para que** a operação seja segura.
4. **Como** operador **quero** ver no Kanban e em “editar campanha” o estado **Pendente / Enviado** correto **para que** eu confie na lista de leads.

---

## Fluxos (comportamento alvo)

### (i) Pasta `done`, tudo coerente

- **When** `listfolders` indica pasta terminal com agregados alinhados ao `planned_count`.
- **Then** o send atualiza `success_count` / `failed_count` / `status` a partir de `listfolders`.
- **And** para cada lead ainda não `sent` confirmado no escopo do send, roda-se `message_find`; positivos → `sent` + metadados de envio; negativos com política de falha → `failed` ou permanecem `pending` para retry conforme regras §5 do doc de produto.
- **And** só então pode rodar rollover / FU, com gate **D9** (estado terminal por lead e/ou contagens coerentes com find, não só agregado API).

### (ii) `partial` / restrição no meio do disparo

- **When** pasta em `partial` ou agregados &lt; `planned` com leads ainda pendentes.
- **Then** **não** promover leads por ordem em `lead_ids` usando só `log_sucess`.
- **And** find no subconjunto pendente sempre que o worker for retomar envio ou fechar chunk.

### (iii) Pasta com `log_failed` &gt; 0 (agregado)

- **When** `listfolders` reporta falhas.
- **Then** **não** assumir que todos falharam; find por lead antes de marcar `failed` ou `pending` retry.
- **And** find positivo → `sent` (não reenviar inicial).

### (iv) Reconexão da instância WhatsApp

- **When** instância volta a `connected` e o worker roda sync / cadência.
- **Then** executar find no escopo §3 do doc de produto para sends **incompletos** ou **ambíguos** antes de `create_advanced_campaign` de retomada ou próximo chunk.
- **And** agregados continuam a vir de `listfolders` apenas.

### (v) Pasta órfã / sumida de `listfolders`

- Manter comportamento já especificado em `tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md` (probe mínimo, `failed` no send quando aplicável); **leads** não devem ser mass-marcados como `sent` por agregado enganoso.

---

## Implementation Plan

### Tasks (ordenadas por dependência)

- [x] **Task 1 — Modelo de “candidato a find”**  
  - File: `utils/sync_uazapi.py`  
  - Action: Extrair função (ex.: `_lead_ids_needing_message_find(conn, campaign_id, send_row, folder_id)`) que devolve subconjunto de `lead_ids` com: incluídos no send, não removidos do funil, cadência ativa, e `status` / `last_sent_folder_id` / `last_sent_stage` coerentes com “ainda não confirmado para **este** `folder_id`/etapa”, **incluindo regras F3** (evitar re-find infinito em `failed` já esgotado para este folder). Documentar SQL e invariantes.  
  - Notes: Reutilizar padrões de `_cadence_stage_sql_guard` / guards já usados nos `UPDATE` existentes. **F8:** em `reconcile_leads_via_message_find`, se `phone` e `whatsapp_link` normalizam para JIDs diferentes, tentar ambos.

- [x] **Task 2 — Parar de marcar `sent` pelos primeiros N da lista**  
  - File: `utils/sync_uazapi.py`  
  - Action: Remover ou esconder atrás de flag **default off** (ex. `UAZAPI_LISTFOLDERS_PREFIX_SENT=0`) a chamada a `_sync_folder_via_listfolders` que faz `UPDATE ... status=sent` por ordem em `lead_ids`. Garantir que `listfolders` ainda alimenta apenas `campaign_stage_sends` (`success_count`, `failed_count`, `status` normalizado).  
  - Notes: Se flag legacy `1` for necessária temporariamente para rollback, logar aviso uma vez por send.

- [x] **Task 3 — Find obrigatório no escopo antes de decisões de avanço**  
  - File: `utils/sync_uazapi.py`  
  - Action: Introduzir função orquestradora (ex. `reconcile_send_leads_via_message_find_for_scope(...)`) chamada desde `sync_campaign_leads_from_uazapi` sempre que: pasta em `done`/`partial`/`failed` com pendentes; **ou** send em `running` com `log_success>0` e leads ainda `pending` (reconexão); respeitar `UAZAPI_MESSAGE_FIND`. Implementar paginação/limite conforme **F2** (`UAZAPI_MESSAGE_FIND_LIMIT`, `UAZAPI_MESSAGE_FIND_MAX_PAGES`) usando a API existente em `services/uazapi.py` (`limit`≤200, `offset`).  
  - Action: Relaxar/remover a restrição de `_should_reconcile_via_message_find` para estes casos **ou** substituir por regra: “se existe candidato D4 → find”. Manter throttle via env.  
  - Notes: Log JSON uma linha: `event`, `campaign_id`, `send_id`, `folder_id`, `find_scope_count`, `find_positive_count`, `find_negative_count`, `message_find_pages_used` (§7 product-rules + F2).

- [x] **Task 3b — Flag de rollout única (antes de cortar list_messages)**  
  - File: `utils/sync_uazapi.py` (+ env sample se existir)  
  - Action: Introduzir `UAZAPI_LEAD_RECONCILE_V2` (default `0` até deploy completo; `1` ativa find-orquestrador + desativa prefixo listfolders + default `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` em conjunto). Ver **F11**.

- [x] **Task 4 — Desligar `list_messages` na reconciliação de leads**  
  - File: `utils/sync_uazapi.py`  
  - Action: Env `UAZAPI_SYNC_RECONCILE_LISTMESSAGES` default **`0`** quando `UAZAPI_LEAD_RECONCILE_V2=1`: ramo `needs_reconcile` que chama `fetch_all_phones_by_status` / `_reconcile_send_by_messages` / `_reconcile_stage_by_messages` **não** corre para marcação de lead; quando `1`, preservar comportamento antigo com comentário `DEPRECATED`.  
  - Notes: Quando flag `0`, lacunas “done com menos updates que log_success” devem ser resolvidas por Task 3, não por Sent/Failed em massa. **Exclusão F7:** **não** alterar o probe `list_messages(Scheduled, page=1)` usado só para deteção de pasta órfã quando ausente de `listfolders`.

- [x] **Task 5 — Rollover inicial → FU1 alinhado à evidência**  
  - File: `worker_cadence.py`  
  - Action: Imediatamente antes de selecionar `rollover_leads` em `process_uazapi_initial_stage_rollovers`, chamar `sync_campaign_leads_from_uazapi` (já importado noutros sítios) **ou** função dedicada que force find no escopo do send, garantindo que só `status='sent'` reflere find positivo para a pasta inicial. Aplicar **D10**: `SELECT … FROM campaign_stage_sends WHERE id=%s FOR UPDATE` antes de `fu_rollover_done` e updates em massa de leads.  
  - Action: Opcional env `UAZAPI_RECONCILE_FIND_BEFORE_ROLLOVER=1` (default `1`) para facilitar rollout gradual.  
  - Notes: Comentário no código: “Nunca promover FU só com log_sucess + ordem” (product-rules §5.3). Ajustar gate de elegibilidade conforme **D9/F9** (não só `succ+fail>=planned`). **Implementado:** `sync` + reload do send; `should_block_initial_rollover_for_pending_find` (V2); `FOR UPDATE` nos commits de `fu_rollover_done`/leads.

- [x] **Task 6 — Retomada de chunk / `create_advanced_campaign`**  
  - File: `worker_cadence.py` — **`_materialize_scheduled_stage_sends`** (criação de folder + `create_advanced_campaign`, ≈ L382+); **`schedule_next_initial_chunk`** (≈ L1157+); `app.py` (ex.: chamada a `_materialize_scheduled_stage_sends` ~L6109, `create_advanced_campaign` ~L6709).  
  - Action: Antes de criar novo send com leads que já estiveram em send incompleto da mesma etapa, garantir Task 3 concluída para o send antigo (mesmo `campaign_id` + `stage`).  
  - Notes: Cruzar com exclusão SQL já existente em `_materialize` (`lead_ids` em chunks `done`/`running`/`partial`) e garantir que **find** cobriu ambiguidade antes de novo folder. **Implementado:** `utils/sync_uazapi.sync_campaign_stage_sends_before_new_chunk` (gate `UAZAPI_RECONCILE_FIND_BEFORE_CHUNK`, pré-check EXISTS por `stage`); chamado em `_materialize` (por grupo), `schedule_next_initial_chunk` e `_create_stage_campaign` (envio imediato FU). O fluxo **continue-initial** em `app.py` reutiliza `_materialize_scheduled_stage_sends` (sem segundo pré-sync no app).

- [x] **Task 7 — Cadeia FU1 → FU2 → Despedida**  
  - File: `worker_cadence.py`  
  - Action: Onde hoje se assume `status=sent` + `snooze_until` para avançar, garantir que o send da etapa anterior foi reconciliado via sync (find), espelhando a política da inicial. Documentar no código o `stage` (`follow1`, `follow2`, `breakup`) por send.

- [x] **Task 8 — Testes**  
  - File: `tests/test_sync_uazapi.py`  
  - Action: Casos: (1) `log_sucess=2`, `lead_ids=[A,B]`, só `B` com find positivo → apenas `B` `sent`. (2) Flag `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` → não chamar mocks de `list_messages` para reconcile. (3) Pasta `partial` + reconexão simulada → find chamado para pendentes.  
  - Notes: Mock `message_find` para devolver mensagens com/sem `send_folder_id` esperado.

- [x] **Task 9 — Verificação UI / API**  
  - Files: `app.py` — rotas **`GET /api/campaigns/<campaign_id>/kanban-data`** (função `campaign_kanban_data`), **`GET /api/campaigns/<campaign_id>/leads`**, **`GET /api/admin/campaigns/<campaign_id>/leads`**.  
  - Action: Confirmar que não há segunda fonte que recalcule “sent” por agregado `listfolders`; leitura deve refletir só `campaign_leads` após sync.  
  - Notes: `kanban-data` já pode chamar `sync_campaign_leads_from_uazapi` antes de responder — garantir que não há override de `status` por contadores UAZAPI no JSON. **Verificado 2026-04-15:** `leads[]` só vem de `SELECT` em `campaign_leads`; `uazapi_stats` / `stage_progress` são metadados agregados e não sobrescrevem `status` por lead; docstrings comentam o contrato (F12).

### Acceptance Criteria (Given / When / Then)

- [ ] **AC1:** Dado um `campaign_stage_sends` com `lead_ids` ordenados e `listfolders.log_sucess = k`, quando o sync corre **sem** find positivo para os primeiros k IDs mas **com** find positivo para outros k IDs do mesmo send, então **apenas** os leads com find positivo ficam `status=sent` e `last_sent_folder_id` igual ao folder do send.
- [ ] **AC2:** Dado `UAZAPI_SYNC_RECONCILE_LISTMESSAGES=0` (default), quando a pasta está `done` com lacunas entre agregado e leads no DB, então **não** se chama `list_messages` para enumerar Sent/Failed para marcação de leads e a lacuna é tratada via `message_find` (ou permanece `pending` com log explícito).
- [ ] **AC3:** Dado send inicial com agregados na BD que sugerem conclusão (`succ + fail >= planned`) mas ainda há leads em `lead_ids` com `pending` sem find negativo/positivo **esgotado** (todas as páginas F2), quando `process_uazapi_initial_stage_rollovers` corre, então **não** marca `fu_rollover_done` nem move leads para FU1 até o find cobrir o conjunto de candidatos definido em D4/F3 **ou** até política F4 aplicar (N ciclos de sync sem alteração → terminal `failed`/`pending` documentado).
- [ ] **AC4:** Dado lead marcado `failed` por agregado mas find positivo para o `folder_id` do send, quando corre reconciliação, então o lead passa a `sent` e **não** entra em retry da mesma mensagem.
- [ ] **AC5:** Dado instância desconectada e send `running`/`partial`, quando a instância volta e o worker executa sync, então reruns de find para pendentes ocorrem **antes** de qualquer novo `create_advanced_campaign` de retomada para os mesmos leads na mesma etapa.
- [ ] **AC6:** Dado lead com `sent` confirmado na etapa inicial (`current_step` e `last_sent_stage` coerentes), quando o utilizador abre o Kanban ou a lista na edição da campanha, então o cartão / linha mostra estado **Enviado** (e coluna correta), alinhado à BD.
- [ ] **AC7:** Dado lead sem evidência de envio na etapa corrente, quando o utilizador consulta a mesma UI, então permanece **Pendente** (ou **Falhou** se política terminal), e **não** aparece como Enviado só por causa de `log_sucess`.
- [ ] **AC8:** Dado lead com `phone` e `whatsapp_link` que normalizam para chatids distintos, quando corre `reconcile_leads_via_message_find`, então o sistema tenta **ambos** os chatids (até 2 chamadas `message_find` por lead por ciclo) antes de concluir find negativo.
- [ ] **AC9:** Dado `UAZAPI_MESSAGE_FIND=0` e `UAZAPI_LEAD_RECONCILE_V2=1`, quando corre sync de stage sends, então **não** se promove leads a `sent` por find e é emitido log `uazapi_message_find_disabled_blocking_reconcile` (e documentação de env indica incompatibilidade operacional).
- [ ] **AC10 (NFR):** Dado chunk de ≤30 leads e uma página de find, quando o worker corre sync num único ciclo, então o tempo de find não bloqueia indefinidamente o worker HTTP do Flask (sync pesado permanece em `worker_cadence` / `kanban-data` assíncrono como hoje).

---

## Additional Context

### Dependencies

- API UAZAPI: `POST /message/find`, `GET /sender/listfolders`, `POST /sender/advanced` (`create_advanced_campaign`).
- Variáveis de ambiente: `UAZAPI_MESSAGE_FIND`, `UAZAPI_MESSAGE_FIND_SLEEP_SEC`, `UAZAPI_MESSAGE_FIND_LIMIT`, `UAZAPI_MESSAGE_FIND_MAX_PAGES`, `UAZAPI_SYNC_RECONCILE_LISTMESSAGES`, `UAZAPI_LISTFOLDERS_PREFIX_SENT`, `UAZAPI_RECONCILE_FIND_BEFORE_ROLLOVER`, `UAZAPI_LEAD_RECONCILE_V2`, constante opcional “N ciclos” para F4 (nome sugerido `UAZAPI_RECONCILE_STALE_SYNC_CYCLES`, documentar no código).

### Testing Strategy

- **Unitário:** `tests/test_sync_uazapi.py` com mocks de `UazapiService` (list_folders, message_find; list_messages assert não chamado com flag off).
- **Integração leve:** cenário rollover com BD em memória ou fixture mínima se o projeto já tiver padrão.
- **Manual:** campanha de teste com 3 leads, interromper rede / simular partial, verificar ausência de duplicados e Kanban após ~1 ciclo de worker + find.

### Notes / Riscos

- **Volume:** find = 1 HTTP por lead candidato; campanhas grandes podem prolongar sync — mitigar com sleep, batching documentado e possível limite por ciclo com continuação no próximo tick (se necessário, sub-task documentada).
- **Legado:** `process_rollover` baseado em `list_messages` para campanhas antigas — explicitar na implementação que o fluxo **novo** é `campaign_stage_sends` + `use_uazapi_sender`; evitar dupla verdade entre os dois caminhos.
- **“Perfeição”:** depende da API `message_find` devolver mensagens com `send_folder_id` fiável; se o provedor falhar, registar `last_error` / log (backlog Task 8 da spec n8n-sync).

---

## Referência rápida de código atual (âncoras)

Gatilho do find no escopo (Task 3 — substitui o gate ≥80% / ``log_success > planned``):

```python
# utils/sync_uazapi.py — _should_run_scope_message_find + reconcile_send_leads_via_message_find_for_scope
```

Marcação por prefixo da lista (a eliminar ou desativar por default):

```423:424:utils/sync_uazapi.py
    n_take = min(int(log_success), len(lead_ids))
    ids_to_update = lead_ids[:n_take]
```

Rollover UAZAPI que filtra `status = 'sent'` (deve refletir apenas sent “comprovado”):

```1348:1357:worker_cadence.py
                SELECT cl.id, cl.phone, cl.name, cl.whatsapp_link
                FROM campaign_leads cl
                WHERE cl.campaign_id = %s
                  AND cl.id = ANY(%s)
                  AND cl.current_step = 1
                  AND cl.status = 'sent'
```

---

_Fim do tech-spec._
