---
title: 'Follow-up Uazapi com gestão Kanban e limite diário'
slug: 'follow-up-uazapi-kanban-limite-diario'
created: '2026-03-06'
status: 'ready-for-dev'
stepsCompleted: [1, 2]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Redis', 'RQ', 'Uazapi API', 'pytz']
files_to_modify: ['worker_cadence.py', 'app.py', 'services/uazapi.py', 'templates/campaigns_kanban.html', 'templates/campaigns_new.html']
code_patterns: ['UazapiService create_advanced_campaign com scheduled_for', 'check_daily_limit em worker_sender', 'cadence_status/cadence_config em campaign_leads', 'get_campaign_instance retorna name+apikey', 'move_campaign_lead POST /api/campaigns/<id>/leads/<id>/move']
test_patterns: ['pytest em tests/', 'test_*.py']
depends_on: 'tech-spec-campanhas-uazapi-api'
---

# Tech-Spec: Follow-up Uazapi com gestão Kanban e limite diário

**Created:** 2026-03-06

## Overview

### Problem Statement

O worker_cadence atual usa MegaAPI e Chatwoot para follow-ups. O cliente precisa migrar para Uazapi e implementar uma lógica inteligente que: (1) encaminhe contatos que não responderam pelo fluxo inicial → follow-up 1 → follow-up 2 → despedida; (2) permita remover manualmente (via Kanban) leads que responderam para "convertido" ou "perdido", evitando envio de follow-up; (3) respeite o limite diário de novas mensagens do plano; (4) quando o limite for atingido ou ao final do dia (~23h), mova contatos ainda em "inicial" para follow-up 1 e crie campanha automática agendada conforme configuração de follow-up.

### Solution

Migrar worker_cadence para Uazapi (POST /sender/advanced com scheduled_for). **Uazapi tem preferência sobre MegaAPI**; MegaAPI e worker legado serão desativados em breve. Manter Kanban existente com colunas Inicial, Follow-up 1, Follow-up 2, Despedida, Convertido, Perdido. Adicionar lógica de "rollover diário": às rollover_time (configurável HH:MM, default 23:00; permite 23:34, 9:01 para testes) move leads em inicial para follow-up 1 e cria campanha Uazapi agendada. Movimentação manual no Kanban para Convertido/Perdido atualiza cadence_status='converted'/'lost' e impede follow-up (worker só processa cadence_status='snoozed').

### Scope

**In Scope:**
1. Migrar worker_cadence de MegaAPI para Uazapi (send_text ou create_advanced_campaign)
2. Manter/adaptar Kanban com etapas: Inicial, Follow-up 1, Follow-up 2, Despedida, Convertido, Perdido
3. Movimentação manual no Kanban: arrastar para Convertido/Perdido → cadence_status='converted'/'lost', sem mais follow-up
4. Respeitar limite diário (License.daily_limit) antes de enviar follow-ups
5. Rollover diário: às rollover_time (ou ao atingir limite), leads em "inicial" (current_step=1, receberam 1ª msg) → mover para follow-up 1; criar campanha Uazapi agendada para o dia seguinte conforme delay configurado
6. Configuração de follow-up: delays entre etapas (dias) configuráveis por step na campanha; horário de rollover com **minutos** (ex: 23:34, 9:01) para facilitar testes; toggle pular final de semana (send_saturday, send_sunday)

**Out of Scope:**
- Detecção automática de resposta via webhook Uazapi (fase posterior; manter Chatwoot opcional ou detecção manual)
- Múltiplas campanhas de follow-up concomitantes por usuário (validar uma primeiro)
- Agente de IA para respostas (roadmap item 4)

## Context for Development

### Codebase Patterns (Investigation Step 2)

| Padrão | Localização | Notas |
|--------|-------------|-------|
| check_daily_limit | worker_sender.py:278 | `check_daily_limit(user_id, plan_limit)` — conta sent_at hoje (BRT), retorna True se pode enviar |
| get_campaign_instance | worker_cadence.py:258 | Retorna `{name, apikey}` de campaign_instances + instances; precisa incluir api_provider para Uazapi |
| move_campaign_lead | app.py:2199 | POST `/api/campaigns/<id>/leads/<id>/move` — body `{target_step, target_status}`; target_status 'converted'/'lost' já impede follow-up (worker só processa cadence_status='snoozed') |
| cadence_config | campaigns.cadence_config (JSONB) | Adicionar `rollover_time` (string "HH:MM", ex: "23:34") para horários quebrados em testes |
| campaign_steps | campaign_steps table | step_number, step_label, message_template, delay_days, media_path, media_type |
| License.daily_limit | app.py:550 | Retorna int por license_type (starter=10, pro=20, scale=30) |
| Uazapi create_advanced_campaign | services/uazapi.py:181 | Payload: delayMin, delayMax (seg), messages, scheduled_for (Unix timestamp) |

### Files to Reference

