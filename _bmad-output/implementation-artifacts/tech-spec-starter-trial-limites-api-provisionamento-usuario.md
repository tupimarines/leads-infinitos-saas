---
title: 'Limites Starter Trial + API de provisionamento (usuário e plano)'
slug: starter-trial-limites-api-provisionamento-usuario
created: '2026-04-19T12:00:00Z'
status: implementation-complete
stepsCompleted: [1, 2, 3, 4, 5, 6, 7]
tech_stack:
  - Python 3.x
  - Flask ≥3
  - PostgreSQL (psycopg2)
  - Flask-Login
files_to_modify:
  - utils/limits.py
  - templates/admin/users.html
  - app.py
code_patterns:
  - PLAN_POLICY em utils/limits.py como fonte única de limites por plano
  - License.create + revoke anterior espelhando admin_create_license
  - Rotas /api/* com JSON; admin atual usa @login_required + @admin_required
  - Segredo por variável de ambiente (padrão CRON_SECRET em rotas internas)
test_patterns:
  - pytest em tests/; smoke manual para rotas JSON
---

# Tech-Spec: Limites Starter Trial + API de provisionamento (usuário e plano)

**Criado:** 2026-04-19

## Overview

### Problem Statement

1. O plano **Starter Trial** está definido com limites mais generosos do que o produto deseja oferecer agora: 15 envios/dia por instância, 2 instâncias e 210 extrações mensais. O alvo é **10 envios/dia**, **1 instância** e **100 extrações** no mesmo período de trial (7 dias inalterado, salvo decisão futura).
2. Integrações externas (ex.: automação, parceiros) precisam **criar usuário por e-mail** e **aplicar licença** (inicialmente Starter Trial) sem passar pelo fluxo web de registro/Hotmart, com extensão futura para outros `license_type`.

### Solution

1. Ajustar apenas a entrada `starter_trial` em `PLAN_POLICY` (`utils/limits.py`) e alinhar textos administrativos que descrevem o plano (`templates/admin/users.html`). Demais consumidores de `get_plan_policy` / `get_user_daily_limit` passam a refletir os novos valores automaticamente.
2. Implementar **uma ou duas rotas HTTP JSON** protegidas por **segredo de servidor** (variável de ambiente dedicada), que reutilizam `User.create`, validação de email duplicado e `License.create` com o mesmo padrão de revogação de licenças anteriores usado em `admin_create_license` (form POST admin).

### Scope

**In Scope:**

- Alteração numérica e de `instance_limit` do plano `starter_trial` na política central.
- Atualização da descrição do plano no select do admin.
- Endpoints JSON para provisionamento server-to-server: criação de usuário por email; aplicação de licença com **`license_type` opcional default `starter_trial`** e validação contra `ACTIVE_LICENSE_TYPES` / `resolve_license_type`.
- Documentar variável de ambiente do segredo e formato de request/response.

**Out of Scope:**

- Alterar duração do trial (`validity_days: 7`) ou regras de expiração em `utils/expire_starter_trial.py` (salvo menção explícita depois).
- UI pública de pricing ou landing.
- Envio obrigatório de e-mail com senha (pode ser follow-up; a spec permite retorno da senha gerada no JSON para integrações confidenciais).
- Refatorar `app.py` em módulos (manter padrão atual: novas rotas próximas às rotas admin/API existentes).

## Context for Development

### Codebase Patterns

- **Fonte única de limites:** `PLAN_POLICY` em `utils/limits.py` define `instance_limit`, `monthly_extraction_limit`, `daily_sends_per_instance_default` por tipo resolvido (`starter`, `starter_trial`, `pro`, `scale`, `infinite`).
- **Estado atual `starter_trial`:** `instance_limit: 2`, `monthly_extraction_limit: 210`, `daily_sends_per_instance_default: 15`, `validity_days: 7`.
- **Criação de usuário:** `User.create(email, password)` em `app.py`; checagem de duplicidade `User.get_by_email(email)`.
- **Criação de licença:** `License.create(user_id, hotmart_purchase_id, hotmart_product_id, license_type, purchase_date)`; tipos ativos em `ACTIVE_LICENSE_TYPES` derivados de `PLAN_POLICY.keys()`.
- **Grant manual admin:** `admin_create_license` em `app.py` revoga licenças anteriores (`UPDATE licenses SET status = 'cancelled'`), gera `purchase_id`/`product_id` fictícios e chama `License.create`.
- **API admin existente:** rotas `/api/admin/...` usam `@login_required` + `@admin_required` (sessão). Para integração **sem sessão**, o padrão do projeto para rotas acionadas por infra é token em query/header (ex.: `CRON_SECRET` em `/cron/expire-starter-trial`).

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `utils/limits.py` | `PLAN_POLICY`, `get_plan_policy`, `get_user_daily_limit`, `resolve_license_type` |
| `app.py` | `User`, `License`, `ACTIVE_LICENSE_TYPES`, `admin_create_license`, `admin_create_user`, `/api/login` |
| `templates/admin/users.html` | Texto do `<option>` Starter Trial no formulário de plano |
| `_bmad-output/project-context.md` | Stack Flask/Postgres, placeholders `%s`, evitar refator grande |

### Technical Decisions

| Decisão | Escolha | Motivo |
| -------- | -------- | ------ |
| Autenticação dos novos endpoints | Header `Authorization: Bearer <PROVISION_API_SECRET>` **ou** `X-Provision-Token` igual ao valor de env `PROVISION_API_SECRET` (documentar um formato único no código) | Separação de `CRON_SECRET`; escopo só de provisionamento; rotação independente. |
| Um vs dois endpoints | **Dois:** `POST /api/provision/user` e `POST /api/provision/license` | Permite criar usuário sem licença e aplicar licença depois; também aplicar licença a usuário existente por email. |
| Default de plano | Body `license_type` opcional; se ausente, usar `starter_trial` | Atende “inicialmente Starter Trial” com extensibilidade. |
| Senha na criação de usuário | Se `password` omitido ou vazio, gerar `secrets.token_urlsafe(12)` e devolver **uma vez** no JSON | Paridade com necessidade de login; integrador armazena com segurança. |
| Conflito de email | HTTP **409** com JSON `{"error": "email_already_registered"}` | Distinto de 400 genérico. |

## Implementation Plan

### Tasks

- [x] **Task 1:** Ajustar limites do plano `starter_trial` em `PLAN_POLICY`
  - File: `utils/limits.py`
  - Action: Em `starter_trial`, definir `instance_limit: 1`, `monthly_extraction_limit: 100`, `daily_sends_per_instance_default: 10`. Atualizar comentário na linha do bloco se citar números antigos.
  - Notes: Não alterar chaves do dict nem `PLAN_PRIORITY` salvo necessidade.

- [x] **Task 2:** Atualizar copy do admin alinhada aos novos números
  - File: `templates/admin/users.html`
  - Action: Texto do `<option value="starter_trial">` para refletir **1 instância**, **100 extrações**, **10 disparos/dia/instância**, **7 dias**.

- [x] **Task 3:** Adicionar variável de ambiente e leitura no bootstrap da app
  - File: `app.py` (topo, junto de outros `os.environ.get`)
  - Action: Documentar `PROVISION_API_SECRET` (obrigatório em produção para usar as rotas; em dev pode ficar vazio e rotas retornam 503 ou 401 — **escolher um comportamento e documentar**; recomendação: **401 se secret não configurado** para não expor acidentalmente).

- [x] **Task 4:** Implementar helper `_require_provision_secret()` 
  - File: `app.py`
  - Action: Comparar header Bearer (ou o header único escolhido na TD) com `os.environ.get("PROVISION_API_SECRET","")`. Constant-time compare (`secrets.compare_digest`) após normalizar. Retornar `jsonify` 401 se inválido.

- [x] **Task 5:** `POST /api/provision/user`
  - File: `app.py`
  - Action: Decorar lógica com verificação do secret (antes de qualquer side effect). Body JSON: `email` (obrigatório, strip+lower), `password` (opcional). Se email já existe → 409. Caso contrário `User.create`, retornar `201` com `{ "user_id", "email", "password": "<only-if-generated>" }` — se o client enviou password, omitir campo `password` na resposta ou retornar `"password_set": true` sem ecoar a senha.
  - Notes: Não fazer login de sessão nesta rota.

- [x] **Task 6:** `POST /api/provision/license`
  - File: `app.py`
  - Action: Mesmo secret. Body: `email` (obrigatório) e `license_type` (opcional; default `starter_trial`). Resolver com `resolve_license_type(..., allow_legacy_fallback=False)`; se inválido → 400 com lista de tipos permitidos (reutilizar mensagem similar a `admin_create_license`). Buscar usuário por email; se não existir → 404. Revogar licenças ativas/pendentes como em `admin_create_license`, depois `License.create` com IDs fictícios `MANUAL-` / `MANUAL-GRANT` e `purchase_date` UTC ISO.
  - Notes: Garantir transação: revoke + insert na mesma conexão com commit único se possível (hoje admin usa duas conexões; melhorar localmente nesta rota com um único `get_db_connection`).

- [x] **Task 7:** (Opcional recomendado) Teste unitário mínimo do policy
  - File: `tests/test_limits_policy.py` (criar se não existir)
  - Action: Assert `get_plan_policy("starter_trial")` retorna `instance_limit==1`, `monthly_extraction_limit==100`, `daily_sends_per_instance_default==10`.

### Acceptance Criteria

- [x] **AC1:** Dado `PLAN_POLICY["starter_trial"]` após deploy, quando `get_plan_policy("starter_trial")` é chamado, então `instance_limit` é 1, `monthly_extraction_limit` é 100 e `daily_sends_per_instance_default` é 10.

- [x] **AC2:** Dado um usuário com plano ativo que permitia 2 instâncias no passado, quando a política nova está ativa e o usuário tenta criar/vincular recursos que respeitam `instance_limit`, então o limite efetivo para **novas** checagens é 1 (comportamento esperado do código que usa `get_plan_policy`; não exige migração de instâncias legadas — documentar que usuários com 2 instâncias pré-existentes podem precisar de saneamento manual ou script separado **fora deste escopo**).

- [x] **AC3:** Dado o painel admin em `/admin/users`, quando o superadmin abre o select “Alterar Plano”, então o texto do Starter Trial descreve 1 instância, 100 extrações e 10 disparos/dia.

- [x] **AC4:** Dado `PROVISION_API_SECRET` configurado, quando `POST /api/provision/user` com JSON válido e header correto, então resposta é `201` e o usuário aparece no banco com email normalizado.

- [x] **AC5:** Dado o mesmo secret, quando `POST /api/provision/user` com email já existente, então resposta é `409` e nenhum novo registro de usuário é criado.

- [x] **AC6:** Dado usuário existente sem `license_type` no body, quando `POST /api/provision/license` com email válido, então é criada licença `starter_trial` ativa e licenças anteriores do usuário ficam `cancelled`.

- [x] **AC7:** Dado body com `"license_type": "pro"` (ou outro valor em `ACTIVE_LICENSE_TYPES`), quando `POST /api/provision/license`, então a licença criada é do tipo solicitado.

- [x] **AC8:** Dado body com `license_type` inválido, quando `POST /api/provision/license`, então resposta é `400` e nenhuma alteração parcial inconsistente permanece (transação).

- [x] **AC9:** Dado requisição sem secret ou secret incorreto, quando qualquer rota `/api/provision/*`, então `401` e corpo JSON de erro.

## Additional Context

### Dependencies

- Nenhuma biblioteca nova obrigatória.
- DevOps: definir `PROVISION_API_SECRET` no ambiente (Dokploy/.env) antes de habilitar integrações.

### Testing Strategy

- **Unit:** asserts em `PLAN_POLICY` / `get_plan_policy` para `starter_trial` (Task 7).
- **Manual / curl:** criar usuário e aplicar licença com header Bearer; repetir para 409 e 401.
- **Regressão:** smoke em `get_user_daily_limit` para usuário com licença `starter_trial` (campanha nova não deve permitir daily_limit acima de 10 no clamp existente em `_create_campaign_core`).

### Notes

- **Usuários trial atuais** com 210 extrações “consumidas” parcialmente no mês: a redução do teto afeta o **limite restante** conforme a lógica mensal em `ScrapingJob` / contadores — validar se o código usa o limite do mês corrente do policy snapshot; se sim, o novo teto 100 aplica-se ao mês vigente. Mencionar ao QA para validar um usuário real de staging.
- **Duas instâncias Uazapi** já criadas para um trial antigo: com `instance_limit: 1`, novas criações podem falhar; não é objetivo desta spec remover instâncias automaticamente.
- Fluxo BMAD completo prevê checkpoints interativos; esta spec foi consolidada em arquivo único **ready-for-dev** para handoff a implementação imediata.
