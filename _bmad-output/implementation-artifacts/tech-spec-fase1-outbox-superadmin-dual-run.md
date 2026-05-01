---
title: 'Fase 1: outbox no criador normal /campaigns/new (superadmin) + dual-run com legado advanced'
slug: 'fase1-outbox-superadmin-dual-run'
created: '2026-05-01T23:30:00Z'
updated: '2026-05-01T23:30:00Z'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3', 'Flask', 'PostgreSQL', 'pytest', 'worker_cadence']
files_to_modify:
  - 'app.py'
  - 'tests/test_admin_campaign_crud.py'
code_patterns:
  - '_create_campaign_core(..., admin_id=)'
  - 'use_outbox_enqueue = USE_MESSAGE_OUTBOX and _phase1_outbox_operator_is_superadmin(admin_id)'
  - 'worker_cadence: legado (campaign_stage_sends) + process_message_outbox_tick em paralelo'
test_patterns:
  - 'pytest monkeypatch USE_MESSAGE_OUTBOX + login superadmin + mock create_advanced_campaign'
---

# Tech-Spec: Fase 1 — outbox em `/campaigns/new` (só superadmin) e dual-run (sem migração de legado)

**Criado:** 2026-05-01  
**Actualizado:** 2026-05-01 (pivô de produto: **sem** continuidade/migração de campanhas existentes)  
**Projeto:** leads-infinitos-saas  
**Idioma:** pt-BR

---

## Overview

### Problem Statement

1. Com **`USE_MESSAGE_OUTBOX`** ligado, o enqueue em **`campaign_message_outbox`** na criação só corre quando **`_phase1_outbox_operator_is_superadmin(admin_id)`** é verdadeiro. O **`POST /api/campaigns`** (`/campaigns/new`) não passa **`admin_id`**, logo superadmins não conseguem usar o criador “normal” para novas campanhas **outbox** — apenas o fluxo admin “para qualquer utilizador” funciona.

2. **Produto (decisão actual):** não haverá **migração** nem “continuação” automática de campanhas antigas **advanced** para outbox. Campanhas já criadas por advanced **mantêm-se** no modelo folder/chunks enquanto existirem e estiverem activas.

### Solution

1. **Backend (esta spec / Fase 1):** Em **`POST /api/campaigns`**, quando **`USE_MESSAGE_OUTBOX`** estiver activo **e** **`is_super_admin()`**, passar **`admin_id=current_user.id`** para **`_create_campaign_core`**, preservando ADR-4 (gate superadmin + flag).

2. **Novas campanhas (após patch + env):** na prática, **criação** por utilizadores abrangidos pelo gate usa **só outbox** (sem `create_advanced_campaign`) quando `use_outbox_enqueue` for verdadeiro — igual ao comportamento já existente no **`POST /api/admin/campaigns`**, mas agora também a partir de **`/campaigns/new`** para o próprio superadmin.

3. **Dual-run operacional:** O **`worker_cadence`** continua a processar **legado** (`campaign_stage_sends`, pastas, `create_advanced_campaign` nos ramos existentes) para campanhas que já seguem esse modelo (**ex.: `scheduled` / `running`** no fluxo actual de chunks). Em paralelo, **`process_message_outbox_tick`** processa **novas** campanhas criadas só com fila outbox. **Não** se mistura o mesmo “motor” na mesma campanha: campanha antiga = legado até ao fim; campanha nova (pós-regra) = outbox.

### Scope

**In scope**

- Patch **`create_campaign`** em **`app.py`** + testes pytest alinhados a **`tests/test_admin_campaign_crud.py`** (mock `create_advanced_campaign`, flag on).
- Documentação nesta spec do **dual-run** e da **Fase 2** (próximo patch: todos os utilizadores, **apenas** novas campanhas).

**Explicitamente fora de escopo (esta entrega)**

- **Migração** de campanhas existentes advanced → outbox (API, CLI operacional, offset `waiting_reconnect`, etc.).
- Remoção do código legado ou de `create_advanced_campaign` globalmente.
- Abrir outbox a **todos** os utilizadores (isso é **próximo patch** com relaxamento do gate ADR-4 só na **criação**).

**Nota:** O script **`scripts/migrate_campaign_to_outbox.py`** pode permanecer no repositório para emergências/manutenção, mas **não** faz parte do rollout planeado.

---

## Context for Development

### Dual-run (resumo técnico)

| Tipo de campanha | Mecanismo de envio inicial (alto nível) |
| ---------------- | ---------------------------------------- |
| **Já existente** criada via advanced (tem pasta / chunks em curso) | Continua **legado** (`worker_cadence` + `campaign_stage_sends`, etc.) até terminar o ciclo ou política operacional. |
| **Nova** criada com `use_outbox_enqueue` | **Só** fila **`campaign_message_outbox`** + envio unitário Uazapi (sem `create_advanced_campaign` nesse ramo). |

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `app.py` | `create_campaign`, `_create_campaign_core`, `use_outbox_enqueue` |
| `worker_cadence.py` | Legado + chamada `process_message_outbox_tick` quando flag |
| `worker_message_outbox.py` | Tick outbox; filtra `campaigns.status` |
| `utils/config.py` | `USE_MESSAGE_OUTBOX`, `SUPER_ADMIN_EMAILS` |
| `tests/test_admin_campaign_crud.py` | Padrões de teste outbox |

### Technical Decisions