| File | Purpose |
|------|---------|
| worker_cadence.py | Migrar MegaAPI→Uazapi; adicionar rollover; check_daily_limit; get_campaign_instance para api_provider=uazapi (token em apikey) |
| app.py | Salvar rollover_time em cadence_config; rota move já suporta converted/lost; obter License.daily_limit por user_id |
| services/uazapi.py | create_advanced_campaign já existe; verificar scheduled_for (Unix vs minutos) na OpenAPI |
| templates/campaigns_new.html | Campo rollover_time (time picker HH:MM, ex: 23:34); delay_days por step; toggle send_saturday/send_sunday |
| templates/campaigns_kanban.html | Coluna "Break-up" = Despedida; Convertido/Perdido já existem |
| worker_sender.py | Extrair check_daily_limit para módulo compartilhado (ex: utils/limits.py) para reuso em worker_cadence |

### Technical Decisions (Investigation)

1. **Rollover**: `cadence_config.rollover_time` (string "HH:MM", default "23:00"). Suporta horários quebrados (ex: 23:34, 9:01) para testes. Worker verifica hora:minuto atual (BRT) >= rollover_time; executa rollover uma vez por dia.
2. **Idempotência rollover**: Usar `rollover_applied_at DATE` em campaign_leads ou verificar se lead já está em current_step>=2 antes de mover.
3. **get_campaign_instance**: Preferir instâncias `api_provider='uazapi'`; usar `apikey` como token. MegaAPI será desativada em breve — Uazapi tem prioridade.
4. **Bug existente**: worker_cadence.py linha ~422 usa `unread` sem definir — extrair `unread = cw_data.get('unread_count', 0)` antes do bloco.
5. **scheduled_for**: Verificar spec Uazapi — app.py usa "minutos a partir de agora"; OpenAPI pode exigir Unix timestamp. Ajustar conforme documentação.

### Estado Atual
- worker_cadence.py: MegaAPI, Chatwoot para labels/unread, cadence_status (pending, snoozed, monitoring, stopped, completed)
- campaign_steps: step_number, message_template, delay_days
- Kanban: campaigns_kanban.html com colunas por current_step
- check_daily_limit(user_id, plan_limit) em worker_sender.py
- UazapiService já tem create_advanced_campaign com scheduled_for (Unix timestamp)

### Fluxo Desejado
```
Inicial (1ª msg enviada) 
  → [não respondeu + (limite atingido OU rollover_time)] → Follow-up 1 (campanha agendada)
  → [não respondeu + delay_days] → Follow-up 2
  → [não respondeu + delay_days] → Despedida
  → [respondeu] → Convertido ou Perdido (manual no Kanban, cadence_status=converted/lost)
```

### Exemplo de Cenário de Teste
| Momento | Ação |
|---------|------|
| Segunda 8h | 1ª mensagem enviada |
| Segunda 9h (rollover_time) | Move leads em Inicial → Follow-up 1 |
| Segunda 9:01 | Se delay_days=0: envia Follow-up 1 imediatamente. Se delay_days=1: agenda Terça 8h. Se delay_days=2: agenda Quarta 8h. |
| Pular fim de semana | Com send_saturday=false, send_sunday=false: ao contar delay_days, pular sáb/dom (ex: Sexta 8h + 2 dias = Terça 8h) |

### Decisões Técnicas Preliminares
- **Rollover**: `cadence_config.rollover_time` (string "HH:MM", default "23:00"). Time picker com minutos (ex: 23:34, 9:01) para testes.
- **delay_days**: Configurável por step em campaign_steps (já existe). UI deve expor em cada etapa (Follow-up 1, 2, Despedida). delay_days=0 = envio no próximo ciclo (minutos); delay_days>=1 = próximo dia útil no horário send_hour_start (ex: 8h).
- **Pular fim de semana**: Usar send_saturday, send_sunday da campanha. Ao calcular scheduled_for/snooze_until, não contar sáb/dom quando toggle off.
- Campanha agendada: Uazapi create_advanced_campaign com scheduled_for = próximo dia útil no horário comercial (send_hour_start)
- Limite diário: extrair check_daily_limit para módulo compartilhado; worker_cadence chama antes de enviar batch
- **UI**: Campo rollover_time — time picker HH:MM (permite 23:34, 9:01); label "Horário de rollover (leads em Inicial → Follow-up 1)"

## Acceptance Criteria

- [ ] **AC1**: Given campanha com cadência Uazapi e rollover_time="23:34", when for 23:34 BRT, then leads em Inicial (current_step=1, status=sent) são movidos para Follow-up 1 e campanha Uazapi é criada agendada.
- [ ] **AC1b**: Given rollover_time="09:01" (teste), when for 9:01 BRT, then rollover executa — permite validar fluxo em horário próximo.
- [ ] **AC2**: Given lead no Kanban arrastado para Convertido, when move concluído, then cadence_status='converted' e worker não envia mais follow-up.
- [ ] **AC3**: Given lead arrastado para Perdido, when move concluído, then cadence_status='lost' e worker não envia mais follow-up.
- [ ] **AC4**: Given usuário com daily_limit=10 já atingido hoje, when worker processa follow-ups, then nenhum envio é feito.
- [ ] **AC5**: Given create_advanced_campaign falha no rollover, when erro retornado, then leads NÃO são movidos (rollback); retry no próximo ciclo.
- [ ] **AC6**: Given campanha com instâncias Uazapi e MegaAPI, when get_campaign_instance, then retorna instância Uazapi (prioridade).
- [ ] **AC7**: Given delay_days=0 no step Follow-up 1, when lead entra em Follow-up 1, then envia no próximo ciclo do worker (~1 min).
- [ ] **AC8**: Given delay_days=2 e send_saturday=false, send_sunday=false, when lead em Sexta 8h, then agenda Terça 8h (pula sáb/dom).

