---
title: 'UI: status de lead alinhado ao envio real (Editar + Kanban)'
slug: 'ui-sincronizacao-status-lead-envio-campanhas'
created: '2026-05-01T12:00:00Z'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8, 9]
tech_stack: ['Python 3', 'Flask', 'PostgreSQL', 'Jinja2', 'JavaScript (fetch)', 'Tailwind CDN']
files_to_modify:
  - 'app.py'
  - 'templates/campaigns_edit.html'
  - 'templates/campaigns_kanban.html'
  - 'templates/admin/campaigns_edit.html'
code_patterns:
  - 'Monólito Flask: rotas JSON em app.py com RealDictCursor e json.dumps/jsonify'
  - 'Listagem de leads: GET /api/campaigns/<id>/leads e GET /api/admin/campaigns/<id>/leads leem só colunas de campaign_leads (sem inferência)'
  - 'Kanban: GET /api/campaigns/<id>/kanban-data já expõe last_sent_stage, last_message_sent_at; label "Enviado" no card depende de sentThisStage (comparação com coluna)'
  - 'Métricas do cartão: GET /api/campaigns/<id>/stats reconcilia enviados (cadência+Uazapi via campaign_stage_sends; sem cadência via list_folders)'
  - 'Worker outbox: worker_message_outbox._persist_outcome + _apply_lead_success atualiza campaign_leads.status, last_sent_stage, last_message_sent_at'
test_patterns:
  - 'tests/test_admin_campaign_crud.py, tests/test_load.py — padrão pytest + DB'
---

# Tech-Spec: UI — status de lead alinhado ao envio real (Editar + Kanban)

**Criado:** 2026-05-01  
**Projeto:** leads-infinitos-saas  
**Idioma do documento:** pt-BR

## Overview

### Problem Statement

Na página **Editar Campanha**, a tabela "Leads da Campanha" mostra **Pendente** mesmo quando os envios já estão a decorrer com sucesso (métricas no dashboard, ex. "Enviados 4/13", já refletem progresso). Na vista **Kanban**, o cartão do lead na coluna correta nem sempre mostra a label **Enviado** quando o utilizador espera ver o estado de envio confirmado.

Isto gera desconfiança operacional: a UI não acompanha o mesmo critério de "sucesso de envio" que o resto do produto já usa (incluindo agregados Uazapi / outbox / reconciliação em `/stats`).

### Solution

1. **Backend (SSOT na API):** expor um campo derivado **no servidor** (ex.: `ui_send_status` ou reutilizar `status` com regra única documentada) na listagem paginada de leads usada pelo **Editar Campanha** (rota user e, para paridade, rota admin), combinando:
   - `campaign_leads.status` (já atualizado pelo worker outbox em `_apply_lead_success` quando `campaign_message_outbox` fica `sent`);
   - **equivalente explícito** para a UI: presença de linha em `campaign_message_outbox` com `status = 'sent'` para aquele `campaign_lead_id` (prova de envio terminal sem duplicar heurística HTTP no front);
   - sinais já persistidos em `campaign_leads` quando a sincronização Uazapi grava envio (`last_sent_stage`, `last_message_sent_at`) para reduzir janela em que agregados e linha do lead divergem.

2. **Front (templates):** a tabela em `campaigns_edit.html` (e admin) usa o campo devolvido pela API para o badge **Pendente / Enviado / …**, sem recalcular negócio no browser.

3. **Kanban:** ajustar a condição `sentThisStage` em `campaigns_kanban.html` para usar o mesmo campo booleano ou regra equivalente devolvida por `kanban-data` (ex.: `stage_sent_initial` / flags por etapa), garantindo label **Enviado** alinhada ao envio confirmado na BD, com **contraste** adequado no tema escuro (ex.: `text-emerald-300` + fundo sutil ou `aria-label` descritivo).

4. **Polling:** manter **um** intervalo por página; opcionalmente alinhar período (ex. 10–15 s) ou extrair constante JS partilhada apenas se existir segundo timer redundante na mesma vista — **não** adicionar segundo canal de polling só para status.

### Scope

**In scope:**

- `app.py`: `get_campaign_leads`, `admin_get_campaign_leads`, e opcionalmente enriquecimento mínimo em `campaign_kanban_data` (payload `leads[]`) com flags derivadas por lead.
- `templates/campaigns_edit.html`, `templates/campaigns_kanban.html`, `templates/admin/campaigns_edit.html`: consumo do novo campo; acessibilidade da label no Kanban.
- Teste mínimo (pytest) **ou** secção explícita de verificação manual (campanha a enviar, abas Editar + Kanban).

