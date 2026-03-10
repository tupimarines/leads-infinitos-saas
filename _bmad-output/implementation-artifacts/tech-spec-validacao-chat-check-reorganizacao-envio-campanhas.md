---
title: 'Validação prévia /chat/check e reorganização do fluxo de envio de campanhas'
slug: 'validacao-chat-check-reorganizacao-envio-campanhas'
created: '2026-03-08'
status: 'Implementation Complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Uazapi API', 'pytz']
files_to_modify: ['app.py', 'worker_cadence.py', 'services/uazapi.py', 'utils/sync_uazapi.py', 'templates/campaigns_kanban.html', 'templates/campaigns_new.html', 'templates/campaigns_list.html']
code_patterns: ['check_phone batch', 'create_advanced_campaign', 'check_daily_limit', 'listfolders log_sucess', 'campaign_leads status invalid']
test_patterns: ['pytest tests/', 'test_*.py']
depends_on: 'tech-spec-follow-up-uazapi-kanban-limite-diario'
---

# Tech-Spec: Validação prévia /chat/check e reorganização do fluxo de envio de campanhas

**Created:** 2026-03-08

## Overview

### Problem Statement

1. **Números inválidos infectam o fluxo:** Leads sem WhatsApp (ex: "not on WhatsApp") são enviados na campanha, falham, e o usuário não sabe quais excluir — `list_messages` com status=Failed retorna só 1 item mesmo com múltiplas falhas.
2. **Campanha inicial vs limite diário:** Cliente pode extrair 500 leads mas o plano permite 30/dia. Hoje não há limite aplicado na criação da campanha.
3. **Follow-up manual fragmentado:** Usuário remove manualmente quem respondeu no Kanban; falta botão "Gerar campanha para esta etapa" para criar campanha avançada com os restantes após a remoção.

### Solution

1. **Validação prévia** — Rodar POST /chat/check em batch (50 por vez) em todos os números **pendentes** antes de criar campanha. Marcar leads com `isInWhatsapp: false` como `status='invalid'` (ou excluir da lista e re-parsear). Só enviar para válidos.
2. **Limite diário por instância** — 1 campanha por instância por dia; máximo 10/20/30 números conforme plano. Nova campanha só liberada após meia-noite (BRT). Se usuário tem 3 instâncias, pode criar 1 campanha por instância, cada com o máximo do plano.
3. **Botão "Gerar follow up"** — No Kanban, acima de cada etapa (Inicial, Follow-up 1, 2, Despedida), botão que cria campanha Uazapi avançada com os leads daquela etapa. Folders são criados apenas quando o usuário clica (não pré-criados). Sem rollover automático — apenas botões manuais.
4. **Sync via listfolders** — Chamar `list_folders` **sem** parâmetro status (retorna todas as campanhas); buscar folder pelo `id` da campanha criada. Usar `log_sucess` para marcar os números do payload como "enviado" no card. `list_messages` Failed para marcar falhas (parcial).
5. **Cortes/batches para listas grandes** — Ex.: 500 leads → 400 válidos após validação; limite 30/instância/dia. Após validação, atribuir `send_batch` (1, 2, 3, ...) a cada lead: primeiros 30 = batch 1, próximos 30 = batch 2, etc. Dia 1: enviar batch 1; após sync, esses 30 passam a status=sent e current_step=2 (FU1). Dia 2: enviar batch 2 (próximos 30). Para follow-ups: mesma lógica — enviar em ordem de `send_batch`, excluindo convertido/perdido. Assim os próximos 30 são puxados automaticamente a cada dia.

### Scope

**In Scope:**
- Endpoint POST /api/campaigns/<id>/validate-leads — valida batch via /chat/check, marca status=invalid (ou exclui inválidos)
- UI: botão "Validar lista" na página da campanha
- Limite: 1 campanha por instância por dia; máximo 10/20/30 conforme plano; liberar nova após meia-noite
- Botão "Gerar follow up" acima de cada etapa no Kanban (Inicial, FU1, FU2, Despedida)
- Fluxo: validar (check_phone) → marcar/excluir inválidos → criar campanha com limite → listfolders para marcar enviados