## Rollback e Mitigação de Riscos

| Risco | Mitigação |
|-------|------------|
| Uazapi indisponível no rollover | Retry com backoff; não atualizar current_step em caso de falha |
| Rollover executado 2x (worker restart) | Idempotência: só mover leads com current_step=1 e status=sent |
| scheduled_for formato errado | Verificar OpenAPI; teste unitário com valor de exemplo |
| Usuário sem licença ativa | Validar License antes de rollover; pular campanhas sem licença |

## Cenário de Teste (delay_days + pular fim de semana)

- **delay_days=0**: Lead em Follow-up 1 → envia no próximo ciclo do worker (ex: 1 min).
- **delay_days=1**: Lead recebeu 1ª msg Segunda 8h; rollover Segunda 9h; Follow-up 1 agendado para Terça 8h (send_hour_start).
- **delay_days=2**: Follow-up 1 agendado para Quarta 8h.
- **Pular fim de semana**: Se send_saturday=false, send_sunday=false — Sexta 8h + 2 dias = Terça 8h (pula sáb/dom).

---

## Resumo das Alterações Aplicadas (2026-03-07)

### Correções de rollover e follow-up

| # | Alteração | Arquivo(s) | Descrição |
|---|-----------|------------|-----------|
| 1 | **SQL NULL em cadence_status** | worker_cadence.py | `COALESCE(cl.cadence_status, '') NOT IN ('converted','lost')` — leads com cadence_status=NULL passam a ser incluídos no rollover |
| 2 | **Logs de debug** | worker_cadence.py | Logs quando rollover retorna early (instância MegaAPI, sem leads elegíveis com contagem por status) |
| 3 | **rollover_time=00:00 e Modo teste** | worker_cadence.py, campaigns_new.html, app.py | rollover_time=00:00 ou `rollover_test_mode: true` faz rollover rodar em todo ciclo (~2 min) |
| 4 | **Time picker step=1** | campaigns_new.html | Permite 23:34, 9:01 para testes |
| 5 | **delay_days=0 tratado corretamente** | worker_cadence.py | `delay_days or 1` convertia 0 em 1. Corrigido para `1 if delay_days is None else int(delay_days)`. Quando delay_days<=0, usa timedelta(minutes=2) |
| 6 | **Marcar leads como sent ao criar campanha Uazapi** | app.py | Após create_advanced_campaign OK, UPDATE campaign_leads SET status='sent', current_step=1 WHERE status='pending' |
| 7 | **scheduled_for em milissegundos** | worker_cadence.py | Uazapi API espera Unix timestamp em ms; alterado para `int(target_dt.timestamp() * 1000)` |
| 8 | **Armazenar rollover_fu1_folder_id** | worker_cadence.py | Salva folder_id em cadence_config para permitir cancelar |
| 9 | **Botão Cancelar FU1 agendado** | app.py, campaigns_kanban.html | Endpoint POST /api/campaigns/<id>/cancel-rollover; botão no Kanban quando há follow-up agendado |
| 10 | **UAZAPI no container cadence** | docker-compose.yml, docker-compose.dev.yml | Variáveis UAZAPI_URL e UAZAPI_ADMIN_TOKEN no serviço cadence; serviço cadence adicionado ao dev |
| 11 | **Timezone BRT em "Criada"** | app.py, campaigns_list.html, admin/campaigns.html | Filtro Jinja `to_brt` para exibir created_at em BRT |
| 12 | **Checkbox Modo teste** | campaigns_new.html, app.py | Checkbox "Modo teste: rollover e avanço automático em todo ciclo"; define delay_days=0 em todos os steps |
| 13 | **delay_days min=0 na UI** | campaigns_new.html | Inputs "Aguardar (dias)" passam a aceitar 0 nos 3 follow-ups |
| 14 | **Rollover FU1→FU2 e FU2→Despedida** | worker_cadence.py | `process_rollover_fu_next()`: leads em current_step=2 ou 3 com snooze_until<=NOW() → criar campanha Uazapi agendada e mover para step 3 ou 4. Cards avançam no Kanban após FU1/FU2 enviados. |
| 15 | **scheduled_for em ms (rollover Inicial→FU1)** | worker_cadence.py | `int(target_dt.timestamp() * 1000)` — Uazapi espera Unix timestamp em milissegundos |
