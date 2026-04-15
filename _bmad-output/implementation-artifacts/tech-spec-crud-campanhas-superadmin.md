---
title: 'CRUD de Campanhas para Superadmin'
slug: 'crud-campanhas-superadmin'
created: '2026-04-01'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask ≥3.0', 'PostgreSQL 15', 'psycopg2-binary', 'Jinja2', 'Tailwind CSS (CDN)', 'Redis + RQ', 'UazapiService', 'pandas']
files_to_modify: ['app.py', 'templates/admin/campaigns.html', 'templates/admin/campaigns_new.html (NOVO)', 'templates/admin/campaigns_edit.html (NOVO)']
code_patterns: ['@admin_required decorator', 'get_db_connection() + RealDictCursor', 'Campaign/CampaignLead classes', '_get_uazapi_instances_for_campaign()', '_create_campaign_core() (a extrair)', 'UazapiService.create_advanced_campaign()', 'validate_job_csv pattern (batch 5, check_phone)']
test_patterns: ['tests/test_campaign_creation.py (integration test manual)', 'tests/test_validate_job_csv.py (pytest + mock)']
---

# Tech-Spec: CRUD de Campanhas para Superadmin

**Created:** 2026-04-01

## Overview

### Problem Statement

O superadmin não possui ferramentas para criar, editar ou gerenciar campanhas em nome dos usuários. A tela `/admin/campaigns` é somente leitura (listagem + modal de detalhes + exclusão). A equipe de suporte do Leads Infinitos não consegue apoiar os clientes diretamente — precisa pedir ao usuário que crie/edite a campanha por conta própria. Além disso, os contadores de envio nos cards (Enviados/Pendentes) não refletem a realidade da API Uazapi, pois dependem apenas do status local em `campaign_leads`.

### Solution

Implementar CRUD completo de campanhas no painel superadmin (`/admin/campaigns`) com:
1. **Criar** — formulário com seleção de usuário, instância do usuário, scraping job OU upload de CSV (com validate number), mensagens spintax, follow-ups, horários de envio, delay, toggles de fim de semana. Criação real via `create_advanced_campaign` da Uazapi.
2. **Ler** — manter listagem com cards, modal de detalhes existente, e adicionar sync automático com Uazapi para contadores reais.
3. **Editar** — mesmos campos da criação (nome, mensagens, instâncias, horários, cadência, follow-ups).
4. **Excluir** — já existe, manter funcionalidade atual.
5. **Leads na edição** — listar leads com status `sent` atualizado via sync automático.
6. A campanha criada pelo superadmin aparece normalmente na tela do usuário final.

### Scope

**In Scope:**
- Botão "Nova Campanha" na tela `/admin/campaigns`
- Página/modal de criação com: seleção de usuário, instância(s) do usuário selecionado, scraping job do usuário OU upload de CSV com validação de número
- Campos: nome, mensagens (spintax/rotação múltipla), instâncias, horários de envio (start/end), toggles sábado/domingo, delay min/max, cadência e follow-ups (etapas com delay_days e mensagem)
- Criação real — dispara via `create_advanced_campaign` Uazapi; campanha aparece na tela do usuário
- Página de edição de campanha pelo superadmin (mesmos campos)
- Seção de leads na edição com status atualizado pelo sync
- Sync automático com Uazapi (`list_folders`) ao carregar `/admin/campaigns` para atualizar contadores nos cards
- Manter botão de Detalhes (modal existente) e botão de Excluir
- Botão de Editar nos cards (ao lado de Detalhes e Excluir — redimensionar para caber 3 botões)
- Coluna `created_by_admin_id` (nullable) para auditoria: saber se campanha foi criada pelo suporte

**Out of Scope:**
- Kanban de leads
- Agente de IA
- Alteração de lógica interna do `worker_sender.py` / `worker_cadence.py`
- Migração de frontend (Jinja2 → Next.js)
- Alteração de planos/licenças

## Context for Development

### Codebase Patterns