**Out of Scope:**
- Rollover automático (removido; apenas botões manuais "Gerar follow up")
- Validação automática na importação (pode ser fase 2)
- Agente de IA para sugerir remoção de conversões
- MegaAPI (mantém comportamento atual; foco em Uazapi)

### Caso hipotético: lista grande com cortes diários

**Cenário:** 500 leads → após validação sobram 400; campanha inicial suporta 30 por instância por dia.

**Fluxo:**
1. **Validação:** 500 → 400 válidos (100 invalid).
2. **Atribuição de batch:** Após validação, atribuir `send_batch` (1, 2, 3, ...) a cada lead válido, ordenado por id: leads 1–30 = batch 1, 31–60 = batch 2, ..., 371–400 = batch 14.
3. **Dia 1 (Inicial):** Usuário clica "Gerar follow up" na coluna Inicial. Sistema pega os primeiros 30 (send_batch=1, status=pending, current_step=1). Envia. Após sync: status=sent, current_step=2 (FU1).
4. **Dia 2 (Inicial):** Próximos 30 (send_batch=2). Envia. Após sync: status=sent, current_step=2.
5. **Follow-ups:** Mesma lógica. Para FU1: pega leads com current_step=2, status=sent, cadence_status NOT IN ('converted','lost'), ORDER BY send_batch ASC, id ASC LIMIT 30. Após sync: current_step=3. Para FU2→FU3, FU3→Despedida: idem, excluindo convertido/perdido.

**Coluna `send_batch`:** Garante ordem determinística — os próximos 30 são sempre os do próximo batch. Sem essa coluna, a ordem poderia variar entre dias.

## Context for Development

### Codebase Patterns

| Padrão | Localização | Notas |
|--------|-------------|-------|
| check_phone | services/uazapi.py:223 | POST /chat/check — aceita batch; retorna **array** (1 item por número). Ver schema abaixo. |
| check_daily_limit | utils/limits.py:49 | `check_daily_limit(user_id, plan_limit)` — True se pode enviar |
| get_user_daily_limit | utils/limits.py:22 | Retorna 10/20/30 por plan |
| create_advanced_campaign | services/uazapi.py:255 | Payload: delayMin, delayMax, messages, info, scheduled_for |
| campaign_leads.status | app.py:339 | pending, sent, invalid, failed |
| campaign_leads.send_batch | novo | Integer 1, 2, 3, ... — ordem de envio para cortes diários; atribuído após validação |
| listfolders | services/uazapi.py:336 | GET /sender/listfolders — chamar **sem** status; buscar folder por id no array retornado; log_sucess, log_failed, log_total |
| list_messages | services/uazapi.py:367 | POST /sender/listmessages — retorna só 1 item (Sent/Failed) |
| campaign_instances | app.py:2910 | Query `campaign_instances ci JOIN instances i` para obter apikey Uazapi por campaign_id |
| move_campaign_lead | app.py:2241 | POST /api/campaigns/<id>/leads/<id>/move — body `{target_step, target_status}` |
| normalize_phone_for_match | utils/sync_uazapi.py:13 | Extrai dígitos, variantes 55/11; usar para normalizar phone antes de check_phone |

### API check_phone (POST /chat/check)

**cURL:**
```bash
curl --request POST \
  --url https://neurix.uazapi.com/chat/check \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --header 'token: <TOKEN>' \
  --data '{"numbers": ["5511999999999", "123456789@g.us"]}'
```

**Resposta 200:** array direto (1 item por número)
```json
[
  {"query": "string", "jid": "string", "lid": "string", "isInWhatsapp": false, "verifiedName": "string", "groupName": "string", "error": "string"}
]
```

**Resposta 400:** payload inválido ou sem números
```json
{"error": "Missing numbers in payload"}
```

### Files to Reference

