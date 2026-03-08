---
title: 'Rollover Modo Teste: Marcar Envios Corretamente, Atrasar FU1 e Excluir Convertido/Perdido'
slug: 'rollover-modoteste-convertido-perdido'
created: '2026-03-07'
status: 'Implementation Complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Flask', 'PostgreSQL', 'Uazapi', 'worker_cadence', 'worker_sender']
files_to_modify: ['app.py', 'worker_cadence.py', 'services/uazapi.py']
code_patterns: ['create_advanced_campaign', 'list_messages', 'rollover_test_mode', 'cadence_status converted/lost']
test_patterns: []
---

# Tech-Spec: Rollover Modo Teste — Marcar Envios Corretamente, Atrasar FU1 e Excluir Convertido/Perdido

**Created:** 2026-03-07

## Overview

### Problem Statement

Ao rodar campanha de testes com "Modo teste: rollover e avanço automático em todo ciclo (~2 min entre etapas)", ocorrem três problemas:

1. **Leads movidos antes dos envios terminarem**: Todos os leads são movidos do Inicial para Follow-up 1 no Kanban antes mesmo de a Uazapi finalizar os envios da mensagem inicial. Isso acontece porque, para campanhas `use_uazapi_sender=true`, o app marca todos os leads como `status='sent'` em lote logo após `create_advanced_campaign`, sem esperar a Uazapi reportar cada envio.

2. **Convertido/Perdido ainda recebem Follow-up 1**: Quando o usuário arrasta um card para Convertido ou Perdido, o lead ainda recebe a mensagem de Follow-up 1 (não recebe FU2 nem despedida). O motivo: se o rollover já criou a campanha FU1 com esse lead, a Uazapi já o incluiu no payload e enviará. O endpoint `/sender/edit` não permite remover mensagens individuais — apenas stop/continue/delete da campanha inteira.

3. **Janela curta para mover antes do rollover**: No modo teste, o rollover roda a cada ~2 min. O usuário tem pouco tempo para arrastar leads para Convertido/Perdido antes que o rollover crie a campanha FU1 e os inclua.

### Solution

1. **Marcar envios corretamente**: Usar o payload da campanha avançada (Uazapi `list_messages`) para sincronizar `campaign_leads.status` com o status real (Sent/Failed) da Uazapi, em vez de marcar em lote. O worker_cadence deve chamar sync antes do rollover para campanhas com `uazapi_folder_id` (campanha inicial).

2. **Atrasar rollover Inicial→FU1 no modo teste**: Adicionar `rollover_test_delay_minutes` (default 5) em `cadence_config`. No modo teste, o rollover só move leads para FU1 após esse delay desde a criação da campanha inicial (ou desde o último envio reportado). Isso dá tempo para o usuário mover cards e para a Uazapi finalizar envios.

3. **Re-query antes de criar campanha FU1**: Antes de chamar `create_advanced_campaign` para FU1, re-executar a query de leads elegíveis para excluir qualquer lead que foi movido para Convertido/Perdido no intervalo entre a query inicial e o momento do envio.

4. **Garantir exclusão Convertido/Perdido**: O move API já define `cadence_status='converted'|'lost'`. O rollover já exclui com `COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')`. Reforçar com a re-query imediata antes do create.

### Scope

**In Scope:**
- Sync com `list_messages` antes do rollover para campanhas Uazapi (marcar envios corretamente)
- Delay configurável (5 min) no modo teste para rollover Inicial→FU1
- Re-query de leads elegíveis imediatamente antes de `create_advanced_campaign` (FU1 e FU2/FU3)
- Leads em Convertido/Perdido não entram na próxima campanha

**Out of Scope:**
- Remover leads já incluídos em campanha FU1 agendada (Uazapi não suporta; o delay resolve)
- Campanhas MegaAPI (não afetadas)
- Modificar `/sender/edit` da Uazapi

---

## Estudo dos Endpoints UAZAPI (sender)

### POST /sender/simple
- **Uso**: Campanha simples — mesma mensagem para todos os números.
- **Payload**: `numbers`, `type`, `delayMin`, `delayMax`, `scheduled_for` (obrigatórios); `text`, `file`, `folder`, `info`, etc.
- **Relevância**: Não usado no rollover; rollover usa `/sender/advanced`.