**Out of scope:**

- Alterar lógica do worker de envio (`worker_message_outbox.py`) salvo se, durante implementação, se descobrir que `campaign_leads` não está a ser atualizado em algum ramo — nesse caso tratar como bug separado com critério explícito.
- Refatorar `sync_uazapi` ou modelo de dados completo de `campaign_stage_sends`.
- Canvas ou novos endpoints só para métricas se o existente `/api/campaigns/<id>/stats` + leads já bastarem.

## Context for Development

### Codebase Patterns

- **Editar campanha (user):** `GET /campaigns/<id>/edit` → `templates/campaigns_edit.html`; tabela preenchida por `loadLeads()` → `GET /api/campaigns/<id>/leads` (`get_campaign_leads` em `app.py`, ~8890). Polling: `setInterval` com `CAMPAIGN_ACTIVE_POLL_MS` (10 s) se `campaign.status` ∈ `running`, `pending`.
- **Editar campanha (admin):** `templates/admin/campaigns_edit.html` → `GET /api/admin/campaigns/<id>/leads` (`admin_get_campaign_leads`, ~4701). Comentário atual: *"sem inferência a partir de agregados UAZAPI"* — ao introduzir `ui_send_status`, **atualizar este comentário** para refletir a regra única documentada abaixo.
- **Kanban:** `refreshBoard()` → `GET /api/campaigns/<id>/kanban-data` (`campaign_kanban_data`, ~3078). Polling com o mesmo `CAMPAIGN_ACTIVE_POLL_MS` (10 s) nas mesmas condições de status. Card: `createLeadCard` usa `sentThisStage` comparando `last_sent_stage` com `stageForColumn` (`getStageByStep`).
- **Dashboard / cartão:** `updateCampaignStats` em `templates/campaigns_list.html` usa `/api/campaigns/<id>/stats` (`get_campaign_stats`, ~6797), que para **cadência + Uazapi** substitui contagens via `_reconciled_uazapi_cadence_counts_via_stage_progress` (agregados `campaign_stage_sends`), podendo **adiantar** percecção de "enviados" vs. linhas ainda `pending` em `campaign_leads` até à próxima sincronização — daí a necessidade de campo derivado na listagem de leads e, se preciso, flag no Kanban.
- **Outbox:** `_persist_outcome` em `worker_message_outbox.py` marca outbox `sent` e chama `_apply_lead_success`, que faz `UPDATE campaign_leads SET status = 'sent', last_sent_stage = 'initial', …` no ramo inicial.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `app.py` | `get_campaign_leads`, `admin_get_campaign_leads`, `campaign_kanban_data`, `get_campaign_stats`, constantes de sync Uazapi |
| `templates/campaigns_edit.html` | Tabela de leads, polling, mapeamento de labels de status |
| `templates/campaigns_kanban.html` | `createLeadCard`, `refreshBoard`, polling |
| `templates/admin/campaigns_edit.html` | Paridade com listagem admin |
| `worker_message_outbox.py` | Referência do critério "sucesso" persistido (outbox + `campaign_leads`) |

### Technical Decisions

1. **Nome do campo JSON:** `ui_send_status` (valores: `pending` \| `sent` \| `failed` \| `invalid` — espelhar enum visível na UI; `failed`/`invalid` mantêm-se a partir de `campaign_leads.status`).

2. **Regra SQL (proposta — implementar num único sítio, ex. expressão CASE na query ou helper Python que monte o dict):**

   - Se `campaign_leads.status` ∈ `('failed','invalid')` → `ui_send_status = status`.
   - Senão, se `campaign_leads.status = 'sent'` → `sent`.
   - Senão, se existe `campaign_message_outbox` com `campaign_lead_id = cl.id` e `status = 'sent'` → `sent` (prova de envio outbox concluído).
   - Senão, se `last_message_sent_at IS NOT NULL` **e** `last_sent_stage IS NOT NULL` (strings normalizadas) → tratar como `sent` **apenas se** o produto aceitar "qualquer etapa com envio confirmado" na coluna única "Status" da tabela de edição; se a tabela deve significar só **primeira mensagem (initial)**, restringir a `last_sent_stage ILIKE 'initial'` ou equivalente. **Decisão recomendada:** para a coluna única "Status" da página Editar, alinhar a "já houve pelo menos um envio bem-sucedido persistido" = OR acima com `last_sent_stage` não nulo **ou** outbox `sent` para qualquer `stage`; manter filtro por `status` na query existente quando o utilizador filtra "Pendente" na UI (ver AC de filtros).