| File | Purpose |
|------|---------|
| app.py | Rotas campanhas, create campaign, Kanban API; adicionar validate-leads, gerar-campanha |
| worker_cadence.py | Rollover, create_advanced_campaign; já usa check_daily_limit |
| services/uazapi.py | check_phone, create_advanced_campaign, list_folders, list_messages |
| utils/sync_uazapi.py | sync_campaign_leads_from_uazapi; listfolders como fonte primária |
| utils/limits.py | get_user_daily_limit, check_daily_limit |
| templates/campaigns_kanban.html | Colunas; adicionar botão "Gerar campanha" por coluna |
| templates/campaigns_new.html | Criação; adicionar "Validar lista" ou fluxo validar antes de enviar |
| templates/campaigns_list.html | Lista; exibir leads válidos/inválidos |

### Technical Decisions

1. **Validação apenas pendentes (F3):** Validar **somente** leads com `status='pending'`. **Por quê?** Se validássemos também os `sent`, um lead que foi enviado com sucesso ontem poderia hoje ter o número desativado no WhatsApp. O check_phone retornaria isInWhatsapp=false e marcaríamos como invalid — mas esse lead já recebeu a mensagem! Seria errado "desfazer" o envio. **Solução:** validar só quem ainda não foi enviado (pending).
2. **validate-leads:** Síncrono; batch 50; delay 0.5s entre batches; **timeout 90s** no request. Resposta: array indexado — mapear por índice (ordem preservada).
3. **Limite por instância (F2):** 1 campanha por instância por dia. Se 3 instâncias, 3 campanhas (cada com max do plano). Nova campanha liberada apenas após meia-noite BRT.
4. **Gerar follow up (F4, F7):** Folders criados apenas ao clicar "Gerar follow up" acima da etapa. Sem rollover automático — apenas botões manuais.
5. **listfolders (F8):** Chamar `list_folders(token)` **sem** parâmetro status; resultado pode ter várias campanhas; buscar folder pelo `id` da campanha criada.
6. **Payload storage (F9):** Armazenar em `cadence_config`: `{rollover_fu1_folder_id, rollover_fu1_lead_ids, ...}` por etapa. Step 1: `campaigns.uazapi_folder_id` + `campaigns.uazapi_last_send_lead_ids` (ou coluna JSONB). Garante que sync consiga mapear log_sucess → lead_ids.
7. **list_messages Failed:** Chamar para marcar os que retornar como failed; aceitar que só 1 pode vir.
8. **Retry e fallbacks (F10):** Ver seção "Error Handling e Retry" abaixo.
9. **Cortes/batches (send_batch):** Coluna `send_batch` em campaign_leads. Após validação, atribuir 1 aos primeiros 30 válidos (ORDER BY id), 2 aos próximos 30, etc. Gerar follow up step 1: WHERE status=pending AND current_step=1 ORDER BY send_batch ASC, id ASC LIMIT 30. Follow-ups: WHERE status=sent AND current_step=N AND cadence_status NOT IN ('converted','lost') ORDER BY send_batch ASC, id ASC LIMIT 30. Após sync: atualizar current_step para próxima etapa.

### Error Handling e Retry (F10)

| API | Retry | Backoff | Após esgotar retries |
|-----|-------|---------|----------------------|
| **check_phone** | 2x se None/Timeout | 1s | **Pular batch** e continuar com o próximo. Números do batch com falha permanecem pending (não marcados invalid). Incluir no retorno: `batches_skipped: N`, `partial: true` se houve falha. |
| **check_phone 429** | 3x | 2s | Pular batch e continuar. |
| **check_phone 400** | Não retry | — | Retornar mensagem do payload (erro de input). |
| **create_advanced_campaign** | 2x se None/Timeout | 2s | Retornar erro 502 — não há "próximo número" (envio é atômico). |
| **create_advanced_campaign 429** | 3x | 3s | Retornar 429 ao usuário. |
| **list_folders** | 1x se None | 1s | Fallback para list_messages; se folder não encontrado, pular e continuar com próximo folder (se houver). |

