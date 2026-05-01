---
title: 'Superadmin: fila outbox ao criar campanha em /campaigns/new (POST /api/campaigns)'
slug: 'superadmin-outbox-via-campaigns-new'
created: '2026-05-01T20:00:00Z'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3', 'Flask', 'PostgreSQL', 'pytest']
files_to_modify:
  - 'app.py'
  - 'tests/test_admin_campaign_crud.py'
code_patterns:
  - '_create_campaign_core(user_id, data, admin_id=None)'
  - 'use_outbox_enqueue = USE_MESSAGE_OUTBOX and _phase1_outbox_operator_is_superadmin(admin_id)'
  - 'POST /api/campaigns → create_campaign() hoje sem admin_id'
test_patterns:
  - 'pytest + monkeypatch USE_MESSAGE_OUTBOX + cliente logado superadmin'
---

> **Supersedido por:** `tech-spec-fase1-outbox-superadmin-dual-run.md` (dual-run, sem migração legado; roadmap Fase 2 para todos os users só em novas campanhas).

# Tech-Spec: outbox (envio individual) para superadmin no criador normal `/campaigns/new`

**Criado:** 2026-05-01  
**Projeto:** leads-infinitos-saas  
**Idioma:** pt-BR

## Overview

### Problem Statement

Com **`USE_MESSAGE_OUTBOX`** ligado, o enqueue para **`campaign_message_outbox`** (envio individual, sem `create_advanced_campaign` na criação) só ocorre quando `_phase1_outbox_operator_is_superadmin(admin_id)` é verdadeiro. O **`admin_id`** só é passado em **`_create_campaign_core`** a partir do fluxo **`POST /api/admin/campaigns`** (`admin_id=current_user.id`). No fluxo utilizador **`POST /api/campaigns`** (`/campaigns/new`), **`admin_id` fica `None`**, logo **`use_outbox_enqueue`** é sempre falso — mesmo para utilizadores cujo email está em **`SUPER_ADMIN_EMAILS`**.

Resultado: o superadmin é forçado a usar o painel “criar campanha para qualquer usuário” para obter o novo módulo, em vez de usar o criador normal para a **própria** conta.

### Solution

Na rota **`create_campaign`** (`POST /api/campaigns`), quando o ambiente tiver **`USE_MESSAGE_OUTBOX`** ativo **e** o utilizador autenticado for superadmin (`is_super_admin()`), chamar **`_create_campaign_core(current_user.id, request.json, admin_id=current_user.id)`**.

Assim reutiliza-se o gate existente **`_phase1_outbox_operator_is_superadmin`**, que em contexto HTTP exige `current_user.id == admin_id` e `is_super_admin()` — coerente com ADR-4 / Fase 1.

Opcional de produto (escolher uma e documentar na implementação):

- **A (recomendado):** só passar `admin_id` quando `USE_MESSAGE_OUTBOX` é verdadeiro **e** `is_super_admin()`, para não alterar `created_by_admin_id` em ambientes só legado.
- **B:** passar `admin_id=current_user.id` para todo superadmin sempre, preenchendo `created_by_admin_id` também com flag off (auditoria “criado pelo superadmin via UI normal”).

### Scope

**In scope:**

- Alteração mínima em **`app.py`** na função da rota **`POST /api/campaigns`**.
- Teste automatizado: superadmin + flag on + `POST /api/campaigns` → não chama `create_advanced_campaign` (mock) e/ou existem linhas `campaign_message_outbox` após criação (alinhado a testes existentes em `test_admin_campaign_crud.py`).

**Out of scope:**

- Alterar o gate Fase 1 (continua: **`USE_MESSAGE_OUTBOX`** + email em **`SUPER_ADMIN_EMAILS`**).
- Abrir outbox a utilizadores não superadmin.
- Mudar UI de `/campaigns/new` além do efeito colateral já suportado pelo backend (sem novo copy obrigatório).

---

## Confirmação: pausa / continuar na lista de campanhas e mecanismo outbox

### Comportamento observado

O botão da lista (**`templates/campaigns_list.html`**) chama **`POST /api/campaigns/<id>/toggle_pause`** (`toggle_campaign_pause` em **`app.py`**).

### Por que funciona também para envio individual (outbox)

O worker **`process_message_outbox_tick`** em **`worker_message_outbox.py`** só considera itens cuja campanha está em estado activo:

```text
AND c.status IN ('running', 'pending')
```

(ver query principal do tick — filtro por `campaign_status`.)

A rota **`toggle_pause`**:

1. **Se** `use_uazapi_sender` **e** `uazapi_folder_id`: chama **`_uazapi_control_campaign`**, que por sua vez faz **`edit_campaign`** na Uazapi **e** **`UPDATE campaigns SET status`** para `paused` / `running` (**linhas ~5568–5571** em `app.py`).
2. **Senão** (sem pasta principal — típico de campanha criada **só** por outbox, sem `create_advanced_campaign`): faz **`UPDATE campaigns SET status`** directamente (**~5793–5797**).

Em ambos os casos o **`status`** da campanha na BD passa a **`paused`** ou **`running`**. Enquanto **`paused`**, o worker outbox **não** selecciona mensagens dessa campanha — pelo mesmo critério `running`/`pending`.

