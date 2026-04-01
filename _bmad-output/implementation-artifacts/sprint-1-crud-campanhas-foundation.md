# Sprint 1: Foundation — DB Migration + Endpoints de Suporte

**Spec principal:** `_bmad-output/implementation-artifacts/tech-spec-crud-campanhas-superadmin.md`
**Sprint:** 1 de 4
**Escopo:** Tasks 1-4 (migração DB + 3 endpoints cascata)
**Risco:** Baixo
**Estimativa:** Pequeno — ~100-150 linhas de código novo

---

## Contexto Mínimo

Este sprint cria a fundação necessária para os sprints seguintes: coluna de auditoria no DB e 3 endpoints de API que alimentarão os dropdowns cascata do formulário de criação de campanhas pelo superadmin.

### Arquivos para LER antes de implementar

| Arquivo | Linhas | Motivo |
|---------|--------|--------|
| `app.py` L2900-2961 | Rota `admin_campaigns()` — padrão de rotas admin |
| `app.py` L3108-3135 | Rota `admin_users()` — query de usuários existente |
| `app.py` L3576-3596 | Rota `api_scraping_jobs()` — padrão de endpoint JSON |
| `app.py` L600-720 | Função `init_db()` — onde adicionar migração |

### Padrões a seguir

- Rotas admin usam `@login_required` + `@admin_required` decorators
- Queries usam `get_db_connection()` + `RealDictCursor` + `%s` placeholders
- Retornar JSON com `json.dumps()` ou `jsonify()`
- Fechar conexão com `conn.close()` ou `finally`

---

## Tasks

### Task 1: Migração DB — coluna `created_by_admin_id`

- **File:** `app.py` (função `init_db()`, seção de ALTER TABLEs de campaigns ~L700)
- **Action:** Adicionar:
  ```sql
  ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS created_by_admin_id INTEGER REFERENCES users(id) DEFAULT NULL;
  ```
- **Validação:** Rodar `init_db()` (restart app) e verificar coluna existe: `SELECT column_name FROM information_schema.columns WHERE table_name = 'campaigns' AND column_name = 'created_by_admin_id';`

### Task 2: Endpoint `GET /api/admin/users/list`

- **File:** `app.py` (próximo às rotas admin, após `admin_users()` ~L3135)
- **Action:** Nova rota:
  ```python
  @app.route('/api/admin/users/list')
  @login_required
  @admin_required
  def admin_users_list_api():
  ```
- **Query:**
  ```sql
  SELECT DISTINCT u.id, u.email
  FROM users u
  WHERE EXISTS (
      SELECT 1 FROM licenses l WHERE l.user_id = u.id AND l.status = 'active' AND l.expires_at > NOW()
  ) OR EXISTS (
      SELECT 1 FROM instances i WHERE i.user_id = u.id
  )
  ORDER BY u.email ASC
  ```
- **Response:** `json.dumps([{id, email} for each user], default=str)`
- **Segurança:** NÃO expor senha, apikey ou outros campos sensíveis.

### Task 3: Endpoint `GET /api/admin/users/<id>/instances`

- **File:** `app.py` (logo após Task 2)
- **Action:** Nova rota:
  ```python
  @app.route('/api/admin/users/<int:user_id>/instances')
  @login_required
  @admin_required
  def admin_user_instances_api(user_id):
  ```
- **Query:**
  ```sql
  SELECT id, name, status, COALESCE(api_provider, 'megaapi') as api_provider
  FROM instances
  WHERE user_id = %s AND COALESCE(api_provider, 'megaapi') = 'uazapi'
  ORDER BY id ASC
  ```
- **Response:** JSON array. NÃO expor `apikey`.

### Task 4: Endpoint `GET /api/admin/users/<id>/scraping-jobs`

- **File:** `app.py` (logo após Task 3)
- **Action:** Nova rota:
  ```python
  @app.route('/api/admin/users/<int:user_id>/scraping-jobs')
  @login_required
  @admin_required
  def admin_user_scraping_jobs_api(user_id):
  ```
- **Query:** (mesmo padrão de `api_scraping_jobs()` L3576-3596, mas parametrizado)
  ```sql
  SELECT id, keyword, locations, total_results, lead_count, created_at
  FROM scraping_jobs
  WHERE user_id = %s AND status = 'completed'
  ORDER BY created_at DESC
  ```
- **Response:** JSON array com `created_at` serializado via `.isoformat()`.

---

## Acceptance Criteria deste Sprint

- [x] Coluna `created_by_admin_id` existe na tabela `campaigns` (nullable, FK para users, ON DELETE SET NULL)
- [x] `GET /api/admin/users/list` retorna lista de usuários com licença/instância ativa
- [x] `GET /api/admin/users/1/instances` retorna instâncias Uazapi do usuário 1
- [x] `GET /api/admin/users/1/scraping-jobs` retorna jobs completados do usuário 1
- [x] Todos os endpoints retornam 403 para usuários não-admin
- [x] Nenhum endpoint expõe `apikey` ou `password`

## Verificação Pós-Sprint

Antes de prosseguir para Sprint 2, verificar:
1. Restart da app sem erros (migração executou)
2. Testar os 3 endpoints via curl/browser com usuário superadmin
3. Testar que endpoints retornam 403 para usuário comum

## Review Notes
- Status: **Completed**
- Adversarial review realizada com 10 findings
- 3 findings reais corrigidos (FK ON DELETE SET NULL, DISTINCT redundante, filtro expires_at NULL)
- 7 findings classificados como padrão existente/noise — não corrigidos neste sprint