**Regra geral:** Quando possível (operações em batch ou iterativas), **pular o item com falha e continuar** em vez de abortar tudo. Retornar resultado parcial com indicação do que falhou.

### Estado Atual

- app.py: criação de campanha envia imediatamente para Uazapi se use_uazapi_sender, sem validação prévia
- worker_sender: check_phone_one por lead (envio 1 a 1) — não usado para campanha Uazapi
- worker_cadence: rollover automático — **será removido/desabilitado** para este fluxo
- Kanban: move manual; sem botão "Gerar follow up"

## Implementation Plan

### Tasks

- [x] **Task 1: Helper para normalizar phone de lead**
  - File: `utils/sync_uazapi.py` ou novo `utils/phone_utils.py`
  - Action: Criar `_normalize_phone_for_api(phone: str) -> str` que extrai dígitos; se 10–11 dígitos sem 55, adiciona 55. Retorna string ou None se inválido. Reutilizar lógica de `normalize_phone_for_match` se necessário.
  - Notes: Usado em validate-leads e gerar-campanha para montar payload.

- [x] **Task 2: Helper para obter leads pendentes com phone**
  - File: `app.py`
  - Action: Função `_get_leads_for_validation(campaign_id)` — SELECT id, phone, whatsapp_link WHERE campaign_id AND status = 'pending'. Retorna lista de dicts. Para cada lead: usar phone ou whatsapp_link; normalizar com helper acima. Filtrar leads sem número.
  - Notes: **Apenas pending** — não revalidar sent (F3).

- [x] **Task 3: Endpoint POST /api/campaigns/<id>/validate-leads**
  - File: `app.py`
  - Action: Rota @login_required; verificar campanha pertence ao user; obter instância Uazapi; chamar _get_leads_for_validation (só pending); em batches de 50, chamar uazapi.check_phone(token, numbers) com retry (F10); para cada item com isInWhatsapp=false, UPDATE campaign_leads SET status='invalid' WHERE id=lead_id (mapear por índice); time.sleep(0.5) entre batches; **após validação, atribuir send_batch** aos válidos restantes (status=pending): SELECT id FROM campaign_leads WHERE campaign_id=X AND status='pending' ORDER BY id; atribuir batch 1 aos primeiros 30, batch 2 aos próximos 30, etc.; retornar JSON {valid: N, invalid: M}. **Timeout 90s** no request (F5).
  - Notes: Batch 50; delay 0.5s. Atribuição send_batch garante cortes diários ordenados. Se revalidar, reatribuir send_batch aos válidos atuais.

- [x] **Task 4: Função para contar sent hoje e verificar se instância já enviou**
  - File: `utils/limits.py`
  - Action: `get_sent_today_count(user_id)` — count por user_id; `get_sent_today_count_by_instance(instance_id)` ou `can_create_campaign_today(instance_id)` — retorna True se instância ainda não criou campanha hoje (ou após meia-noite). Nova campanha só liberada após meia-noite BRT (F2).
  - Notes: 1 campanha por instância por dia.

- [x] **Task 5: Aplicar limite na criação de campanha Uazapi**
  - File: `app.py` (bloco ~3690–3705)
  - Action: Verificar 1 campanha por instância por dia (F2); `limit = get_user_daily_limit(campaign.user_id)`; truncar valid_leads para `min(len(valid_leads), limit)`. Filtrar status=invalid antes. Incluir no retorno: `leads_sent_today`, `limit_remaining`.
  - Notes: Nova campanha só após meia-noite por instância.

- [x] **Task 6: Endpoint POST /api/campaigns/<id>/gerar-campanha**
  - File: `app.py`
  - Action: Body `{step: 1|2|3|4}`. Verificar campanha pertence ao user; obter instância Uazapi; verificar 1 campanha/instância/dia (F2). **Query com send_batch:** Step 1: WHERE status=pending AND current_step=1 ORDER BY send_batch ASC, id ASC LIMIT 30; Steps 2–4: WHERE status=sent AND current_step=N AND cadence_status NOT IN ('converted','lost') ORDER BY send_batch ASC, id ASC LIMIT 30. Montar messages; chamar create_advanced_campaign com retry (F10); salvar folder_id + lead_ids em cadence_config (F9). Retornar {folder_id, count}.
  - Notes: send_batch garante que os "próximos 30" sejam sempre os do próximo corte; follow-ups excluem convertido/perdido.