**Conclusão:** Sim, **procede**: o mesmo botão de pausa/continuar que historicamente integrava **`advanced_campaign`** via pasta continua relevante onde há **`uazapi_folder_id`** (pausa remota + BD), e para campanhas **sem** pasta o fluxo reduz-se ao **SSOT `campaigns.status`**, que já é o que o worker outbox respeita. Não é um segundo botão específico do outbox (esse existe só em **`/api/admin/campaigns/.../outbox/pause`** para superadmin).

---

## Context for Development

### Codebase Patterns

- **`_create_campaign_core`**, ramo Uazapi (~6343–6487): `use_outbox_enqueue` e enqueue em `campaign_message_outbox` vs ramo `create_advanced_campaign`.
- **`_phase1_outbox_operator_is_superadmin(admin_id)`**: com pedido HTTP, exige alinhamento `current_user.id == admin_id` e `is_super_admin()`.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `app.py` | `create_campaign`, `_create_campaign_core`, `toggle_campaign_pause`, `_uazapi_control_campaign` |
| `worker_message_outbox.py` | Filtro `campaign_status` no tick |
| `templates/campaigns_list.html` | `fetch('/api/campaigns/${id}/toggle_pause')` |
| `tests/test_admin_campaign_crud.py` | Padrão `use_message_outbox_env`, mock `create_advanced_campaign` |

### Technical Decisions

1. **Sem novo endpoint:** apenas `admin_id` opcional no `POST /api/campaigns` conforme condições acima.
2. **Paridade com painel admin:** mesmo utilizador superadmin + mesma flag → mesmo resultado de fila (outbox vs legado).
3. **Auditoria:** se adoptada opção **A**, `created_by_admin_id` só preenchido quando o patch passa `admin_id` (flag on + superadmin); utilizador normal inalterado.

---

## Implementation Plan

### Tasks

- [x] **Task 1:** Em `app.py`, na função da rota `POST /api/campaigns`, calcular `admin_id` (por exemplo `current_user.id` se `USE_MESSAGE_OUTBOX` e `is_super_admin()`, senão `None`) e chamar `_create_campaign_core(current_user.id, request.json, admin_id=admin_id)`.
- [x] **Task 2:** Actualizar docstring curta da rota a referir paridade outbox com fluxo admin quando superadmin + flag.
- [x] **Task 3:** Teste pytest: cliente autenticado como superadmin, `USE_MESSAGE_OUTBOX=1`, `POST /api/campaigns` com payload mínimo válido Uazapi — assert mock `create_advanced_campaign` não chamado **ou** presença de linhas na outbox (reutilizar fixtures/helpers existentes).

### Acceptance Criteria

- [ ] **AC1:** Dado `USE_MESSAGE_OUTBOX` ligado e utilizador com email em `SUPER_ADMIN_EMAILS`, quando cria campanha Uazapi via **`POST /api/campaigns`** com o mesmo corpo que hoje o criador normal envia, então o fluxo segue o ramo **`use_outbox_enqueue`** (sem `create_advanced_campaign` para essa criação) tal como no **`POST /api/admin/campaigns`** para o próprio utilizador.
- [ ] **AC2:** Dado utilizador **não** superadmin, quando chama **`POST /api/campaigns`**, então comportamento permanece igual ao actual (sem `admin_id` para gate outbox).
- [ ] **AC3:** Dado `USE_MESSAGE_OUTBOX` desligado, quando superadmin chama **`POST /api/campaigns`**, então não há regressão: continua legado como hoje (sem depender de `admin_id` para outbox).
- [ ] **AC4 (manual):** Com campanha outbox activa, usar pausa/continuar na lista — worker deixa de enviar / volta a enviar conforme `campaigns.status` (confirmar logs ou estados `campaign_message_outbox`).

---

## Additional Context

### Dependencies

- Env: **`USE_MESSAGE_OUTBOX`**, **`SUPER_ADMIN_EMAILS`** incluindo operadores de teste.
- Postgres + worker `worker_cadence` para validação manual de envio.

### Testing Strategy

- Automatizado: novo caso ou extensão em `tests/test_admin_campaign_crud.py` (ou ficheiro dedicado) com login superadmin e flag.
- Manual: criar campanha em `/campaigns/new` como superadmin; verificar logs `[UAZAPI] Outbox enqueue` e ausência de `create_advanced_campaign OK` quando aplicável.

### Notes

- Relacionado: **`tech-spec-envio-individual-fila-intercalada-campanhas.md`** (Task 6 / ADR-4); este patch fecha lacuna de **entrada** (`/api/campaigns`) sem mudar política de fila.
- Se no futuro **admins não super** precisarem do mesmo comportamento, isso é decisão de produto separada (fora do gate actual).

---

## Validação rápida (checklist)

1. Superadmin + `USE_MESSAGE_OUTBOX=1`: criar em `/campaigns/new` → outbox enfileirada.
2. Utilizador normal: criar em `/campaigns/new` → comportamento legado inalterado (flag on ou off, conforme gate).
3. Pausa na lista → `campaigns.status = paused` → nenhum novo envio outbox até continuar.
