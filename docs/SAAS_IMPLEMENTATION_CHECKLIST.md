# Plano e Checklist – Transformar o Scraper em SaaS

## Objetivo
Entregar uma versão SaaS com login/senha, planos/licenças, execuções concorrentes via fila, e deploy na VPS (Hostinger) com Dokploy. Downloads protegidos por usuário.

## Arquitetura-Alvo (resumo)
- Web: Flask + Gunicorn
- Fila: Redis + RQ (workers)
- Banco: PostgreSQL (prod) / SQLite (dev)
- Navegador: Playwright (Chromium) em workers headless
- Proxy/HTTPS: Nginx (gerenciado pelo Dokploy)
- Storage: pasta por usuário (`storage/<userId>/...`) no host; opcional S3 futuramente
- Observabilidade: logs estruturados, métricas simples, Sentry (opcional)

---

## Ordem de Implementação (MVP 1 → MVP 3)
1) MVP 1 – Autenticação + Jobs síncronos por usuário
   - [x] Login/Logout/Registro (Flask-Login + senha com hash)
   - [x] Encapsular `run_scraper` como serviço por usuário e salvar em `storage/<userId>/...`
   - [x] Rotas de download que validam owner
2) MVP 2 – Fila e Concorrência
   - [ ] Redis + RQ
   - [ ] Enfileirar jobs; página de status (polling) com progresso básico
   - [ ] Limite de jobs simultâneos por usuário
3) MVP 3 – Licenças/Planos e Quotas
   - [ ] Modelos de plano/licença
   - [ ] Contabilizar leads/dia e bloquear ao exceder
   - [ ] Stripe Subscriptions + webhook para ativar/suspender licença

---

## Modelagem Inicial (SQL)
- `users(id, email UNIQUE, password_hash, created_at, is_active)`
- `subscriptions(id, user_id FK, plan, status, current_period_start, current_period_end)`
- `jobs(id, user_id FK, query, total, status[pending,running,done,failed], csv_path, xlsx_path, created_at, finished_at, error)`
- `usage_daily(id, user_id FK, date, leads_count)`

---

## Checklist Detalhado

### 1) Autenticação e Sessão
- [x] Instalar `Flask-Login`, `Werkzeug` para hash
- [x] Páginas: `/register`, `/login`, `/logout`
- [x] Decorator `@login_required` nas rotas de extrair/baixar
- [x] Middleware que injeta `current_user.id`

### 2) Armazenamento e Escopo por Usuário
- [x] Diretório base configurável: `STORAGE_DIR` (env)
- [x] Paths: `storage/<userId>/<YYYY-MM-DD>/<arquivo>`
- [x] Rotas `/download?path=` validam owner por prefixo

### 3) Fila e Concorrência
- [ ] Instalar `rq`, `redis`
- [ ] Config `REDIS_URL`
- [ ] Fila `default` com `queue.enqueue(run_scraper, ...)`
- [ ] Worker: `rq worker default`
- [ ] Limite por usuário: `MAX_PARALLEL_JOBS_PER_USER` (env) com chave Redis `inflight:<userId>`
- [ ] Atualizar `jobs.status` em transições (pending → running → done/failed)

### 4) Quotas e Licenças (MVP)
- [ ] Tabela `subscriptions` simples (manual/seed); depois Stripe
- [ ] Env: `DAILY_LEADS_QUOTA`
- [ ] Antes de iniciar job, somar `usage_daily.leads_count` de hoje
- [ ] Após job, incrementar leads gerados

### 5) UI/UX (mínimo viável)
- [ ] Tela de login/registro (pt-BR)
- [ ] Página “Nova extração” (usa sessão)
- [ ] Página “Meus Jobs”: status, data, links de download
- [ ] Página “Status do Job”: polling (JS) até `done`/`failed`

### 6) Observabilidade
- [ ] Logger padrão JSON (nível INFO)
- [ ] IDs de job em logs
- [ ] Métricas simples: jobs por status, duração média (exposto em `/metrics` simples ou logs)
- [ ] Sentry DSN (opcional, env)

### 7) Segurança
- [ ] Senhas com `werkzeug.security.generate_password_hash`
- [ ] CSRF (usar `Flask-WTF` ou token simples para POSTs sensíveis)
- [ ] Limitar tamanho de consulta/`total`
- [ ] Sanitizar input em `query`
- [ ] Headers de segurança básicos (X-Frame-Options, etc.)

### 8) Deploy (Dokploy / Hostinger)
- [ ] Dockerfile (Python slim + playwright install chromium)
- [ ] docker-compose: `web`, `worker`, `redis`
- [ ] Variáveis no Dokploy: `FLASK_SECRET`, `REDIS_URL`, `DATABASE_URL`, `STORAGE_DIR`, cotas/limites
- [ ] Domínio + HTTPS (Let’s Encrypt)
- [ ] Healthcheck (`/` ou `/healthz`)
- [ ] Volumes persistentes: `storage/`, `redis` e (se SQLite) `app.db`

### 9) Testes e QA
- [ ] Smoke test de autenticação (register → login → extrair → baixar)
- [ ] Teste de concorrência: 2–3 jobs simultâneos (ver limite e isolamento)
- [ ] Teste de quota diária (forçar excedente)
- [ ] Teste de permissionamento de download (usuário A não baixa de B)

---

## Tarefas Granulares (prontas para issues)

### Issue: Autenticação básica
- [ ] Adicionar `users` (SQLite inicialmente)
- [ ] Rotas `/auth/register`, `/auth/login`, `/auth/logout`
- [ ] Proteger `/scrape` e `/download`
- [ ] Teste manual do fluxo

### Issue: Fila com Redis/RQ
- [ ] Adicionar `redis` ao compose
- [ ] Criar `queue.py` e `worker.py`
- [ ] Enfileirar `run_scraper` (não bloquear requisição)
- [ ] Tela “Status do Job” com polling

### Issue: Limite de jobs simultâneos por usuário
- [ ] Chave `inflight:<userId>` no Redis (INCR/DECR com TTL de segurança)
- [ ] Bloquear novo job se exceder `MAX_PARALLEL_JOBS_PER_USER`

### Issue: Quota diária de leads
- [ ] Tabela `usage_daily`
- [ ] Checagem antes do enqueue
- [ ] Atualização após o job

### Issue: Deploy no Dokploy
- [ ] Criar app (web) e app (worker)
- [ ] Anexar Redis gerenciado ou container
- [ ] Configurar domínio/HTTPS e variáveis de ambiente
- [ ] Volumes e backups (storage/db)

---

## Variáveis de Ambiente (min em produção)
- `FLASK_SECRET`
- `DATABASE_URL` (Postgres recomendado)
- `REDIS_URL`
- `STORAGE_DIR` (ex.: `/data/storage`)
- `MAX_PARALLEL_JOBS_PER_USER` (ex.: 1 ou 2)
- `DAILY_LEADS_QUOTA` (ex.: 500)
- `SENTRY_DSN` (opcional)

---

## Critérios de Aceite (DoD)
- [ ] Usuário consegue registrar, logar e iniciar extração
- [ ] Job roda em worker e libera UI imediatamente
- [ ] Downloads somente para o dono
- [ ] Limite de concorrência por usuário respeitado
- [ ] Quota diária aplicada
- [ ] Deploy reproduzível no Dokploy, HTTPS funcionando

---

## Roadmap Futuro
- Multi-tenant robusto (schemas por cliente ou colunas com `tenant_id`)
- Armazenamento S3 + links assinados
- Webhooks de conclusão de job
- Fila com Celery e autoscale (opcional)
- Melhorias de UI/UX (histórico, filtros, reexecução) 