| ID | Decisão |
| -- | ------- |
| D1 | Passar `admin_id` só quando `USE_MESSAGE_OUTBOX` **e** `is_super_admin()` (evita alterar `created_by_admin_id` sem necessidade em ambientes só legado). |
| D2 | **Sem migração:** não introduzir novas rotas `migrate-preview` / `migrate` nesta fase. |
| D3 | **Próximo patch (Fase 2):** permitir `use_outbox_enqueue` para utilizadores não superadmin **apenas** em **`POST /api/campaigns`** (novas campanhas); critério exacto de gate (ex.: só flag env) documentado noutra spec. |

---

## Implementation Plan

### Tasks

- [ ] **Task 1:** `app.py` — Na rota `POST /api/campaigns` (`create_campaign`), definir `admin_id = current_user.id` se `USE_MESSAGE_OUTBOX` e `is_super_admin()`, caso contrário `None`; chamar `_create_campaign_core(current_user.id, request.json, admin_id=admin_id)`. Actualizar docstring breve.
- [ ] **Task 2:** `tests/` — Cobrir: com flag on + superadmin, `POST /api/campaigns` não invoca `create_advanced_campaign` (padrão dos testes admin existentes); utilizador não superadmin inalterado.

### Acceptance Criteria

- [ ] **AC1:** Dado `USE_MESSAGE_OUTBOX` ligado e email em `SUPER_ADMIN_EMAILS`, quando o utilizador cria campanha Uazapi via `POST /api/campaigns`, então `use_outbox_enqueue` pode ser verdadeiro e **não** se chama `create_advanced_campaign` para essa criação (mesma semântica que `POST /api/admin/campaigns` para si próprio).
- [ ] **AC2:** Dado utilizador **não** superadmin, quando chama `POST /api/campaigns`, então comportamento permanece o mesmo que antes do patch (sem `admin_id` para gate outbox).
- [ ] **AC3:** Dado `USE_MESSAGE_OUTBOX` desligado, quando superadmin chama `POST /api/campaigns`, então comportamento legado inalterado (sem dependência de `admin_id` para outbox).
- [ ] **AC4 (regressão dual-run):** Campanhas existentes que já usam advanced **não** são alteradas por este patch (sem migração de dados; worker legado continua aplicável).

---

## Additional Context

### Dependencies

- Env: `USE_MESSAGE_OUTBOX`, `SUPER_ADMIN_EMAILS`.
- Worker `worker_cadence` em execução com tick outbox.

### Testing Strategy

- Pytest com monkeypatch do env e patch de `UazapiService.create_advanced_campaign`, espelhando `test_admin_campaign_crud.py`.

### Notes — roadmap

- **Fase 2 (próximo patch):** todos os utilizadores, **somente** ao criar **nova** campanha — relaxar gate (`admin_id` / superadmin) conforme decisão de produto; mantendo campanhas antigas em advanced até ao fim natural.

---

## Anexo A — Explicação detalhada da antiga «pergunta 1» (`waiting_reconnect`)

Esta pergunta só era **crítica** no desenho de **migração** (hoje **fora de escopo**). Mantém-se aqui como referência técnica.

**Contexto:** Para decidir o **primeiro lead** a enfileirar na outbox **sem duplicar** envios que o advanced já tinha coberto, um script de migração percorre os registos **`campaign_stage_sends`** da etapa **`initial`** (ordem `id ASC`) e acumula um **offset** na lista canónica de leads.

No código de referência (`scripts/migrate_campaign_to_outbox.py`), o **delta** por linha funciona assim (simplificado):

- **`scheduled` / `failed` / `queued`:** o chunk inteiro conta como “já ocupado” → `delta = n` (tamanho do chunk).
- **`done`:** usa `success_count` (fallback no tamanho planeado).
- **`running` / `partial`:** usa tentativas já contabilizadas (`success_count + failed_count`, com fallbacks).
- **Qualquer outro status** no `else`:** `delta = 0` — o offset **não** avança para esse registo.

**Onde entra `waiting_reconnect`:** em `utils/limits.py`, `waiting_reconnect` está em **`INITIAL_CHUNK_ACTIVE_SEND_STATUSES`** — é um estado em que a materialização do chunk pode estar **suspensa** até a instância Uazapi voltar. No script de migração, **se** `waiting_reconnect` não tiver ramo próprio, cai no **`else`** → **`delta = 0`**. Isso significa: “este registo **não** move o ponteiro de offset”. Dependendo do significado operacional (já houve envios parciais ou não), isso pode:

- **subestimar** quantos leads já foram “consumidos” pelo legado (risco de re-fila duplicada na outbox), ou
- ser **correcto** se o chunk ainda não consumiu posições na ordenação.

Daí a pergunta de produto original: **tratar `waiting_reconnect` como `scheduled` (delta = n)** vs **como `running` (contagens parciais)** vs **manter delta 0**.

**Com o plano actual (sem migração em massa), não é necessário fechar esta regra para implementar a Fase 1.**

---

## Validação rápida

1. Superadmin + flag on: criar em `/campaigns/new` → log de enqueue outbox, sem `create_advanced_campaign OK`.  
2. Campanha advanced antiga em execução: comportamento de worker legado **inalterado** por este patch.  
3. Próximo patch: validar gate para utilizadores normais só na **criação**.

---

**Próximo passo BMAD quick-dev:**

```text
quick-dev _bmad-output/implementation-artifacts/tech-spec-fase1-outbox-superadmin-dual-run.md
```