3. **Filtro `status` na query:** O parâmetro `status=pending` do utilizador deve continuar a filtrar por **`campaign_leads.status`** (BD), não por `ui_send_status`, para não quebrar "Substituir Pendentes" e semântica de DB; opcionalmente documentar que o filtro "Pendente" é pendente **estrito** na BD. Alternativa (nice-to-have): query string `ui_status` — só se necessário; default fora de escopo.

4. **Kanban:** Incluir no payload por lead um booleano `sent_for_column_stage` calculado no **servidor** (recomendado) com a mesma normalização de `stage` que o JS usa (`initial`, `follow1`, …), evitando drift entre TS e Python. Se preferir mínimo diff: estender apenas o JSON com `outbox_sent_stages` (array) ou `last_sent_stage` já exposto + corrigir comparação no JS (ex.: normalizar `last_sent_stage` numérico `1` → `initial` se existir legado).

5. **Sincronização opcional antes do SELECT:** `campaign_kanban_data` já pode chamar `sync_campaign_leads_from_uazapi` com throttle. **Não** obrigar sync pesado em `get_campaign_leads` na primeira iteração; preferir campo derivado + polling. Se após QA ainda houver lag > SLA, avaliar chamar o mesmo bloco throttled de sync **uma vez** por request em campanhas `running`/`pending` com Uazapi (parametrizado), documentando custo.

6. **Acessibilidade:** Label "Enviado" com contraste ≥ recomendação WCAG em fundo escuro; preferir `text-emerald-300` / `border border-emerald-500/30` e `role="status"` ou texto visível + `title` descritivo no card.

## Implementation Plan

### Tasks

- [x] **Task 1:** Definir função pura ou fragmento SQL reutilizável em `app.py` que calcula `ui_send_status` a partir de colunas de `campaign_leads` + EXISTS em `campaign_message_outbox` (status `sent`), com testes unitários leves (opcional: mock de row dict).
  - **File:** `app.py`
  - **Action:** Centralizar regra; evitar copiar CASE em três sítios sem comentário cruzado.

- [x] **Task 2:** Estender `get_campaign_leads` (`GET /api/campaigns/<id>/leads`): incluir na SELECT colunas necessárias (`last_sent_stage`, `last_message_sent_at`, `current_step`, `cadence_status` se úteis ao debug) e serializar `ui_send_status` por lead. Manter `status` bruto da BD para compatibilidade.
  - **File:** `app.py`
  - **Notes:** Atualizar docstring da rota; garantir `default=str` / isoformat em timestamps como já feito para `sent_at`.

- [x] **Task 3:** Estender `admin_get_campaign_leads` com a **mesma** regra e colunas, e atualizar docstring (~4707) removendo a afirmação de "sem inferência" ou substituindo por "inferência limitada a BD: campaign_leads + campaign_message_outbox".
  - **File:** `app.py`

- [x] **Task 4:** Estender `campaign_kanban_data` serialização de `leads[]` com booleano por lead, ex. `ui_sent_in_column_stage`, calculado com `current_step`, `cadence_status`, `last_sent_stage` e, se necessário, EXISTS outbox por `(campaign_lead_id, stage)`.
  - **File:** `app.py`
  - **Notes:** Manter custo O(n) por página de leads da campanha; usar uma query com subselect agregado ou pré-busca de outbox `sent` por `campaign_id` numa única query auxiliar se o volume for preocupante.

- [x] **Task 5:** Atualizar `templates/campaigns_edit.html` — em `loadLeads`, usar `lead.ui_send_status ?? lead.status` para `statusClass` / `statusLabel`; corrigir `colspan` das linhas de loading/erro/vazio de `3` para **5** (cabeçalho da tabela tem 5 colunas).
  - **File:** `templates/campaigns_edit.html`

- [x] **Task 6:** Atualizar `templates/admin/campaigns_edit.html` com a mesma lógica de badge que Task 5.
  - **File:** `templates/admin/campaigns_edit.html`

- [x] **Task 7:** Atualizar `templates/campaigns_kanban.html` — em `createLeadCard`, usar `lead.ui_sent_in_column_stage === true` (ou nome acordado na Task 4) para mostrar a label **Enviado**; ajustar classes para contraste no dark theme; opcional `aria-label` no card com nome + estado.
  - **File:** `templates/campaigns_kanban.html`

- [x] **Task 8 (opcional):** Ajustar intervalo de polling (ex.: 10 s) **uma vez** para Editar e Kanban de forma consistente (constante única no topo do script ou comentário cruzado), apenas se a validação manual achar 15 s "não quase real"; não adicionar segundo `setInterval` para o mesmo fim.