- [x] **Task 6b: Coluna send_batch em campaign_leads**
  - File: `app.py` (init_db)
  - Action: `ALTER TABLE campaign_leads ADD COLUMN IF NOT EXISTS send_batch INTEGER DEFAULT NULL`. Atribuído após validação (Task 3). Leads sem send_batch (campanhas antigas) usam ORDER BY id como fallback.
  - Notes: Garante cortes diários ordenados para listas grandes.

- [x] **Task 7: Armazenar payload em cadence_config (F9)**
  - File: `app.py`, `campaigns` table
  - Action: Step 1: `campaigns.uazapi_folder_id` + nova coluna `uazapi_last_send_lead_ids JSONB` (ou em cadence_config se preferir). Steps 2–4: `cadence_config.rollover_fu1_folder_id`, `cadence_config.rollover_fu1_lead_ids`, etc. Ao criar campanha, salvar lead_ids enviados para que sync possa mapear log_sucess → leads.
  - Notes: Obrigatório para sync via listfolders funcionar (listfolders não retorna números, só log_sucess).

- [x] **Task 8: Botão "Validar lista" no Kanban**
  - File: `templates/campaigns_kanban.html`
  - Action: Adicionar botão no header (ao lado de Atualizar): "Validar lista". onclick chama fetch POST /api/campaigns/{{campaign.id}}/validate-leads. Exibe toast com resultado (X válidos, Y inválidos). Só visível se campaign.use_uazapi_sender.
  - Notes: Usar fetch, response.json(), toast existente.

- [x] **Task 9: Botão "Gerar follow up" por etapa no Kanban**
  - File: `templates/campaigns_kanban.html`
  - Action: Acima de cada etapa (Inicial, FU1, FU2, Despedida), adicionar botão "Gerar follow up" se count > 0. onclick: fetch POST /api/campaigns/{{campaign.id}}/gerar-campanha com body {step: data-step}. Toast sucesso/erro. refreshBoard() após sucesso.
  - Notes: Só visível para campanhas Uazapi com cadência. Sem rollover automático (F7).

- [x] **Task 10: Sync com listfolders**
  - File: `utils/sync_uazapi.py`
  - Action: Chamar `list_folders(token)` **sem** parâmetro status (F8); buscar folder pelo id no array retornado. Se status=done e log_sucess > 0, usar lead_ids armazenados para marcar status=sent **e atualizar current_step** conforme etapa: step 1 (Inicial)→current_step=2; step 2 (FU1)→current_step=3; step 3 (FU2)→current_step=4; step 4 (Despedida)→manter. Assim os leads avançam para a próxima coluna do Kanban após sync. Fallback: se folder não encontrado ou sem payload, usar fetch_all_phones_by_status (list_messages). Retry conforme F10.
  - Notes: listfolders retorna várias campanhas; filtrar por folder_id. Sync precisa saber qual step o folder representa (via cadence_config ou uazapi_last_send_step).

- [x] **Task 11: list_messages Failed no sync**
  - File: `utils/sync_uazapi.py`
  - Action: Após sync Sent, chamar list_messages status=Failed; para cada mensagem retornada, extrair chatid com _extract_phones_from_message; UPDATE campaign_leads SET status='failed' WHERE phone/whatsapp_link match (normalize_phone_for_match).
  - Notes: Aceitar que só 1 pode vir; marcar o que vier.

- [x] **Task 12: Desabilitar rollover automático (F7)**
  - File: `worker_cadence.py`
  - Action: Desabilitar ou remover a lógica de rollover automático (process_rollover, process_rollover_fu_next). Envios apenas via botões "Gerar follow up" no Kanban.
  - Notes: Pode ser flag `rollover_manual_only` em cadence_config ou simplesmente não agendar rollover.

