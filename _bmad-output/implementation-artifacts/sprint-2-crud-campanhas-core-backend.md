# Sprint 2: Core Backend — Refatorar Criação + API Admin + Upload CSV

**Spec principal:** `_bmad-output/implementation-artifacts/tech-spec-crud-campanhas-superadmin.md`
**Sprint:** 2 de 4
**Pré-requisito:** Sprint 1 completo (endpoints cascata + coluna `created_by_admin_id`)
**Escopo:** Tasks 5-7 (extrair helper + endpoint criação admin + upload CSV com validate)
**Risco:** ALTO — Task 5 refatora a rota mais crítica do sistema (`create_campaign`)
**Estimativa:** Grande — ~400-600 linhas de código (refatoração + novo)

---

## Contexto Mínimo

Este sprint é o coração da feature. Extrai a lógica de criação de campanhas em um helper reutilizável, cria o endpoint de criação pelo admin, e implementa upload de CSV com validação de números.

### Arquivos para LER INTEIROS antes de implementar

| Arquivo | Motivo |
|---------|--------|
| `app.py` L4062-4593 | Rota `create_campaign()` — TODA a lógica a ser refatorada (~530 linhas) |
| `app.py` L5489-5515 | Helper `_get_uazapi_instances_for_campaign()` |
| `app.py` L5518-5540 | Helper `_resolve_uazapi_remote_jid()` |
| `app.py` L3629-3700 | Helper `_uazapi_control_campaign()` |
| `utils/validate_job_csv.py` | INTEIRO — padrão de validate number a reutilizar |
| `utils/limits.py` L1-100 | `PLAN_POLICY`, `get_user_daily_limit()` |
| `utils/uazapi_pacing.py` L1-40 | `default_inter_message_delay_range_minutes()` |
| `services/uazapi.py` L229-260 | `check_phone()` |
| `services/uazapi.py` L264-315 | `create_advanced_campaign()` |

### Padrões críticos

- `create_campaign()` hoje usa `current_user.id` em ~15 lugares. O helper deve usar `user_id` parametrizado.
- `daily_limit` hoje é hardcoded `100` (L4270). O helper deve usar `get_user_daily_limit(user_id)`.
- `use_uazapi_sender` deve ser sempre `True` (hardcoded).
- `rotation_mode` = `'round_robin'` se `len(instance_ids) > 1`, senão `'single'`.
- Media storage path: `storage/{user_id}/campaign_media/` (usar `user_id`, não `current_user.id`).
- Validate number: batch 5, `_check_phone_with_retry`, retry 2x, timeout 30s, pausa 2s entre batches.

---

## Tasks

### Task 5: Extrair `_create_campaign_core(user_id, data, admin_id=None)`

- **File:** `app.py`
- **Action PASSO A PASSO:**

  1. **Criar o helper** `_create_campaign_core(user_id, data, admin_id=None)` acima da rota `create_campaign()`.

  2. **Mover o corpo** da rota `create_campaign()` (de L4079 até L4593, após `data = request.json`) para dentro do helper. O helper retorna o mesmo `json.dumps(...)` que a rota retornava.

  3. **Substituir `current_user.id`** por `user_id` em TODAS as ocorrências dentro do helper:
     - `current_user.id` → `user_id` (INSERT campaigns, SELECT instances, SELECT campaign_leads, storage path, etc.)
     - `is_super_admin()` → `admin_id is not None` (para lógica de mídia step 1)

  4. **Adicionar `created_by_admin_id`** no INSERT de campaigns:
     ```sql
     INSERT INTO campaigns (user_id, name, ..., created_by_admin_id)
     VALUES (%s, %s, ..., %s)
     ```
     Passar `admin_id` (que será `None` para criação normal do usuário).

  5. **Substituir `daily_limit` hardcoded** (`100` em L4270):
     ```python
     from utils.limits import get_user_daily_limit
     daily_limit = get_user_daily_limit(user_id)
     ```

  6. **A rota `create_campaign()` existente** fica assim:
     ```python
     @app.route('/api/campaigns', methods=['POST'])
     @login_required
     def create_campaign():
         return _create_campaign_core(current_user.id, request.json)
     ```

  7. **TESTAR** que a criação normal pelo usuário continua funcionando (mesmos inputs, mesmos outputs).

- **RISCO:** Esta é a task mais perigosa. Testar exaustivamente:
  - Criar campanha pelo painel do usuário normal → funciona igual antes
  - `campaign_instances` criados corretamente
  - `campaign_steps` criados se cadência ativa
  - `campaign_stage_sends` com folder_id da Uazapi
  - `campaign_leads` com send_batch atribuído

### Task 6: Endpoint `POST /api/admin/campaigns`

