---
title: 'Desconexão WhatsApp — pausa de campanhas e retoma após reconexão (outbox Uazapi)'
slug: desconexao-whatsapp-pausa-retoma-campanhas
created: '2026-05-01'
status: ready-for-dev
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  - Python 3.x
  - Flask
  - PostgreSQL (psycopg2)
  - Workers (`worker_cadence.py`, `worker_message_outbox.py`)
files_to_modify:
  - app.py
  - worker_cadence.py
  - worker_message_outbox.py
  - utils/uazapi_support_notify.py
  - utils/uazapi_error_taxonomy.py (ou novo módulo auxiliar)
code_patterns:
  - RealDictCursor em workers; SQL com `%s`
  - Feature flag `USE_MESSAGE_OUTBOX` em `utils.config`
  - SSOT envio confirmado = sucesso na API Uazapi (`result_json` truthy → HTTP 200 implícito em `send_*`)
test_patterns:
  - pytest em `tests/` (padrão existente do projeto)
---

# Tech-Spec: Desconexão WhatsApp — pausa de campanhas e retoma após reconexão (outbox Uazapi)

**Criado:** 2026-05-01

## Overview

### Problem Statement

Hoje o disparo via **fila outbox** (`campaign_message_outbox`) não tem um fluxo unificado quando a **instância Uazapi / WhatsApp desliga**: falhas de envio em `_persist_outcome` tratam qualquer não-sucesso como **`failed`** terminal; não há pausa coordenada ao nível da campanha nem distinção consistente entre erro **transitório** (desconexão, 503, sem sessão) e falha definitiva. O legado **advanced** já usa `waiting_reconnect` em `campaign_stage_sends` e `_resume_waiting_reconnect_stage_sends` no `worker_cadence.py`, mas o **outbox** não replica essa semântica — risco de “queimar” leads e de duplicar ou bloquear envios indevidamente.

### Solution

1. **Detecção** de desconexão com a mesma base do legado: `UazapiService.get_status` + `is_instance_disconnected_status` / `get_instance_status_cached` (`utils/uazapi_support_notify.py`), executada periodicamente no worker (ou job dedicado no mesmo ciclo).
2. **Pausa automática** das campanhas **running/pending** que dependem da instância desligada (via `campaign_instances` + uso Uazapi), com **motivo persistido** distinguindo pausa **sistema** vs **utilizador**.
3. **Outbox**: classificar falhas de envio; erros **disconnect / instância indisponível / HTTP transitório** **não** marcam `failed` terminal sem política — preferir **`pending`** com `next_run_at` em backoff ou estado dedicado (ex. `waiting_instance`) + registo em `campaign_send_attempts`.
4. **Reconexão**: ao detectar transição **desligado → ligado**, **notificar** (in-app + reutilizar `maybe_send_disconnect_support_whatsapp` / padrões existentes) e permitir **retoma** segura: SSOT continua a ser confirmação actual pós-envio (resposta JSON truthy da API); **`track_id` / idempotência** mantidos para mitigar duplo envio.
5. **Dual-run**: documentar comportamento paralelo **outbox** vs **advanced** (`waiting_reconnect`) para não haver UX contraditória.

### Scope

**In scope:**

