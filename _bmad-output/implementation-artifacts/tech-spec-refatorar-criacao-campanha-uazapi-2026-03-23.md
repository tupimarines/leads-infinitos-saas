---
title: 'Refatorar criação de campanha (Uazapi, pacing, rollover)'
slug: refatorar-criacao-campanha-uazapi-2026-03-23
created: '2026-03-23'
status: implemented
stepsCompleted: [1, 2, 3, 4]
baseline_commit: 930e3b48ddbaf3696934b9160f9632518c71075a
---

# Tech-Spec: Refatorar criação de campanha (Uazapi)

## Overview

### Problem Statement

A UI de criação expunha toggle “Envio em massa 2.0”, delays min/max manuais, rollover por horário e modo teste. Era necessário humanizar o ritmo no backend, alinhar sub-campanhas Uazapi por faixa de atraso, respeitar janela de envio por campanha e avançar follow-up 1 quando a API confirmar todos os envios do bloco inicial.

### Solution

- `use_uazapi_sender` inferido no backend quando há ao menos uma instância Uazapi selecionada; UI informativa sem toggle/delays manuais.
- `utils/uazapi_pacing.py`: buckets ponderados (30% / 20% / 40%) para intervalos entre mensagens; 10% de chance de pausa longa entre sub-campanhas (não cria campanha só de pausa).
- `worker_cadence._materialize_scheduled_stage_sends`: para `initial` + Uazapi, divide o chunk em sub-segmentos; primeiro segmento cria folder na linha atual; demais viram novas linhas `campaign_stage_sends` agendadas com `lead_ids` já fixados.
- Exclusão de leads pendentes considera também linhas `scheduled` com `lead_ids` preenchidos.
- Janela: `is_campaign_send_window(campaign)` substitui `is_business_hours()` para `process_campaign_sends` / `bootstrap`; materialize Uazapi não dispara fora da janela.
- Rollover FU1: `campaign_stage_sends.fu_rollover_done` + `process_uazapi_initial_stage_rollovers` quando `status=done`, `success_count >= planned_count` e `success_count+failed_count >= planned_count`.
- `process_rollover_fu_next` passa a rodar também para campanhas `use_uazapi_sender` (follow-ups 2 e 3).

### Scope

**In scope:** `templates/campaigns_new.html`, `app.py` (create campaign, continue chunk, migração `fu_rollover_done`), `worker_cadence.py`, `utils/uazapi_pacing.py`.

**Out of scope:** Scripts E2E em produção (solicitação do usuário); `campaigns_kanban.html` (delays por etapa no Kanban permanecem se existirem).

## Acceptance criteria (resumo)

- Dado cadência + Uazapi, quando um chunk inicial conclui com sucesso na contagem da API, então leads elegíveis avançam para FU1 sem horário de rollover manual.
- Dado criação com instância Uazapi, então o ritmo entre mensagens não depende de campos min/max na UI.
- Dado horário de envio 8–20 e sábado desligado, então materialização Uazapi e envios Mega respeitam a janela e o fim de semana.

## Testing strategy

Testes manuais recomendados: criar campanha com cadência + 1 instância Uazapi; verificar múltiplos `campaign_stage_sends` para o mesmo slot; após `list_folders` com `log_success` = planejado, verificar movimento no Kanban para FU1. CI: não adicionado neste entregável.
