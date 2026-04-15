---
project_name: leads-infinitos-saas
user_name: Augusto
date: '2026-03-04'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'implementation_rules', 'roadmap_context', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: complete
rule_count: 35
optimized_for_llm: true
---

# Project Context for AI Agents

_Este arquivo contém regras críticas e padrões que agentes de IA devem seguir ao implementar código neste projeto. Foco em detalhes não óbvios que agentes podem ignorar._

---

## Visão do Produto

**Leads Infinitos** é um SaaS com as seguintes funcionalidades principais:

1. **Extração de contatos Google Maps** — via Apify (Actor compass/crawler-google-places)
2. **Criação de listas e campanhas** — com mensagens personalizadas (spintax, variáveis)
3. **Disparo de campanha** — via API não oficial de WhatsApp (atualmente MegaAPI; migração para Uazapi planejada)

O app foi desenvolvido sem PRD completo. O contexto de projeto é de **atualização incremental de features** — alterações devem ser feitas uma feature por vez, com validação antes de aplicar globalmente.

---

## Technology Stack & Versions

| Tecnologia | Versão | Uso |
|------------|--------|-----|
| Python | 3.x | Runtime principal |
| Flask | ≥3.0.0 | Web framework |
| Flask-Login | ≥0.6.3 | Autenticação |
| Werkzeug | ≥3.0.0 | Utilitários WSGI |
| PostgreSQL | 15 | Banco de dados (psycopg2-binary ≥2.9.0) |
| Redis | 5.x | Filas e cache |
| RQ | 1.16.2 | Redis Queue para jobs assíncronos |
| Apify Client | (apify-client) | Extração Google Maps |
| OpenAI | ≥1.0.0 | IA (futuro: agente de respostas) |
| pandas | 2.2.3 | Manipulação de dados |
| playwright | 1.52.0 | (legado; extração agora via Apify) |
| requests | ≥2.31.0 | HTTP client |
| python-dotenv | ≥1.0.0 | Variáveis de ambiente |

**Integrações externas:**
- **MegaAPI** (atual): `MEGA_API_URL`, `MEGA_API_TOKEN` — WhatsApp não oficial
- **Uazapi** (planejado): substituir MegaAPI para disparo e follow-up
- **Apify**: `APIFY_TOKEN` — extração Google Maps
- **Chatwoot** (opcional): `CHATWOOT_API_URL`, `CHATWOOT_ACCESS_TOKEN`, `CHATWOOT_ACCOUNT_ID` — labels/status para follow-up
- **Hotmart/Hubla**: webhooks de licenciamento

---

## Arquitetura e Estrutura

- **Monolito Flask** — `app.py` central (~4600 linhas); classes de domínio (User, License, Campaign, CampaignLead, ScrapingJob, WhatsappService, etc.) no mesmo arquivo. **Modularização:** deixar para refatoração dedicada; preferir extrair módulos incrementalmente ao tocar em features (ex.: ao migrar para Uazapi, extrair `WhatsappService` para `services/whatsapp.py`)
- **Workers separados**: `worker_sender.py` (disparo), `worker_cadence.py` (follow-up/cadência), `main.py` (scraper Apify)
- **Banco**: PostgreSQL com `psycopg2`; placeholders `%s` (não `?`); `RealDictCursor` em workers
- **Filas**: Redis + RQ para scraping e email assíncrono
- **Storage**: `storage/{user_id}/` para CSVs de leads e uploads
- **Templates**: Jinja2 em `templates/`; `base.html` com tema escuro (CSS variables)

---

## Critical Implementation Rules

### 1. Banco de Dados
- Usar **`%s`** como placeholder em queries (PostgreSQL via psycopg2)
- Usar `RETURNING id` em INSERTs quando precisar do ID gerado
- `get_db_connection()` retorna conexão raw; fechar com `conn.close()` ou usar context manager
- Migrações manuais via `init_db()` ou scripts `migrate_*.py`

### 2. Autenticação e Autorização
- `@login_required` para rotas protegidas
- `@admin_required` para área admin
- `is_super_admin(user)` — email `augustogumi@gmail.com` tem acesso superadmin
- `current_user` do Flask-Login para usuário logado

### 3. Multi-tenancy
- Sempre filtrar por `user_id` ou `campaign_id` pertencente ao usuário
- `_is_path_owned_by_current_user(path)` para validar acesso a arquivos em `storage/`

### 4. WhatsApp / Disparo
- **MegaAPI** (atual): `WhatsappService` em `app.py`; workers usam `MEGA_API_URL`, `MEGA_API_TOKEN`
- Endpoints: `POST /rest/instance/init`, `GET /rest/instance/qrcode/{key}`, `GET /rest/instance/{key}`
- Formato JID: `55{número}@s.whatsapp.net` (função `format_jid` em workers)
- **Uazapi** (planejado): migrar superadmin primeiro, validar, depois aplicar globalmente