- Modelo de dados mínimo para **motivo de pausa sistema** e, se necessário, **estado estendido** na outbox (ex. novo valor de `status` ou coluna auxiliar).
- **Job/tick de saúde** por instância Uazapi com campanhas activas ou fila outbox não vazia.
- Alterações em **`_persist_outcome`** e no fluxo **antes**/**depois** do HTTP para classificar erros.
- **UI/API**: aviso ao utilizador (instância + contagem de campanhas); CTA **Retomar** alinhado ao fluxo existente de pause/resume em `app.py`.
- Alinhamento com **`_resume_waiting_reconnect_stage_sends`** apenas a nível de **regras de produto e documentação** (sem obrigar refactor completo do legado).

**Out of scope (MVP explícito):**

- Alterar contrato HTTP da Uazapi além do já implementado em `services/uazapi.py`.
- SSE ou push em tempo real no browser.
- Priorização global entre múltiplas campanhas.
- Migração massiva de campanhas legadas para outbox.

## Context for Development

### Codebase Patterns

- **Worker cadência**: loop principal em `process_cadence()` chama `_resume_waiting_reconnect_stage_sends`, depois `process_message_outbox_tick` quando `USE_MESSAGE_OUTBOX` (`worker_cadence.py`).
- **Outbox**: `process_message_outbox_tick` selecciona apenas `c.status IN ('running', 'pending')` — campanhas **`paused`** deixam de ser elegíveis automaticamente.
- **Sucesso envio**: `success = bool(result_json)` após `send_text_idempotent` / `send_media_campaign`; só então `_persist_outcome` promove outbox para `sent` e actualiza lead (`worker_message_outbox.py`).
- **Falha**: ramo `else` em `_persist_outcome` faz `UPDATE campaign_message_outbox SET status = 'failed'` sem distinção de causa.
- **Legado reconnect**: `_resume_waiting_reconnect_stage_sends` usa `get_instance_status_cached` e só promove para `scheduled` se **não** `is_instance_disconnected_status(st)` (`worker_cadence.py`).
- **Taxonomia**: `utils/uazapi_error_taxonomy.py` classifica erros de `create_advanced_campaign`; pode servir de modelo para uma função **`classify_outbox_send_failure`** (corpo/HTTP/`None`).

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `worker_message_outbox.py` | `process_message_outbox_tick`, `_persist_outcome`, claim `pending`→`sending`, terminal `failed`/`sent`. |
| `worker_cadence.py` | `_resume_waiting_reconnect_stage_sends`, ordem de chamadas no loop principal. |
| `utils/uazapi_support_notify.py` | `get_instance_status_cached`, `is_instance_disconnected_status`, `maybe_send_disconnect_support_whatsapp`. |
| `services/uazapi.py` | `get_status` → GET `/instance/status` (`connected` / `connecting` / `disconnected`). |
| `app.py` | Rotas pause/resume campanha, `campaign_instances`, init DB `campaign_message_outbox`. |
| `utils/uazapi_error_taxonomy.py` | Padrão de classificação `no_session`, `transient_http`, etc. |

### Technical Decisions

| Decisão | Escolha recomendada | Motivo |
| ------- | ------------------- | ------ |
| Âmbito da pausa | `campaigns.status = 'paused'` + colunas de **pausa sistema** | Reutiliza filtro existente na query outbox; utilizador já reconhece “pausada”. |
| Distinguir pausa manual | Colunas novas, ex. `campaigns.pause_origin` (`'user' \| 'system' \| NULL`) e `campaigns.pause_reason_code` (`NULL` \| `'instance_disconnected'`) | Evita sobrescrever intenção do utilizador e permite copy específica. |
| Estado outbox transitório | Preferir manter `pending` + `next_run_at` **ou** `status = 'waiting_instance'` | `failed` só para erros definitivos (template inválido, 4xx persistente, etc.). |
| Onde correr saúde | Mesmo processo `worker_cadence` com intervalo configurável (env) | Menos moving parts; já existe `get_status` cache 60s. |
| Idempotência | Manter `idempotency_key` / `track_id` como hoje | Mitiga duplo envio após retoma. |

## Implementation Plan

### Tasks

- [ ] **Task 1 — Migração PostgreSQL (campanhas + opcional outbox)**  
  - File: `app.py` (`_init_db_body` ou bloco de migrações existente) **e/ou** script `migrate_*.py` se o projecto preferir migração separada.  
  - Action: Adicionar colunas em `campaigns`: `pause_origin` TEXT, `pause_reason_code` TEXT, `system_paused_at` TIMESTAMP NULL (nomes finais podem ajustar-se ao estilo actual). Garantir compatibilidade com campanhas existentes (NULL = comportamento actual).  
  - Opcional: `campaign_message_outbox.status` permitir valor `'waiting_instance'` **ou** coluna `transient_hold` BOOLEAN + manter `pending`.  
  - Notes: Se existir CHECK/DROP em `campaigns.status` na BD real, validar se `'paused'` já é permitido (o código usa `'paused'` nas rotas; o CREATE inicial antigo pode divergir — confirmar em ambientes).

- [ ] **Task 2 — Função de classificação de falha de envio outbox**  
  - File: novo `utils/uazapi_outbox_errors.py` **ou** extensão de `utils/uazapi_error_taxonomy.py`.  
  - Action: Implementar `classify_outbox_send_failure(http_status, response_body, result_json, exception_class) -> Literal['terminal', 'retry_backoff', 'instance_unreachable']` com regras alinhadas ao produto: `None`/timeout → `instance_unreachable` ou `retry_backoff`; corpo com “no session” / “desconect” → `instance_unreachable`; 502/503/504 → `retry_backoff`; 4xx claros → `terminal`.  
  - Notes: Documentar matriz num comentário curto no módulo.

- [ ] **Task 3 — `_persist_outcome` ramificado**  
  - File: `worker_message_outbox.py`.  
  - Action: No ramo `else` (não sucesso), em vez de só `status='failed'`:  
    - Se `instance_unreachable` ou `retry_backoff`: repor ou manter `pending`, limpar `sending`, definir `next_run_at` (backoff exponencial com teto), incrementar contador opcional de tentativas; **não** marcar lead como `sent`.  
    - Se `terminal`: manter comportamento actual (`failed`).  
  - Notes: Garantir que `campaign_send_attempts` continua a registar **todas** as tentativas com `outcome` descritivo (`failed_terminal` vs `retry_scheduled`).

- [ ] **Task 4 — Tick de saúde da instância + pausa em cascata**  
  - File: `worker_cadence.py` (função nova, ex. `_pause_campaigns_for_disconnected_instances`) **ou** `utils/uazapi_support_notify.py` se preferir API pura.  
  - Action: Periodicamente (ex. a cada 60–120s ou a cada N iterações do loop): para cada `instance_id` que tenha pelo menos uma campanha `running`/`pending` com `use_uazapi_sender` e ligação em `campaign_instances`, chamar `get_instance_status_cached`. Se `is_instance_disconnected_status`: UPDATE `campaigns` SET `status='paused'`, `pause_origin='system'`, `pause_reason_code='instance_disconnected'`, `system_paused_at=NOW()` onde `id` em (campanhas afectadas) **e** `status IN ('running','pending')`.  
  - Notes: Idempotência do UPDATE; não apagar filas outbox.

- [ ] **Task 5 — Detecção de reconexão + notificação**  
  - File: `utils/uazapi_support_notify.py` + `app.py` (flash/toast) ou template dashboard.  
  - Action: Manter registo do último estado por instância (memória worker ou coluna `instances.last_uazapi_status` + timestamp). Na transição disconnected→connected: limpar bloqueio lógico; opcionalmente enviar evento in-app (session flag) “Existem N campanhas pausadas por desconexão da instância X”. Reutilizar cooldown de `maybe_send_disconnect_support_whatsapp` onde fizer sentido **ou** mensagem distinta para reconexão (nova função irmã).  
  - Notes: Não enviar spam — um resumo por utilizador/instância por janela.

- [ ] **Task 6 — Retoma segura (API + UI)**  
  - File: `app.py`, templates relevantes (`templates/` campanha/dashboard).  
  - Action: Na rota de **resume** existente: se `pause_reason_code == 'instance_disconnected'`, validar `get_status` **ou** avisar risco e exigir confirmação. Ao retomar: `status='running'`, limpar `pause_reason_code` / `pause_origin` conforme política (ex. só limpar se origem sistema).  
  - Notes: Não alterar linhas outbox `sent`; `pending`/`waiting_instance` processam normalmente após `running`.

- [ ] **Task 7 — Legado advanced (documentação + smoke)**  
  - File: comentário em `worker_cadence.py` ou doc em `_bmad-output/implementation-artifacts/` (apenas se necessário para equipa).  
  - Action: Parágrafo explícito: `waiting_reconnect` aplica-se a `campaign_stage_sends`; outbox usa `pending`/`waiting_instance` — ambos dependem de `get_status` para reconnect.

- [ ] **Task 8 — Testes**  
  - File: `tests/test_outbox_disconnect_policy.py` (novo) ou ficheiro existente de worker.  
  - Action: Testes unitários para `classify_outbox_send_failure`; teste de integração leve com BD mock ou fixtures: falha transitória não produz `campaign_message_outbox.status='failed'`; campanha pausada sistema não é seleccionada pelo SELECT do tick.

### Acceptance Criteria

- [ ] **AC1:** Dado uma instância Uazapi em estado **desligado** (`is_instance_disconnected_status`), quando o job de saúde executa, então todas as campanhas **running/pending** que usam essa instância em `campaign_instances` passam a **`paused`** com **`pause_origin=system`** e motivo **desconexão**, e permanecem pausadas até acção de retoma ou reconexão conforme política definida.

- [ ] **AC2:** Dado o utilizador com campanha pausada por sistema por desconexão, quando abre o dashboard (ou página de campanhas), então vê **mensagem clara** com **nome/ID da instância** e **número de campanhas** afectadas (ou lista resumida).

- [ ] **AC3:** Dado um envio outbox que falha por **desconexão / sem sessão / 503** sem confirmação de envio (`result_json` falso), quando `_persist_outcome` corre, então **não** actualiza `campaign_leads` como enviado com sucesso e a linha outbox **não** é fechada como `failed` **definitivo** sem política — fica elegível a **retry** após reconexão/backoff.

- [ ] **AC4:** Dado um envio que já recebeu **confirmação** (fluxo actual: `success` com actualização para `sent`), quando a campanha é retomada após reconexão, então **não** há segundo envio para o mesmo `idempotency_key` / mesma linha outbox já `sent`.

- [ ] **AC5:** Dado transição **desligado → ligado** detectada para uma instância, quando o utilizador clica **Retomar** (ou política de auto-retoma, se implementada), então `process_message_outbox_tick` volta a processar linhas **`pending`** dessa campanha sem duplicar envios já **`sent`**.

- [ ] **AC6:** Dado campanha pausada **manualmente** pelo utilizador (`pause_origin=user`), quando o job de saúde detecta desconexão, então o sistema **não** altera o estado para uma confusão com pausa sistema **ou** documenta regra explícita se produto quiser sobrescrever (escolher uma e testar).

- [ ] **AC7:** Dado falha **terminal** real (ex. template em falta, telefone inválido já tratado), quando `_persist_outcome` corre, então comportamento permanece **failed** / lead não marcado como sucesso, como hoje.

### Additional Context

#### Dependencies

- API Uazapi disponível e token válido para `get_status`.
- Variáveis de ambiente existentes (`USE_MESSAGE_OUTBOX`, notificações support) + novas opcionais (intervalo health check, max backoff).

#### Testing Strategy

- **Unitário:** classificação de erros; máquina de estados em memória para pausa/reconexão.  
- **Integração:** PostgreSQL de teste — INSERT campanha + outbox `pending`, simular classificação e verificar UPDATE final.  
- **Manual:** desligar instância na Uazapi (ou mock), verificar pausa e banner; reconectar e retomar.

#### Notes

- **Risco:** falsos positivos em `get_status` (rede intermitente) podem pausar campanhas sem necessidade — mitigar com **histerese** (N falhas consecutivas antes de pausa) ou debounce por instância.  
- **Risco:** colisão entre pausa manual e sistema — exigir colunas `pause_origin` para UX clara.  
- **Dual-run:** equipas com campanha **advanced** + migração outbox devem ver documentação alinhada com `tech-spec-fase1-outbox-superadmin-dual-run.md`.

---

**Próximo passo sugerido:** implementação em contexto fresco (`quick-dev` com este ficheiro), começando por Task 2–3 (classificação + `_persist_outcome`) em conjunto com Task 1 (schema).