- [ ] **Task 13: Exibir limite na UI de criação** (opcional, não implementado)
  - File: `templates/campaigns_new.html`
  - Action: Se use_uazapi_sender, exibir texto "Limite diário: X mensagens. Restam Y hoje." (via API ou JS). Opcional para MVP.
  - Notes: Pode ser fase 2; o limite já é aplicado no backend.

### Acceptance Criteria

- [ ] **AC1:** Given campanha com 100 leads pendentes, when "Validar lista", then check_phone em batches de 50; leads isInWhatsapp=false recebem status=invalid; resposta {valid: 85, invalid: 15}; timeout 90s

- [ ] **AC2:** Given 1 campanha por instância por dia, when usuário tenta criar 2ª campanha na mesma instância no mesmo dia, then retorna erro; após meia-noite BRT, libera

- [ ] **AC3:** Given Kanban com 25 leads em FU1, when "Gerar follow up" na coluna FU1, then campanha Uazapi criada; folder_id e lead_ids salvos em cadence_config; leads permanecem na etapa

- [ ] **AC4:** Given campanha com 100 leads (15 invalid), when "Gerar follow up" ou criação, then apenas 85 válidos considerados; limite aplicado

- [ ] **AC5:** Given campanha enviada com folder_id e lead_ids armazenados, when sync chama list_folders (sem status) e encontra folder por id com status=done e log_sucess=N, then N leads do payload marcados status=sent

- [ ] **AC6:** Given campanha com falhas, when sync chama list_messages status=Failed, then lead correspondente marcado status=failed

- [ ] **AC7:** Given campanha sem instância Uazapi, when "Validar lista" ou "Gerar follow up", then retorna 400

- [ ] **AC8:** Given campanha de outro usuário, when validate-leads ou gerar-campanha, then retorna 404

- [ ] **AC9:** Given check_phone retorna 400 "Missing numbers in payload", then retorna mensagem apropriada ao usuário (não retry)

- [ ] **AC10 (Cortes):** Given 400 leads válidos após validação, when "Validar lista" termina, then send_batch atribuído: 1–30=batch 1, 31–60=batch 2, etc. Dia 1 "Gerar follow up" Inicial envia 30 (batch 1); após sync, current_step=2. Dia 2 envia próximos 30 (batch 2). Follow-ups: mesma ordem por send_batch, excluindo convertido/perdido.

### Dependencies

- Uazapi POST /chat/check aceita batch (confirmado)
- Uazapi listfolders retorna log_sucess (confirmado)
- License.daily_limit por user (utils/limits.py)
- campaign_leads.status: invalid já existe
- Instância Uazapi vinculada à campanha (campaign_instances + instances.api_provider='uazapi')

### Testing Strategy

- **Unit:** Mock uazapi.check_phone em test_validate_leads; retornar array com isInWhatsapp; assert UPDATE status correto
- **Unit:** get_sent_today_count e get_user_daily_limit; assert limite aplicado
- **Integration:** Teste manual: criar campanha, validar lista, gerar campanha no Kanban
- **Regression:** Campanhas MegaAPI e sem cadência continuam funcionando

### Notes

- Batch size 50 para check_phone; timeout 90s em validate-leads
- **Gunicorn:** `--timeout 180` no docker-compose (validate-leads pode levar 90s+ por batch com retries; worker timeout 30s causava WORKER TIMEOUT)
- "Gerar follow up" por etapa: folders criados apenas ao clicar; sem rollover automático
- Armazenar lead_ids obrigatório (cadence_config / uazapi_last_send_lead_ids) — listfolders não retorna números
- list_folders sem status; buscar folder por id no array (várias campanhas no resultado)
- Inválidos: tag status=invalid OU excluir da lista e re-parsear (escolher uma abordagem)
- send_batch: atribuído após validação aos válidos; fallback ORDER BY id para campanhas sem send_batch