### POST /sender/advanced
- **Uso**: Campanha avançada — mensagens personalizadas por destinatário.
- **Payload**: `messages` (array de `{number, type, text|file, ...}`), `delayMin`, `delayMax`, `scheduled_for` (opcional, timestamp ms), `info`.
- **Retorno**: `folder_id`, `count`, `status`.
- **Relevância**: Usado pelo rollover e pela criação de campanha inicial. `scheduled_for` em milissegundos (Unix timestamp).

### POST /sender/edit
- **Uso**: Controlar campanha existente.
- **Ações**: `stop` (pausar), `continue` (retomar), `delete` (remove apenas mensagens não enviadas).
- **Limitação**: Não permite remover mensagens individuais; apenas ações na campanha inteira.
- **Relevância**: Usado para pausar/cancelar follow-ups; não resolve exclusão de lead específico.

### POST /sender/listmessages
- **Uso**: Listar mensagens de uma campanha com filtro por status.
- **Payload**: `folder_id` (obrigatório), `messageStatus` (Scheduled|Sent|Failed), `page`, `pageSize`.
- **Retorno**: `messages` (array com `number`, status, etc.), `pagination` (`total`, `page`, `pageSize`, `lastPage`).
- **Relevância**: Permite sincronizar `campaign_leads.status` com o status real da Uazapi (Sent/Failed). Match por `number` normalizado.

---

## Context for Development

### Codebase Patterns

- **worker_cadence.py**: `process_rollover()` (Inicial→FU1), `process_rollover_fu_next()` (FU1→FU2, FU2→Despedida). Roda a cada ciclo (~2 min). `rollover_test_mode` ou `rollover_time=00:00` faz rodar em todo ciclo.
- **app.py**: Criação de campanha com `use_uazapi_sender` marca todos como `status='sent'` em lote (linhas 3708–3712). Rota `POST /api/campaigns/<id>/sync-uazapi` chama `list_messages` e atualiza `campaign_leads` por telefone.
- **move_campaign_lead**: `target_step=0`, `target_status='converted'|'lost'` para Convertido/Perdido. Atualiza `current_step` e `cadence_status`.
- **cadence_config**: JSON em `campaigns.cadence_config`. Campos: `rollover_time`, `rollover_test_mode`, `rollover_fu1_folder_id`, etc.

### Files to Reference

| File | Purpose |
|------|---------|
| worker_cadence.py | process_rollover, process_rollover_fu_next; adicionar sync, delay, re-query |
| app.py | Não marcar em lote para cadência Uazapi; ou trigger sync após create |
| services/uazapi.py | list_messages já existe |
| uazapi-openapi-spec (1).yaml | sender/advanced, sender/listmessages, sender/edit |

### Technical Decisions

1. **Sync antes do rollover**: Para campanhas com `uazapi_folder_id` (campanha inicial) e `use_uazapi_sender=true`, o worker_cadence chama a lógica de sync (equivalente a `sync-uazapi`) antes de `process_rollover`. Assim, só leads com `status='sent'` de fato (reportado pela Uazapi) entram no rollover.
2. **Delay no modo teste**: Novo campo `rollover_test_delay_minutes` (default 5). Guardar `rollover_fu1_created_at` ou usar `campaigns.created_at` / `uazapi_folder_id` + primeira execução. Alternativa: usar `campaign_leads.sent_at` — no modo teste, só considerar lead elegível para rollover se `sent_at` for há pelo menos N minutos. Mais simples: atrasar o rollover Inicial→FU1 em N minutos após a primeira vez que há leads com status=sent. Implementar com `rollover_last_run_at` ou checagem de `sent_at` mínimo.
3. **Re-query**: Antes de `create_advanced_campaign` em `process_rollover` e `process_rollover_fu_next`, re-executar a query de leads elegíveis e filtrar os que ainda estão em step correto e cadence_status não converted/lost.

---

## Implementation Plan

### Tasks