### 5. Campanhas e Cadência
- `Campaign` com `enable_cadence`, `cadence_config` (JSON)
- `CampaignLead` com status (pending, sent, replied, etc.)
- `worker_cadence.py` usa Chatwoot para labels/status antes de enviar follow-up
- Horário comercial: 8h–20h (America/São_Paulo)

### 6. Limites e Planos
- `License.daily_limit` por tipo — **apenas starter/pro/scale** (semestral/anual não existem)
- Verificar `2k leads/mês` — lógica em `ScrapingJob.get_monthly_lead_count`
- Superadmin tem limite diário próprio por instância

### 7. Frontend
- Tema escuro em `base.html` (CSS variables: `--bg`, `--primary`, etc.)
- **Modo claro** e toggle planejados — preparar variáveis CSS para ambos
- Jinja2: `{% block content %}`, `url_for()`, `flash()`

### 8. Variáveis de Ambiente
- `.env` local; em produção (Dokploy): `APIFY_TOKEN`, `MEGA_API_*`, `DB_*`, `REDIS_URL`, etc.
- Nunca commitar credenciais

---

## Roadmap de Features (Contexto para Implementação)

As alterações abaixo devem ser feitas **uma feature por vez**, preferencialmente via Quick Spec + Quick Dev.

| # | Feature | Notas |
|---|---------|-------|
| 1 | **Migrar disparador superadmin para Uazapi** | Validar no superadmin antes de aplicar globalmente. Atualmente usa MegaAPI. |
| 2 | **Lógica de campanha pelo Uazapi + follow-up** | Incluir lógica de FU e resposta. Definir: como remover do kanban quando lead responde positivamente? |
| 3 | **Verificar limitação 2k leads/mês** | Garantir que `get_monthly_lead_count` e UI respeitam o limite. |
| 4 | **Agente de IA simples** | Responder primeiras perguntas via Uazapi. Pode ser solução para remover card do follow-up ao responder. |
| 5 | **Upload de imagem e vídeo** | Na mensagem personalizada da campanha. |
| 6 | **Kanban funcional** | Sem integração inicial; apenas movimentação manual de cards. |
| 7 | **Agente de IA integrado (Uazapi)** | Feature completa de IA no fluxo de campanha. |
| 8 | **Modo claro + toggle** | Adicionar tema claro e toggle de opção no frontend. |
| 9 | **Remover planos semestral/anual do código** | Migrar schema (CHECK constraint), `License.daily_limit`, `HublaService` e testes para usar apenas starter/pro/scale. |

---

## Decisões Arquiteturais (Registro)

- **Frontend:** Manter Jinja2/HTML por enquanto. Migração para Next.js seria refatoração grande; avaliar apenas se houver necessidade forte de SPA, componentes reutilizáveis ou equipe React. Ver "Nota: Next.js" em referências.
- **Planos:** Apenas starter, pro, scale. Sem semestral/anual.

---

## Padrões de Código

- **Naming**: snake_case para funções/variáveis; PascalCase para classes
- **Imports**: agrupados (stdlib, third-party, local)
- **Logs**: emoji prefixos (`🔄`, `✅`, `❌`) em workers para legibilidade
- **Docs**: `docs/D*_*.md` para planos de implementação; manter atualizado ao alterar features

---

## Testes

- `tests/` com pytest (inferido por `test_*.py`)
- `test_campaign_creation.py`, `test_webhook_public.py`, `test_load.py` existentes

---

## Referências Rápidas

- `docs/D1_ARCHITECTURE_MIGRATION.md` — migração SQLite→Postgres, Redis+RQ
- **Nota: Next.js** — Migração para Next.js: ganhos (SPA, React ecosystem, componentes) vs custos (refatoração grande, auth/session, deploy). Recomendação: manter Jinja2 até features exigirem UX mais rica (ex.: kanban drag-and-drop complexo, real-time)
- `docs/D2_IMPLEMENTATION_PLAN.md` — MegaAPI, SMTP, limites
- `docs/D4_IMPLEMENTATION_PLAN.md` — Apify integration
- `docs/SAAS_IMPLEMENTATION_CHECKLIST.md` — checklist geral

---

## Usage Guidelines

**Para agentes de IA:**
- Ler este arquivo antes de implementar qualquer código
- Seguir TODAS as regras exatamente como documentado
- Em dúvida, preferir a opção mais restritiva
- Atualizar este arquivo se novos padrões surgirem

**Para humanos:**
- Manter o arquivo enxuto e focado nas necessidades dos agentes
- Atualizar quando o stack ou convenções mudarem
- Revisar trimestralmente regras desatualizadas
- Remover regras que se tornem óbvias com o tempo

_Última atualização: 2026-03-04_