- **File:** `app.py`
- **Action:** Nova rota:
  ```python
  @app.route('/api/admin/campaigns', methods=['POST'])
  @login_required
  @admin_required
  def admin_create_campaign():
      data = request.json
      target_user_id = data.get('user_id')
      if not target_user_id:
          return json.dumps({'error': 'user_id é obrigatório'}), 400
      # Verificar que usuário existe
      conn = get_db_connection()
      with conn.cursor() as cur:
          cur.execute("SELECT id FROM users WHERE id = %s", (target_user_id,))
          if not cur.fetchone():
              conn.close()
              return json.dumps({'error': 'Usuário não encontrado'}), 404
      conn.close()
      return _create_campaign_core(target_user_id, data, admin_id=current_user.id)
  ```
- **Validação backend:** O helper `_create_campaign_core` já valida que `instance_ids` pertencem ao `user_id` (Task 5 garante isso ao substituir `current_user.id` por `user_id`).

### Task 7: Endpoint `POST /api/admin/campaigns/validate-csv`

- **File:** `app.py`
- **Action:** Nova rota `@admin_required`. Aceita multipart form:
  - `file`: CSV upload
  - `user_id`: ID do usuário dono
  - `validate_whatsapp`: `"true"` ou `"false"`

- **Lógica:**
  1. Parse CSV com pandas (`pd.read_csv(file, dtype=str)`).
  2. Extrair telefones usando `_normalize_phone_for_api()` de `utils/validate_job_csv.py` (ou do `utils/sync_uazapi.py` — mesma função).
  3. Remover duplicados.
  4. Se `validate_whatsapp == "true"`:
     - Obter token: `_get_connected_uazapi_token_for_user(conn, user_id)` de `utils/validate_job_csv.py`.
     - Se nenhuma instância conectada: retornar `{error: "Nenhuma instância Uazapi conectada para validar"}`, 400.
     - Executar validação em batch 5 (mesma lógica de `validate_job_csv.py` L259-298):
       ```python
       from utils.validate_job_csv import _check_phone_with_retry, _get_connected_uazapi_token_for_user
       from services.uazapi import UazapiService
       uazapi = UazapiService()
       BATCH_SIZE = 5
       indices_drop = set()
       for i in range(0, len(rows), BATCH_SIZE):
           batch = rows[i:i+BATCH_SIZE]
           numbers = [phone for _, phone in batch]
           result, err = _check_phone_with_retry(uazapi, token, numbers, timeout=30)
           if result:
               for j, item in enumerate(result):
                   if j < len(batch) and not item.get('isInWhatsapp', True):
                       indices_drop.add(batch[j][0])
           if i + BATCH_SIZE < len(rows):
               time.sleep(2)
       ```
  5. Salvar CSV validado em `storage/{user_id}/uploads/admin_upload_{timestamp}.csv`.
  6. Criar `scraping_job` fictício:
     ```sql
     INSERT INTO scraping_jobs (user_id, keyword, locations, total_results, lead_count, status, results_path, created_at)
     VALUES (%s, 'Upload Admin', 'Upload', %s, %s, 'completed', %s, NOW())
     RETURNING id
     ```
  7. Retornar `{valid, invalid, job_id}`.

---

## Acceptance Criteria deste Sprint

- [x] `_create_campaign_core()` existe e é chamado tanto pela rota do usuário quanto pela rota admin
- [x] Criar campanha pelo painel do usuário normal continua funcionando identicamente
- [x] `POST /api/admin/campaigns` cria campanha com `user_id` do usuário selecionado e `created_by_admin_id` do admin
- [x] `POST /api/admin/campaigns` valida que `instance_ids` pertencem ao `user_id` selecionado (retorna 400 se não)
- [x] Campanha criada pelo admin aparece no painel do usuário (`/campaigns`)
- [x] `daily_limit` usa `get_user_daily_limit(user_id)` em vez de hardcoded 100
- [x] `POST /api/admin/campaigns/validate-csv` com `validate_whatsapp=true` remove números inválidos
- [x] `POST /api/admin/campaigns/validate-csv` com `validate_whatsapp=false` aplica apenas regex
- [x] Upload CSV cria `scraping_job` fictício que pode ser usado pelo `_create_campaign_core`

## Review Notes
- Adversarial review completed
- Findings: 10 total, 2 fixed (F1, F3), 8 skipped (noise/pré-existente/intencional)
- Resolution approach: auto-fix

## Verificação Pós-Sprint

**CRÍTICO:** Antes de prosseguir, testar:
1. Criar campanha pelo painel do **usuário comum** → funciona igual antes (regressão zero)
2. Criar campanha pelo endpoint admin → campanha aparece para o usuário com todos os dados
3. Verificar `campaign_steps` criados se follow-ups configurados
4. Verificar `campaign_stage_sends` com folder_id
5. Upload CSV com validação → contagem correta