| # | Task | File(s) | Action | Status |
|---|------|---------|--------|--------|
| 1 | **Sync antes do rollover** | worker_cadence.py | Incluir `use_uazapi_sender`, `uazapi_folder_id` na query de campanhas. Antes de `process_rollover`, se campanha tem `uazapi_folder_id` e `use_uazapi_sender=true`, chamar lógica de sync (list_messages Sent/Failed) e atualizar campaign_leads. Reutilizar lógica de app.py sync-uazapi ou extrair para função compartilhada. | [x] |
| 2 | **Não marcar em lote para cadência Uazapi** | app.py | Quando criar campanha com `use_uazapi_sender` e `enable_cadence`, NÃO executar o UPDATE que marca todos como sent. Deixar status='pending' até o sync. Ou: marcar como 'scheduled' e o sync atualiza para 'sent'/'failed'. Verificar impacto no worker_sender (não processa use_uazapi_sender). | [x] |
| 3 | **rollover_test_delay_minutes** | worker_cadence.py, campaigns_new.html, app.py | Adicionar `rollover_test_delay_minutes` (int, default 5) em cadence_config. No modo teste, só executar rollover Inicial→FU1 se o lead mais antigo com status=sent tiver `sent_at` há pelo menos N minutos. Query: `MIN(sent_at) FROM campaign_leads WHERE ... AND status='sent'`; se `NOW() - MIN(sent_at) < N minutes`, return early. | [x] |
| 4 | **Re-query antes de create_advanced_campaign** | worker_cadence.py | Em `process_rollover` e `process_rollover_fu_next`, imediatamente antes de montar `messages` e chamar `create_advanced_campaign`, re-executar a query de leads elegíveis. Filtrar novamente por current_step, cadence_status NOT IN ('converted','lost'). Usar essa lista filtrada para montar messages e para o UPDATE. | [x] |
| 5 | **UI rollover_test_delay_minutes** | templates/campaigns_new.html, app.py | Quando "Modo teste" estiver marcado, exibir campo numérico "Atrasar rollover Inicial→FU1 (minutos)" com default 5, min 1, max 60. Salvar em cadence_config. | [x] |
| 6 | **Função sync compartilhada** | app.py ou services/uazapi.py | Extrair lógica de sync (list_messages + UPDATE campaign_leads) para função reutilizável. worker_cadence importa e chama antes do rollover. | [x] |

### Acceptance Criteria

- **AC1**: Given campanha use_uazapi_sender com cadência e Modo teste, when create_advanced_campaign da inicial retorna OK, then leads NÃO são marcados como sent em lote; sync posterior (via worker) atualiza status conforme list_messages.
- **AC2**: Given Modo teste com rollover_test_delay_minutes=5, when há leads em Inicial com status=sent, then rollover Inicial→FU1 só executa após pelo menos 5 min do sent_at mais antigo.
- **AC3**: Given lead arrastado para Convertido ou Perdido antes do rollover criar FU1, when rollover executa, then esse lead NÃO está na lista de messages da campanha FU1.
- **AC4**: Given lead em Inicial com status=sent há 6 min (modo teste, delay 5), when rollover executa, then rollover cria campanha FU1 e move apenas leads ainda elegíveis (não converted/lost).
- **AC5**: Given sync executado antes do rollover, when list_messages retorna Sent para telefone X, then campaign_leads com esse phone tem status='sent'.
- **AC6**: Regressão — campanhas sem Modo teste mantêm comportamento atual (rollover no horário configurado, sem delay extra).

---

## Additional Context

### Dependencies

- UazapiService.list_messages (já existe)
- Rota sync-uazapi (app.py) — lógica a ser reutilizada
- cadence_config.rollover_test_mode (já existe)

### Testing Strategy

- Teste manual: criar campanha cadência + Modo teste + use_uazapi_sender; verificar que leads não mudam para FU1 antes de 5 min
- Teste manual: mover lead para Convertido antes do rollover; verificar que não recebe FU1
- Teste manual: sync atualiza status corretamente após list_messages
- Verificar que campanhas sem Modo teste não são afetadas

### Notes

- O `sender/edit` da Uazapi não permite remover mensagens individuais. A estratégia de delay + re-query é a forma de garantir que leads movidos a tempo não entrem na campanha FU1.
- Se o usuário mover um lead para Convertido/Perdido após o rollover já ter criado a campanha FU1, esse lead ainda receberá FU1 (limitação da API). O delay de 5 min aumenta a janela para o usuário agir.