- **Monolito Flask** em `app.py` (~7100+ linhas). Rotas admin usam `@admin_required` decorator.
- `is_super_admin(user)` verifica email em `SUPER_ADMIN_EMAILS`.
- `Campaign.create()` é método estático simples (L930-945); a criação real acontece na rota `POST /api/campaigns` (L4062-4593) com lógica extensa (~530 linhas): parse CSV/XLSX, validação instâncias Uazapi, INSERT campaigns + campaign_instances + campaign_steps, atribuição send_batch, chunking 30 leads/instância, chamada `create_advanced_campaign()`, INSERT campaign_stage_sends.
- Templates Jinja2 em `templates/admin/`. Tema escuro com Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`).
- `UazapiService` em `services/uazapi.py` — métodos relevantes: `create_advanced_campaign()` (L264), `edit_campaign()` (L317), `list_folders()` (L352), `list_messages()` (L416), `check_phone()` (L229).
- `utils/sync_uazapi.py` — sincroniza `campaign_leads` com API Uazapi usando `list_folders` (campo `log_sucess`/`log_success`) como fonte de verdade. Função principal: `sync_campaign_leads_from_uazapi()`.
- `utils/validate_job_csv.py` — validação de CSV em lote (batch 5, `_check_phone_with_retry`, retry 2x, fallback instância conectada via `_get_connected_uazapi_token_for_user`).
- `utils/limits.py` — `PLAN_POLICY` define limites por plano (starter: 1 inst, pro: 2, scale: 4, infinite: 20). `daily_sends_per_instance_default` = 30 para todos. `get_user_daily_limit()` resolve plano ativo do usuário.
- `utils/uazapi_pacing.py` — `default_inter_message_delay_range_minutes()` retorna faixa ponderada; `build_pacing_segments_for_leads()` particiona leads em segmentos.
- Contadores atuais nos cards admin: `SELECT COUNT(*) FROM campaign_leads WHERE status = 'sent'` — não reflete realidade Uazapi.
- **Helpers existentes** a reutilizar: `_get_uazapi_instances_for_campaign(campaign_id, user_id)` (L5489), `_resolve_uazapi_remote_jid(uazapi, token)` (L5518), `_uazapi_control_campaign(campaign_id, user_id, action, admin_mode)` (L3629).
- **Padrão de criação de steps** (L4337-4382): loop sobre `data['steps']`, INSERT em `campaign_steps` com `ON CONFLICT DO UPDATE`. Cada step tem: `step_number`, `step_label`, `message_template` (JSON), `media_path`, `media_type`, `delay_days`.
- **admin_users()** (L3108-3135): existe como página HTML, **não** como API JSON. Faz JOIN users + licenses + instances.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `app.py` L2900-2961 | Rota `admin_campaigns()` — listagem atual |
| `app.py` L2964-3075 | Rota `admin_campaign_detail_api()` — modal detalhes |
| `app.py` L3078-3155 | Rota `admin_delete_campaign()` — exclusão |
| `app.py` L3108-3160 | Rota `admin_users()` — listagem de usuários (existe, mas sem API JSON) |
| `app.py` L3562-3574 | Rota `new_campaign()` — form criação do usuário |
| `app.py` L4062-4350+ | Rota `create_campaign()` — lógica completa de criação |
| `app.py` L6492-6499 | Rota `edit_campaign()` — edição do usuário |
| `app.py` L6608-6700+ | Rota `update_campaign()` — API de atualização |
| `app.py` L910-984 | Classe `Campaign` — model |
| `app.py` L987-1050 | Classe `CampaignLead` — leads da campanha |
| `templates/admin/campaigns.html` | Template atual da listagem admin |
| `templates/campaigns_new.html` | Template de criação do usuário (referência para UI) |
| `services/uazapi.py` L229-260 | `check_phone()` — verificação de números |
| `services/uazapi.py` L264+ | `create_advanced_campaign()` |
| `services/uazapi.py` L352+ | `list_folders()` |
| `services/uazapi.py` L416+ | `list_messages()` |
| `utils/sync_uazapi.py` | Lógica de sync leads ↔ Uazapi |
| `utils/validate_job_csv.py` | Validação de CSV via `check_phone` (batch 5, retry, fallback instância) |
| `utils/limits.py` | `PLAN_POLICY`, `get_user_daily_limit()`, `get_instance_daily_limit()` |
| `utils/uazapi_pacing.py` | Delay ponderado entre mensagens |

### API Endpoints Necessários (Novos)

Endpoints de cascata para o formulário de criação admin. **Nenhum desses existe atualmente** — precisam ser criados.

| Método | Endpoint | Descrição | Resposta |
|--------|----------|-----------|----------|
| `GET` | `/api/admin/users/list` | Lista usuários ativos (id, email, name) para select | `[{id, email, name}]` |
| `GET` | `/api/admin/users/<id>/instances` | Instâncias Uazapi do usuário selecionado | `[{id, name, status, api_provider, apikey}]` |
| `GET` | `/api/admin/users/<id>/scraping-jobs` | Jobs completados do usuário (para select de leads) | `[{id, keyword, locations, lead_count, created_at}]` |
| `POST` | `/api/admin/campaigns` | Criar campanha para o usuário (proxy) | `{campaign_id, status, ...}` |
| `POST` | `/api/admin/campaigns/<id>/update` | Editar campanha existente (admin) | `{success, ...}` |
| `GET` | `/api/admin/campaigns/sync` | Sync Uazapi para campanhas running → contadores | `{campaigns: [{id, sent_count, pending_count}]}` |
| `POST` | `/api/admin/campaigns/validate-csv` | Upload CSV + validate number (opcional) | `{valid, invalid, file_token}` |

### Campos do Formulário Admin → Colunas DB

| Campo UI | Coluna DB (`campaigns`) | Tipo | Notas |
|----------|------------------------|------|-------|
| Usuário | `user_id` | `INTEGER` | **Obrigatório.** Select de usuários. Não do admin. |
| Nome | `name` | `TEXT` | Obrigatório |
| Mensagens (spintax) | `message_template` | `TEXT` (JSON array) | Lista de variações. Armazenado como JSON string. |
| Instâncias | — (tabela `campaign_instances`) | `INTEGER[]` | Validar que pertencem ao `user_id` selecionado |
| Modo rotação | `rotation_mode` | `TEXT` | `'single'` se 1 instância; `'round_robin'` se múltiplas |
| Horário início envio | `send_hour_start` | `INTEGER` | Default 8. Range 0-23. |
| Horário fim envio | `send_hour_end` | `INTEGER` | Default 20. Range 0-23. |
| Enviar sábado | `send_saturday` | `BOOLEAN` | Toggle. Default false. |
| Enviar domingo | `send_sunday` | `BOOLEAN` | Toggle. Default false. |
| Delay mín (min) | `delay_min_minutes` | `INTEGER` | Nullable. Se NULL, usa pacing ponderado de `uazapi_pacing.py`. |
| Delay máx (min) | `delay_max_minutes` | `INTEGER` | Nullable. `delay_max >= delay_min`. Range 1-60. |
| Agendar início | `scheduled_start` | `TIMESTAMP` | Opcional. Se vazio = disparo imediato. Se preenchido → `status='pending'`. |
| Limite diário | `daily_limit` | `INTEGER` | **Inferido do plano do usuário** via `get_user_daily_limit(user_id)`. Não editável manualmente — respeitar plano. Default 30/instância para todos os planos. |
| Cadência | `enable_cadence` | `BOOLEAN` | Toggle |
| Config cadência | `cadence_config` | `JSONB` | JSON com `cadence_setup_mode` |
| Follow-ups | — (tabela `campaign_steps`) | — | Step number, label, delay_days, mensagem por etapa |
| Uazapi sender | `use_uazapi_sender` | `BOOLEAN` | Sempre `TRUE` (hardcoded) |
| Criado por admin | `created_by_admin_id` | `INTEGER` (nullable, **NOVO**) | Se NULL = criada pelo próprio usuário. Se preenchido = ID do admin que criou. |

### Technical Decisions

- **Superadmin age como proxy:** cria campanhas com `user_id` do usuário selecionado, não do admin. A campanha pertence ao usuário.
- **Reutilizar lógica existente:** A criação deve reaproveitar ao máximo a lógica de `create_campaign()` existente, parametrizando `user_id`.
- **Sync automático:** Ao carregar `/admin/campaigns`, executar sync via `list_folders` para cada campanha Uazapi ativa e atualizar `campaign_leads` + contadores. Usar cache de curta duração para não sobrecarregar a API (ex.: sync apenas se último sync > 5 min).
- **Upload CSV:** Validação de número (formato brasileiro, remover duplicados) no backend. Reusar padrões de `create_campaign()`.
- **Follow-ups:** Reutilizar a mesma estrutura de `campaign_steps` e `cadence_config` da criação normal — garantir inserção em `campaign_steps` (não apenas `cadence_config`).

### ADRs (Architecture Decision Records)

**ADR-1: Estratégia de criação — Extrair helper `_create_campaign_core()`**
- Extrair a lógica core de `create_campaign()` para um helper `_create_campaign_core(user_id, data)`.
- A rota do usuário passa `current_user.id`; a rota admin passa o `user_id` selecionado.
- Sem duplicação de lógica. Ambas as rotas usam o mesmo core.
- **Rationale:** A rota `create_campaign()` tem ~300 linhas de lógica (parse CSV, validação instâncias, criação de steps, chunking, chamada Uazapi). Duplicar = manutenção dupla inevitável.

**ADR-2: Sync de contadores — Assíncrono via AJAX**
- Página carrega rápido com dados do DB (`campaign_leads.status`).
- Endpoint `GET /api/admin/campaigns/sync` dispara sync apenas para campanhas `running`, retorna contadores atualizados.
- Frontend atualiza cards via AJAX ao receber resposta.
- Cache: ignorar campanhas sincadas há < 5 min (campo `last_sync_at` em `campaign_stage_sends`).
- Fallback: se API falhar, mostrar dados DB com indicador visual "(desatualizado)".

**ADR-3: UI — Páginas dedicadas (não modal)**
- Formulário complexo demais para modal (seleção de usuário, instâncias dinâmicas, N mensagens spintax, N follow-ups, horários, toggles).
- Criar páginas: `/admin/campaigns/new` e `/admin/campaigns/<id>/edit`.
- Reusar estrutura visual de `campaigns_new.html` com campos extras de admin (select de usuário no topo).
- Botão "← Voltar" proeminente + flash message após criar/editar.

**ADR-4: Upload CSV — Validate number via popup de confirmação**
- Validação de formato (regex brasileiro) obrigatória em todos os casos (parse + `_normalize_phone_for_api()`).
- Ao fazer upload de CSV, **popup pergunta:** "Gostaria de validar se todos os números da lista são WhatsApp válido?"
  - **Sim:** executa validate number síncrono usando a mesma lógica de `utils/validate_job_csv.py` (batch 5, `_check_phone_with_retry`, retry 2x, backoff, timeout 30s por batch, pausa 2s entre batches). UI exibe progress bar: "Validando números... Isso pode levar alguns minutos (~X min para Y números)." Após validação, preview: "500 números → 312 válidos. Criar com 312?"
  - **Não:** prossegue com validação de formato apenas (regex), sem `check_phone`.
- Token para `check_phone` obtido via `_get_connected_uazapi_token_for_user()` da instância do usuário selecionado.
- Se nenhuma instância Uazapi do usuário estiver conectada: desabilitar opção de validar e informar "Nenhuma instância conectada para validar números".

**ADR-5: Campos editáveis por status da campanha**

| Status | Campos editáveis | Campos bloqueados |
|--------|-------------------|-------------------|
| `pending` | Todos | Nenhum |
| `paused` | Nome, horários, delay, toggles fim de semana | Mensagens, instâncias, leads |
| `running` | Nome apenas | Tudo (exigir pausar primeiro) |
| `completed` | Nenhum (read-only) | Todos |

- O campo `delay_min_minutes` / `delay_max_minutes` segue a lógica existente de pacing em `utils/uazapi_pacing.py`:
  - Se o superadmin **não definir** delay customizado: usa `default_inter_message_delay_range_minutes()` (bucket ponderado: 22% → 4-8 min, 33% → 8-12 min, 44% → 10-15 min).
  - Se o superadmin **definir** delay: grava em `campaigns.delay_min_minutes` / `delay_max_minutes`, que é respeitado tanto na criação inicial (`create_campaign`) quanto nos chunks subsequentes (`_continue_initial_chunk_core`) e follow-ups.
  - Pausa longa entre sub-campanhas: 10% de chance de gap 25-45 min (gerenciado por `_LONG_GAP_*` em `uazapi_pacing.py`).
  - Conversão: valores salvos em minutos no DB; convertidos para segundos (`delay_min_sec = delay_min * 60`) ao chamar `create_advanced_campaign()`.
  - Validação: `delay_max >= delay_min`; range aceito: 1-60 min por mensagem.
- **Rationale:** Editar mensagem/instâncias em campanha ativa causa inconsistência no worker. Delay é seguro de editar quando `paused` pois o worker lê do DB antes de cada envio.

### Riscos Identificados (Pre-mortem)

| # | Risco | Mitigação |
|---|-------|-----------|
| 1 | **User/instance mismatch** — superadmin seleciona instância de outro usuário | Validar no backend que `instance_ids` pertencem ao `user_id` selecionado. Limpar campos dependentes no frontend ao trocar usuário. Tela de confirmação antes de criar. |
| 2 | **Sync automático lento** — 79 campanhas × chamada API = timeout | Sync apenas para campanhas `running`. Cache TTL 5 min. Sync assíncrono via AJAX (carregar página com dados DB, atualizar cards quando sync retornar). Fallback: dados DB com indicador "(desatualizado)". |
| 3 | **Números inválidos no CSV** — queima limite diário com envios falhos | Popup de confirmação ao upload: "Validar números no WhatsApp?" Se sim, validate number síncrono (batch 5, `check_phone`). Se não, validação regex apenas. Preview com contagem antes de criar. |
| 4 | **Edição de campanha ativa** — mensagens inconsistentes entre leads | Bloquear edição de `message_template` e `instâncias` quando status = `running`. Exigir pausar primeiro. Campos sensíveis desabilitados na UI com indicador "Pause para editar". |
| 5 | **Follow-ups não criados** — `campaign_steps` vazio, `worker_cadence` ignora | Reutilizar o mesmo helper de criação de steps da rota do usuário. Não duplicar lógica. AC explícito: follow-ups criados pelo superadmin devem ser processados pelo `worker_cadence`. |
| 6 | **Folder órfã após delete** — campanha deletada mas folder Uazapi continua ativa | Confirmar uso de `_uazapi_control_campaign(admin_mode=True)` que deleta na Uazapi antes de remover do DB. Warning visual se campanha está `running` ao tentar deletar. |

## Implementation Plan

### Tasks

#### Fase 1: Migração DB + Endpoints de Suporte

- [ ] **Task 1: Migração DB — coluna `created_by_admin_id`**
  - File: `app.py` (função `init_db()`)
  - Action: Adicionar `ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER REFERENCES users(id) DEFAULT NULL;`
  - Notes: Coluna nullable. Se NULL = criada pelo usuário. Se preenchido = ID do admin.

- [ ] **Task 2: Endpoint `GET /api/admin/users/list`**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Query: `SELECT id, email FROM users ORDER BY email ASC`. Retornar JSON array `[{id, email}]`.
  - Notes: Não expor campos sensíveis (senha, apikey). Filtrar apenas usuários com pelo menos 1 licença ativa ou instância (para evitar listar usuários inativos).

- [ ] **Task 3: Endpoint `GET /api/admin/users/<id>/instances`**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Query: `SELECT id, name, status, COALESCE(api_provider, 'megaapi') as api_provider FROM instances WHERE user_id = %s AND COALESCE(api_provider, 'megaapi') = 'uazapi' ORDER BY id ASC`. Retornar JSON array.
  - Notes: Filtrar apenas instâncias Uazapi (padrão do sistema). Não expor `apikey` no response.

- [ ] **Task 4: Endpoint `GET /api/admin/users/<id>/scraping-jobs`**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Query: `SELECT id, keyword, locations, lead_count, created_at FROM scraping_jobs WHERE user_id = %s AND status = 'completed' ORDER BY created_at DESC`. Retornar JSON array com `created_at` formatado.
  - Notes: Mesmo padrão de `api_scraping_jobs()` (L3576-3596), mas parametrizado por `user_id` do path.

#### Fase 2: Refatoração — Extrair `_create_campaign_core()`

- [ ] **Task 5: Extrair lógica core de `create_campaign()` para `_create_campaign_core(user_id, data, admin_id=None)`**
  - File: `app.py`
  - Action: Mover a lógica de L4079-4593 (validação de dados, parse CSV, validação instâncias, INSERT campaigns/campaign_instances/campaign_steps, send_batch, chunking Uazapi, INSERT campaign_stage_sends) para um helper `_create_campaign_core(user_id, data, admin_id=None)`.
  - Notes:
    - O helper recebe `user_id` explícito (não usa `current_user.id`).
    - Se `admin_id` fornecido, grava em `created_by_admin_id`.
    - A rota `create_campaign()` existente passa a chamar: `return _create_campaign_core(current_user.id, request.json)`.
    - A rota admin chama: `return _create_campaign_core(target_user_id, data, admin_id=current_user.id)`.
    - Validação de `instance_ids` usa `user_id` recebido (não `current_user.id`).
    - `daily_limit` inferido via `get_user_daily_limit(user_id)` (importar de `utils/limits.py`).
    - `use_uazapi_sender` = `True` sempre (hardcoded).
    - `rotation_mode` = `'round_robin'` se `len(instance_ids) > 1`, senão `'single'`.
    - Storage path para media: `storage/{user_id}/campaign_media/` (usar `user_id`, não `current_user.id`).

- [ ] **Task 6: Endpoint `POST /api/admin/campaigns` — Criação pelo admin**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Aceita JSON com campos da tabela "Campos do Formulário Admin → Colunas DB". Valida que `user_id` existe. Chama `_create_campaign_core(user_id, data, admin_id=current_user.id)`.
  - Notes: Validação extra no backend: `instance_ids` pertencem ao `user_id` selecionado (não ao admin). Retorna `{campaign_id, status, leads_count}`.

- [ ] **Task 7: Endpoint `POST /api/admin/campaigns/validate-csv` — Upload + validate number**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Aceita multipart form com: `file` (CSV), `user_id`, `validate_whatsapp` (boolean).
  - Notes:
    - Parse CSV com pandas (reusar padrão de `create_campaign()` L4164-4246).
    - Extrair telefones com `_normalize_phone_for_api()`.
    - Se `validate_whatsapp=true`: obter token via `_get_connected_uazapi_token_for_user(conn, user_id)`. Executar `_check_phone_with_retry()` em batch 5, pausa 2s entre batches (mesma lógica de `validate_job_csv.py` L259-298).
    - Salvar CSV validado em `storage/{user_id}/uploads/admin_upload_{timestamp}.csv`.
    - Criar scraping_job fictício para o usuário (status='completed', keyword='Upload Admin') para que o fluxo `create_campaign_core` funcione via `job_id`.
    - Retornar `{valid, invalid, job_id}`.

#### Fase 3: Sync de Contadores

- [ ] **Task 8: Endpoint `GET /api/admin/campaigns/sync` — Sync assíncrono**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Para cada campanha com `status='running'` e `use_uazapi_sender=TRUE`:
    1. Verificar `last_sync_at` de `campaign_stage_sends`: se < 5 min, skip.
    2. Obter token da instância via `campaign_instances` JOIN `instances`.
    3. Chamar `list_folders(token)` para obter `log_sucess`/`log_success`.
    4. Atualizar `campaign_stage_sends.success_count` e `campaign_stage_sends.last_sync_at`.
    5. Recontar leads: `SELECT COUNT(*) FILTER (WHERE status='sent') FROM campaign_leads WHERE campaign_id = %s`.
  - Notes: Retornar `{campaigns: [{id, sent_count, pending_count, total_leads, last_sync}]}`. Usar try/except por campanha para não travar o endpoint se uma instância falhar.

- [ ] **Task 9: Atualizar listagem `admin_campaigns()` — carregar contadores do DB**
  - File: `app.py` (rota `admin_campaigns()`, L2900-2961)
  - Action: Manter query existente (contadores de `campaign_leads`). Esses serão os valores iniciais. O AJAX do frontend atualizará após sync.
  - Notes: Nenhuma alteração na query SQL. A mudança é no frontend (Task 12).

#### Fase 4: Edição pelo Admin

- [ ] **Task 10: Endpoint `POST /api/admin/campaigns/<id>/update` — Edição admin**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Aceita JSON com campos editáveis. Lógica:
    1. Carregar campanha: `SELECT * FROM campaigns WHERE id = %s` (sem filtro `user_id` — admin pode editar qualquer uma).
    2. Aplicar regras de ADR-5 (campos editáveis por status).
    3. Atualizar campos permitidos em `campaigns`.
    4. Se cadência/follow-ups mudaram: UPSERT em `campaign_steps` (padrão `ON CONFLICT DO UPDATE`, L4370-4382).
    5. Se horários de envio mudaram: atualizar `send_hour_start`, `send_hour_end`, `send_saturday`, `send_sunday`.
  - Notes: Retornar `{success: true}` ou `{error: "Não é possível editar [campo] quando status = running. Pause a campanha primeiro."}`.

- [ ] **Task 11: Endpoint `GET /api/admin/campaigns/<id>/leads` — Leads da campanha**
  - File: `app.py`
  - Action: Nova rota `@admin_required`. Mesmo padrão de `get_campaign_leads()` (L6502-6556) mas sem filtro `user_id`. Paginação, filtros por name/phone/status.
  - Notes: Retornar JSON com `{leads, total, page, pages}`. O status dos leads já é atualizado pelo sync (Task 8).

#### Fase 5: Templates Frontend

- [ ] **Task 12: Atualizar `templates/admin/campaigns.html` — Botões + Sync AJAX**
  - File: `templates/admin/campaigns.html`
  - Action:
    1. Adicionar botão "Nova Campanha" no header (ao lado de "← Voltar"), link para `/admin/campaigns/new`.
    2. Nos cards: redimensionar layout de botões para 3 colunas. Adicionar botão "Editar" (azul, link para `/admin/campaigns/{id}/edit`) entre Detalhes e Excluir.
    3. Adicionar script AJAX: ao carregar página, `fetch('/api/admin/campaigns/sync')`, ao receber resposta atualizar `sent_count` e `pending_count` nos cards. Mostrar spinner "Sincronizando..." enquanto aguarda.
    4. Se sync falhar: manter valores do DB e indicar "(DB)" no tooltip.
  - Notes: Os 3 botões devem ter tamanho reduzido (`text-xs` ou `px-2 py-1`) para caber no card. Layout: `grid grid-cols-3 gap-1`.

- [ ] **Task 13: Criar `templates/admin/campaigns_new.html` — Formulário de criação**
  - File: `templates/admin/campaigns_new.html` (NOVO)
  - Action: Criar template baseado em `campaigns_new.html` (copiar estrutura visual: glass-panel, neon-input, tema escuro). Diferenças:
    1. **Select de Usuário** no topo: dropdown que ao mudar, faz AJAX para carregar instâncias e jobs daquele usuário.
    2. **Select de Instâncias**: carregado via `GET /api/admin/users/<id>/instances`. Multi-select se múltiplas instâncias.
    3. **Select de Job OU Upload CSV**: dropdown carregado via `GET /api/admin/users/<id>/scraping-jobs` + botão upload CSV. Ao upload, popup pergunta "Validar números no WhatsApp?" → Se sim, chama `POST /api/admin/campaigns/validate-csv` com progress bar.
    4. **Mensagens spintax**: reusar padrão de `campaigns_new.html` (textareas dinâmicas, botão "Adicionar variação").
    5. **Horários de envio**: inputs numéricos para `send_hour_start` (default 8), `send_hour_end` (default 20). Toggles para sábado e domingo.
    6. **Delay**: inputs opcionais `delay_min_minutes`, `delay_max_minutes` com placeholder "Automático (4-15 min)".
    7. **Scheduled start**: input datetime-local (opcional).
    8. **Cadência/Follow-ups**: reusar estrutura accordion de `campaigns_new.html` (cadence-step class, toggle on/off). Cada step: mensagem (textarea), delay_days (input), label.
    9. **Confirmação**: antes de submeter, exibir resumo: "Criar campanha **{nome}** para **{email_usuario}** com {N} leads em {M} instância(s)?"
    10. Submit via `fetch('POST /api/admin/campaigns', {body: JSON})`.
    11. Botão "← Voltar para Campanhas" proeminente.
  - Notes: JavaScript para cascata de dropdowns: ao trocar usuário, limpar instâncias e jobs, recarregar via AJAX.

- [ ] **Task 14: Criar `templates/admin/campaigns_edit.html` — Formulário de edição**
  - File: `templates/admin/campaigns_edit.html` (NOVO)
  - Action: Template similar a `campaigns_new.html` mas pré-preenchido com dados da campanha.
    1. Carregar dados via rota Flask `admin_edit_campaign(campaign_id)` que passa `campaign` ao template.
    2. Campos desabilitados conforme ADR-5 (status da campanha). Indicador visual: "Pause a campanha para editar mensagens".
    3. **Seção de Leads**: tabela paginada (reusar padrão de `get_campaign_leads`). Colunas: nome, telefone, status, sent_at. Filtros por status. Atualizada pelo sync.
    4. Submit via `fetch('POST /api/admin/campaigns/{id}/update', {body: JSON})`.
  - Notes: Rota Flask: `@app.route('/admin/campaigns/<int:campaign_id>/edit')`, `@admin_required`. Query: `SELECT * FROM campaigns WHERE id = %s` (sem filtro user_id).

- [ ] **Task 15: Rotas Flask para servir as páginas de criação e edição**
  - File: `app.py`
  - Action:
    1. `@app.route('/admin/campaigns/new')` → `@admin_required` → `render_template('admin/campaigns_new.html')`.
    2. `@app.route('/admin/campaigns/<int:campaign_id>/edit')` → `@admin_required` → carregar campanha + steps + instâncias → `render_template('admin/campaigns_edit.html', campaign=..., steps=..., instances=...)`.
  - Notes: A rota de edição deve carregar: campanha (dict), steps (list), instâncias vinculadas (list), usuário dono (email).

#### Fase 6: Testes e Validação

- [ ] **Task 16: Teste de integração — criar campanha pelo admin**
  - File: `tests/test_admin_campaign_crud.py` (NOVO)
  - Action: Teste end-to-end (pytest + mock Uazapi):
    1. Mock `UazapiService.create_advanced_campaign` → retornar `{folder_id: 'test123'}`.
    2. POST `/api/admin/campaigns` com user_id, nome, instância, leads.
    3. Verificar: campanha existe no DB com `user_id` correto e `created_by_admin_id` preenchido.
    4. Verificar: `campaign_instances` vinculada.
    5. Verificar: `campaign_leads` com leads adicionados.
  - Notes: Reusar padrão de `tests/test_validate_job_csv.py` (mock + tempfile).

### Acceptance Criteria

- [ ] **AC 1:** Given superadmin logado em `/admin/campaigns`, when clica "Nova Campanha", then é redirecionado para `/admin/campaigns/new` com formulário completo (usuário, instâncias, leads, mensagens, horários, follow-ups).

- [ ] **AC 2:** Given superadmin em `/admin/campaigns/new`, when seleciona um usuário no dropdown, then instâncias e jobs daquele usuário são carregados via AJAX nos respectivos selects.

- [ ] **AC 3:** Given superadmin preenche formulário com todos os campos obrigatórios e clica "Criar", when campanha é criada, then: (a) registro em `campaigns` com `user_id` do usuário selecionado e `created_by_admin_id` do admin, (b) `campaign_instances` vinculada, (c) leads adicionados em `campaign_leads`, (d) `create_advanced_campaign` chamado na Uazapi, (e) `campaign_stage_sends` criado com `folder_id`.

- [ ] **AC 4:** Given campanha criada pelo superadmin, when usuário acessa `/campaigns` no seu painel, then campanha aparece normalmente na listagem com todos os dados corretos.

- [ ] **AC 5:** Given superadmin faz upload de CSV com 100 números e escolhe "Sim" no popup de validação, when validação executa, then: (a) progress bar exibe progresso, (b) números inválidos são removidos, (c) preview mostra "X válidos de 100", (d) superadmin confirma antes de criar.

- [ ] **AC 6:** Given superadmin faz upload de CSV e escolhe "Não" no popup de validação, when prossegue, then apenas validação de formato (regex) é aplicada e campanha é criada com todos os números formatados.

- [ ] **AC 7:** Given superadmin em `/admin/campaigns`, when página carrega, then: (a) cards mostram contadores do DB imediatamente, (b) AJAX dispara sync para campanhas `running`, (c) cards atualizam com valores reais da Uazapi quando sync retorna.

- [ ] **AC 8:** Given campanha com status `running` na listagem admin, when superadmin clica "Editar", then campos sensíveis (mensagem, instâncias) estão desabilitados com indicador "Pause para editar".

- [ ] **AC 9:** Given campanha com status `pending` ou `paused`, when superadmin edita e salva, then todos os campos permitidos são atualizados no DB (nome, horários, delay, toggles, follow-ups conforme ADR-5).

- [ ] **AC 10:** Given superadmin na tela de edição de campanha, when acessa seção de leads, then leads são listados com paginação, filtros por status, e status `sent` refletindo o último sync.

- [ ] **AC 11:** Given campanha criada pelo superadmin com cadência e 2 follow-ups, when `worker_cadence` executa, then follow-ups são processados normalmente (campaign_steps existe, cadence_config correto).

- [ ] **AC 12:** Given superadmin seleciona instância(s) no formulário, when instância(s) não pertencem ao `user_id` selecionado, then backend retorna erro 400 "Instância não pertence ao usuário selecionado".

- [ ] **AC 13:** Given superadmin em `/admin/campaigns`, when cards exibem 3 botões (Detalhes, Editar, Excluir), then todos cabem no card sem quebra de layout.

## Additional Context

### Dependencies

- `UazapiService` (`services/uazapi.py`) — já existente
- `utils/sync_uazapi.py` — `sync_campaign_leads_from_uazapi()` para sync de contadores
- `utils/validate_job_csv.py` — `_check_phone_with_retry()`, `_normalize_phone_for_api()`, `_get_connected_uazapi_token_for_user()` — reutilizar para validate number no upload CSV
- `utils/limits.py` — `PLAN_POLICY`, `get_user_daily_limit()`, `get_plan_policy()` — respeitar plano do usuário na criação
- `utils/uazapi_pacing.py` — `default_inter_message_delay_range_minutes()` — delay ponderado quando não definido manualmente
- Tabelas: `campaigns`, `campaign_leads`, `campaign_steps`, `campaign_instances`, `campaign_stage_sends`, `instances`, `users`, `scraping_jobs`, `licenses`, `uazapi_instance_sends`
- **Migração DB:** `ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER REFERENCES users(id) DEFAULT NULL`

### Testing Strategy

- **Teste automatizado** (`tests/test_admin_campaign_crud.py`): criação via API admin com mock Uazapi; verificar DB (campaigns, campaign_leads, campaign_instances, campaign_steps, campaign_stage_sends, created_by_admin_id)
- **Testes manuais — Criação:**
  1. Superadmin cria campanha para usuário X → verificar aparece em `/campaigns` do usuário X
  2. Campanha dispara na Uazapi → verificar folder_id no modal de detalhes
  3. Upload CSV com validate number → verificar contagem válidos/inválidos
  4. Upload CSV sem validate number → verificar apenas regex
  5. Criar campanha agendada → verificar status `pending` e que não dispara imediatamente
- **Testes manuais — Edição:**
  6. Editar campanha `pending` → todos os campos editáveis
  7. Editar campanha `running` → campos bloqueados
  8. Editar campanha `paused` → nome, horários, delay editáveis; mensagem bloqueada
  9. Verificar follow-ups persistem após edição
- **Testes manuais — Sync:**
  10. Carregar `/admin/campaigns` → contadores atualizam via AJAX
  11. Campanha `completed` → sync não é disparado
- **Testes manuais — Segurança:**
  12. Tentar criar campanha com instância de outro usuário → erro 400
  13. Trocar usuário no dropdown → instâncias e jobs recarregam, campos limpos

### Notes

- O botão de Detalhes (modal) deve ser mantido nos cards — traz informações importantes de debug.
- A campanha criada pelo superadmin deve ser indistinguível para o usuário final (exceto campo `created_by_admin_id` no DB).
- O campo `use_uazapi_sender` deve ser sempre `True` (hardcoded no helper).
- **Ordem de implementação sugerida:** Tasks 1-4 (DB + endpoints suporte) → Task 5 (refatoração core) → Task 6-7 (API admin) → Task 8-9 (sync) → Task 10-11 (edição) → Tasks 12-15 (frontend) → Task 16 (testes).
- **Risco alto:** Task 5 (refatoração) toca na rota mais crítica do sistema (`create_campaign`). Testar exaustivamente que a criação pelo usuário continua funcionando após extração do helper.
- `daily_limit` na tabela `campaigns` hoje é hardcoded 100 na criação (`L4270`). O helper deve usar `get_user_daily_limit(user_id)` para respeitar o plano.