- [x] **Task 9:** Teste mínimo em `tests/` — inserir campanha + lead + linha `campaign_message_outbox` `sent` com lead ainda `pending` na BD (cenário sintético de lag) e assert `ui_send_status == 'sent'` na resposta JSON; **ou** documentar checklist manual detalhado na secção Testing Strategy abaixo se pytest for inviável no CI atual.

### Acceptance Criteria

- [ ] **AC1:** Dado um lead com `campaign_message_outbox` com `status = 'sent'` para a campanha e `campaign_leads.status` ainda `pending`, quando o utilizador abre **Editar Campanha** e a lista é carregada, então o badge do lead mostra **Enviado** (ou label equivalente já usada na app) com base em `ui_send_status` devolvido pela API.
- [ ] **AC2:** Dado um lead com `campaign_leads.status = 'sent'` e timestamps coerentes, quando a página é recarregada manualmente, então o badge continua **Enviado** (consistência com BD).
- [ ] **AC3:** Dado um lead genuinamente sem envio confirmado (sem outbox `sent`, sem `last_sent_stage` / regra acordada), quando a lista é carregada, então o badge permanece **Pendente**.
- [ ] **AC4:** Dado o Kanban aberto com campanha em `running` ou `pending`, quando o envio confirma na BD e ocorre `refreshBoard` (manual ou por polling), então o card na coluna da etapa correspondente mostra a label **Enviado** (ou ícone + texto) alinhada ao campo servidor da Task 4.
- [ ] **AC5:** Dado o filtro "Status: Pendente" na UI de edição, quando aplicado, então a query continua a filtrar por `campaign_leads.status` (comportamento documentado) e não exclui leads `pending` na BD por engano via `ui_send_status`.
- [ ] **AC6:** Dado tema escuro, quando a label **Enviado** é renderizada no Kanban, então o contraste é legível (inspeção visual ou ferramenta de contraste) e há indicação acessível (`title`/`aria-label` mínimo).

## Additional Context

### Dependencies

- PostgreSQL: tabelas `campaign_leads`, `campaign_message_outbox` (índices existentes em `campaign_message_outbox(campaign_id, …)` / `(campaign_lead_id, stage)` — verificar `EXPLAIN` se necessário).
- Nenhuma dependência pip nova.

### Testing Strategy

- **Automatizado (recomendado):** teste de integração leve que cria `campaign`, `campaign_leads`, `campaign_message_outbox` (`sent`), chama a view/route via client Flask ou função interna, parse JSON e assert em `ui_send_status`.
- **Manual:** (1) Criar campanha Uazapi com envio ativo. (2) Abrir `/campaigns/<id>/edit` e `/campaigns/<id>/kanban` em duas abas. (3) Após primeiro envio bem-sucedido (confirmar na BD ou no dashboard), dentro de ≤2 ciclos de polling (ou refresh manual), verificar badge **Enviado** na tabela e label no card. (4) Hard refresh (F5) e confirmar persistência.

### Notes

- **Causa raiz provável:** `/stats` reconcilia enviados com `campaign_stage_sends` (cadência) ou `list_folders` (sem cadência), enquanto `/api/.../leads` lia apenas `campaign_leads.status` sem joins — comentários em `get_campaign_leads` e `admin_get_campaign_leads` já mencionam ausência de derivación; este spec corrige essa lacuna **sem** mover regra de negócio para o browser.
- **Risco:** Queries com EXISTS por lead; em campanhas muito grandes, monitorizar tempo de resposta — considerar cache ou subquery materializada por `campaign_id` se necessário.
- **Paridade admin:** manter comportamento alinhado ao user para suporte operacional.

## Validação pós-implementação (checklist rápido)

1. Abrir **Editar Campanha** com campanha a enviar; confirmar que leads passam a **Enviado** após sucesso (sem depender de regra inventada no JS).
2. Abrir **Kanban** na mesma campanha; confirmar label **Enviado** na coluna correta e contraste aceitável.
3. **F5** em ambas as vistas; estado mantém-se.
4. (Admin) Repetir em `/admin/campaigns/<id>/edit` se aplicável.

---

**Arquivos esperados após implementação:** `app.py`, `templates/campaigns_edit.html`, `templates/campaigns_kanban.html`, `templates/admin/campaigns_edit.html`, opcionalmente `tests/test_campaign_leads_ui_status.py` (nome sugestivo).

**Comando sugerido para desenvolvimento numa sessão nova:** colar o path deste ficheiro no fluxo `quick-dev` da BMAD, se disponível.
