---
title: 'Validação automática de lista no upload e pós-extração'
slug: 'validacao-automatica-upload-extracao'
created: '2026-03-09'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Uazapi API', 'pandas', 'psycopg2']
files_to_modify: ['app.py', 'worker_scraper.py', 'templates/jobs.html', 'utils/validate_job_csv.py']
code_patterns: ['check_phone batch', '_normalize_phone_for_api', 'scraping_jobs results_path', 'upload_csv_leads job_id', 'extract_phone_from_whatsapp_link', 'phone_col whatsapp_link_col']
test_patterns: ['pytest tests/', 'test_*.py', 'test_background_scraping.py', 'test_campaign_creation.py']
---

# Tech-Spec: Validação automática de lista no upload e pós-extração

**Created:** 2026-03-09

## Overview

### Problem Statement

1. **Upload de lista:** Quando o usuário faz upload de CSV, a lista é salva e um job é criado. A validação via `/chat/check` (remover números sem WhatsApp) só ocorre na criação da campanha — e apenas se `use_uazapi_sender=true`. O usuário quer que a lista seja validada **automaticamente no momento do upload**, removendo inválidos antes de criar campanha.

2. **Pós-extração:** Quando uma extração (scraping) finaliza, a lista fica disponível em `/jobs`. O usuário quer que, **assim que a extração terminar**, a validação rode automaticamente: informar o usuário que está validando, remover inválidos e gerar uma lista 100% funcional (normalizada e validada).

### Solution

1. **Criar helper reutilizável** `validate_job_csv(job_id, user_id)` que:
   - Lê o CSV do `results_path` do job
   - Extrai telefones (reutilizar lógica de `create_campaign` e `_normalize_phone_for_api`)
   - Obtém token Uazapi do usuário (primeira instância Uazapi)
   - Chama `check_phone` em batches de 50 (POST /chat/check)
   - Filtra linhas com `isInWhatsapp=false`
   - Sobrescreve o CSV com apenas linhas válidas
   - Atualiza `lead_count` do job
   - Retorna `{valid, invalid, batches_skipped, partial}`

2. **Upload:** Em `upload_csv_leads`, após criar o job, se o usuário tiver instância Uazapi, chamar `validate_job_csv(job_id, user_id)` de forma síncrona. Retornar no JSON: `validated: true`, `valid`, `invalid`.

3. **Pós-extração:** No `worker_scraper`, chamar `validate_job_csv` diretamente após salvar o CSV e antes de `update_job_status`. O worker importa de `utils/validate_job_csv.py`.

### Scope

**In Scope:**
- Módulo `utils/validate_job_csv.py` com `validate_job_csv(job_id, user_id)` — lê CSV, valida via check_phone, sobrescreve com válidos
- Integração em `upload_csv_leads`: chamar validação após criar job (se user tem Uazapi)
- Integração em `worker_scraper`: chamar validação após job completed (antes de update_job_status final)
- Logs de debug com informações relevantes da validação (job_id, valid, invalid, batches_skipped, partial)
- API POST /chat/check: batch 50, token variável, números normalizados — [docs](https://docs.uazapi.com/endpoint/post/chat~check)

**MVP (sem UI):** Indicador "Validando números..." ou badge "Validado" em `/jobs` fica para fase 2.

**Out of Scope:**
- Validação para usuários sem instância Uazapi (skip silencioso)
- Migração de campanhas MegaAPI
- Validação em tempo real durante extração (apenas pós-complete)

## Context for Development

### Codebase Patterns

| Padrão | Localização | Notas |
|--------|-------------|-------|
| check_phone | services/uazapi.py:223 | POST /chat/check — aceita batch; retorna array (1 item por número). `isInWhatsapp` boolean. UAZAPI_URL env. |
| _normalize_phone_for_api | utils/sync_uazapi.py:14 | Retorna string normalizada ou None; extrai dígitos, adiciona 55 se 10–11 dígitos |
| extract_phone_from_whatsapp_link | app.py:3336 | wa.me/(\d+), phone=, whatsapp.com/send?phone=; fallback dígitos |
| create_campaign parse CSV | app.py:3454–3520 | cols lower; phone_col (phone/tel/cel), name_col, whatsapp_link_col; status=1; iterrows; final_phone de link ou raw_phone |
| upload_csv_leads | app.py:3264 | Salva em storage/{user_id}/Uploads/; cria scraping_job com results_path=filepath; retorna job_id, total_leads |
| worker_scraper | worker_scraper.py:70–212 | get_db_connection próprio (env DB_*); run_scraper_task; final_path antes de update_job_status; job NÃO tem results_path no DB até update |
| _check_phone_with_retry | app.py:4681 | Retry 2x, backoff 1s; batch 50; timeout 90s; 429: 3x, 2s |
| instances Uazapi | app.py:2096 | `COALESCE(api_provider, 'megaapi') = 'uazapi'` para filtrar |

### API check_phone (POST /chat/check)

**cURL:**
```bash
curl --request POST \
  --url https://neurix.uazapi.com/chat/check \
  --header 'Accept: application/json' \
  --header 'Content-Type: application/json' \
  --header 'token: <TOKEN>' \
  --data '{"numbers": ["5511999999999", "123456789@g.us"]}'
```

**Resposta 200:** array (1 item por número)
```json
[
  {"query": "string", "jid": "string", "lid": "string", "isInWhatsapp": false, "verifiedName": "string", "groupName": "string", "error": "string"}
]
```

**Variáveis:** `token` = apikey da instância Uazapi; `numbers` = array de strings normalizadas. Batch em princípio (50 por vez).

### Files to Reference

| File | Purpose |
|------|---------|
| app.py | upload_csv_leads (3264); create_campaign parse (3454–3520); extract_phone_from_whatsapp_link (3336); _check_phone_with_retry (4681) |
| worker_scraper.py | run_scraper_task (70); update_job_status (26); final_path/lead_count antes de update (205); get_db_connection próprio |
| services/uazapi.py | UazapiService.check_phone (223); base_url de UAZAPI_URL |
| utils/sync_uazapi.py | _normalize_phone_for_api (14) |
| templates/jobs.html | UI /jobs; job-card, status completed, botão CSV |
| tests/test_background_scraping.py | Padrão de teste para ScrapingJob |

### Technical Decisions

1. **Token por user:** `_get_uazapi_token_for_user(conn, user_id)` — query `SELECT apikey FROM instances WHERE user_id = %s AND COALESCE(api_provider, 'megaapi') = 'uazapi' LIMIT 1`. Retorna None se não houver.
2. **Parse CSV:** Mesma lógica create_campaign: cols lower; phone_col (phone/tel/cel), whatsapp_link_col; status=1; extract_phone + _normalize_phone_for_api. Manter ordem para mapear índice ↔ check_phone. **Encoding:** `pd.read_csv(..., encoding='utf-8', errors='replace')` ou tentar latin-1 para CSVs brasileiros.
3. **Sobrescrever CSV:** Escrever em arquivo temp (`path + '.tmp'`), depois `os.replace(temp_path, path)` para write-atômico. Evita corrupção se falhar no meio.
4. **Upload síncrono:** Usuário espera até 180s. Se falhar, retornar job_id mesmo assim (lista não validada).
5. **Extração no worker:** Chamar `validate_job_csv(job_id, user_id, file_path=final_path)` — worker tem final_path antes de update; job ainda não tem results_path no DB. Parâmetro `file_path` opcional: se fornecido, usa; senão lê do job.
6. **file_path opcional:** `validate_job_csv(job_id, user_id, file_path=None)`. Upload: file_path=None (job já tem results_path). Worker: file_path=final_path (job ainda não atualizado).
7. **Status "validando":** MVP sem mudança de UI; validação em background.
8. **CSV sem phone/whatsapp_link:** Se não há phone_col nem whatsapp_link_col, retornar None cedo; CSV permanece intacto.
9. **Logs de validação:** `print(f"[validate_job_csv] job_id={job_id} user_id={user_id} path={path} rows_with_phone={len(rows)}")` no início; ao retornar: `print(f"[validate_job_csv] job_id={job_id} valid={valid} invalid={invalid} batches_skipped={batches_skipped} partial={partial}")`; em falha: `print(f"[validate_job_csv] job_id={job_id} skip: {reason}")`.

## Implementation Plan

### Tasks

- [x] **Task 1: Criar utils/validate_job_csv.py**
  - File: `utils/validate_job_csv.py` (novo)
  - Action: Criar módulo com: (1) `_get_db_connection()` usando env DB_*; (2) `_get_uazapi_token_for_user(conn, user_id)`; (3) `_extract_phone_from_row(row, phone_col, whatsapp_link_col)` — recebe `row` (Series), acessa `row.get(phone_col)` e `row.get(whatsapp_link_col)`; extrai via regex wa.me, phone=, ou dígitos de phone_col; retorna string ou None; (4) `_check_phone_with_retry(uazapi, token, numbers)`; (5) `validate_job_csv` — ver fluxo abaixo. **CRÍTICO:** Usar `(df_idx, row)` de `df_filtered.iterrows()` — `df_idx` é o índice real do DataFrame, NÃO enumerate. `rows = [(df_idx, row, phone) for (df_idx, row) in df_filtered.iterrows() for phone in [_extract_phone_from_row(row, phone_col, whatsapp_link_col)] if phone]` — normalizar phone com `_normalize_phone_for_api` antes de incluir.
  - Notes: Write-atômico: `df_valid.to_csv(path + '.tmp')`; `os.replace(path + '.tmp', path)`. CSV encoding: `pd.read_csv(..., encoding='utf-8', errors='replace')`. Se não phone_col nem whatsapp_link_col: `print("[validate_job_csv] job_id={} skip: no phone column"); return None`. Logs: início `print(f"[validate_job_csv] job_id={job_id} user_id={user_id} path={path} rows_with_phone={len(rows)}")`; retorno `print(f"[validate_job_csv] job_id={job_id} valid={valid} invalid={invalid} batches_skipped={batches_skipped} partial={partial}")`; skip `print(f"[validate_job_csv] job_id={job_id} skip: {reason}")`.

- [x] **Task 2: Integrar upload_csv_leads**
  - File: `app.py`
  - Action: Após criar job (após conn.close() do bloco do INSERT), antes do return: verificar se user tem Uazapi. Se sim: `try: val = validate_job_csv(job_id, current_user.id); except Exception as e: print(f"[upload_csv_leads] validate_job_csv failed job_id={job_id}: {e}"); val = None`. Montar resp com `validated=bool(val)`, `valid=val['valid'] if val else count`, `invalid=val['invalid'] if val else 0`. Retornar JSON.
  - Notes: Usar `except Exception:` (não bare `except`). Se validação falhar, validated=false; valid=count; invalid=0.

- [x] **Task 3: Integrar worker_scraper**
  - File: `worker_scraper.py`
  - Action: Após `final_df.to_csv(final_path)` (linha ~201), antes de `update_job_status`: **só chamar validate_job_csv se `final_path` existe e não é vazio** (`if final_path and os.path.exists(final_path)`). Se sim: `try: val = validate_job_csv(job_id, user_id, file_path=final_path); lead_count = val['valid'] if val else len(final_df); except Exception as e: print(f"⚠️ [worker_scraper] validate_job_csv failed job_id={job_id}: {e}"); lead_count = len(final_df)`. Senão: `lead_count = len(final_df)`. No branch `else` (dfs vazio, linha 209): NÃO chamar validate_job_csv — `final_path` pode ser inválido; usar `lead_count = 0` e `update_job_status` normalmente.
  - Notes: validate_job_csv sobrescreve o CSV; worker usa lead_count retornado ou len(final_df) em fallback. Log de sucesso: `print(f"[worker_scraper] job_id={job_id} validated: valid={val['valid']} invalid={val['invalid']}")` quando val.

- [ ] **Task 4: UI /jobs (opcional — MVP skip)**
  - File: `templates/jobs.html`
  - Action: Para MVP: sem mudança. Futuro: badge "Validado" ou botão "Validar" para revalidar.
  - Notes: Validação ocorre em background; usuário vê lead_count atualizado ao recarregar.

- [ ] **Task 5: Endpoint POST /api/jobs/<id>/validate (opcional)**
  - File: `app.py`
  - Action: Endpoint para revalidação manual. Body vazio. Chama validate_job_csv, retorna {valid, invalid, partial}. Verificar job ownership. Log de chamada.
  - Notes: Útil para usuário revalidar lista após mudanças em instâncias.

### Fluxo validate_job_csv (detalhe)

```
1. Ler job do DB (user_id, results_path); verificar ownership; path = file_path or job['results_path']
2. Se não path ou não os.path.exists(path): print skip; return None
3. Ler CSV com pandas (encoding='utf-8', errors='replace'); identificar phone_col, whatsapp_link_col, status_col
4. Se não phone_col e não whatsapp_link_col: print skip; return None
5. Filtrar status=1 se existir; df_filtered
6. rows = []; for (df_idx, row) in df_filtered.iterrows(): raw = _extract_phone_from_row(row, phone_col, whatsapp_link_col); phone = _normalize_phone_for_api(raw) if raw else None; if phone: rows.append((df_idx, row, phone))
   — CRÍTICO: df_idx vem de iterrows(), é o índice real do DataFrame (não enumerate)
7. print(f"[validate_job_csv] job_id={job_id} path={path} rows_with_phone={len(rows)}")
8. token = _get_uazapi_token_for_user(conn, user_id); se não token: print skip; return None
9. Em batches de 50: numbers = [p for _,_,p in batch]; result = check_phone; mapear isInWhatsapp por posição j no batch; indices_drop usa df_idx do batch
10. df_valid = df_filtered[~df_filtered.index.isin(indices_drop)]; write-atômico: to_csv(path+'.tmp'); os.replace(path+'.tmp', path)
11. UPDATE scraping_jobs SET lead_count = len(df_valid) WHERE id = job_id
12. print(f"[validate_job_csv] job_id={job_id} valid={valid} invalid={invalid} batches_skipped={batches_skipped} partial={partial}")
13. return {valid, invalid, batches_skipped, partial}
```

### Acceptance Criteria

- [ ] **AC1:** Given upload de CSV com 100 números (15 sem WhatsApp), when upload concluído com user tem Uazapi, then CSV sobrescrito com 85 linhas; job.lead_count=85; resposta JSON inclui validated=true, valid=85, invalid=15
- [ ] **AC2:** Given extração completa com 50 leads, when worker finaliza, then validate_job_csv é chamado; CSV atualizado com apenas válidos; lead_count atualizado no job
- [ ] **AC3:** Given usuário sem instância Uazapi, when upload ou extração, then validação é skip (lista não modificada); sem erro; resposta upload com validated=false
- [ ] **AC4:** Given check_phone retorna 504/timeout em um batch, when validate_job_csv, then batches_skipped; partial=true; CSV mantém linhas dos batches que validaram; retorna resultado parcial
- [ ] **AC5:** Given job de outro usuário, when validate_job_csv(job_id, user_id), then retorna None (ownership verificado)
- [ ] **AC6:** Given CSV sem coluna phone ou whatsapp_link, when validate_job_csv, then retorna None cedo; CSV permanece intacto; log "skip: no phone column"
- [ ] **AC7:** Given upload com user Uazapi, when validate_job_csv lança exceção, then upload retorna job_id com validated=false; job não é perdido
- [ ] **AC8:** Given worker com dfs vazio (sem CSVs válidos), when job completa, then NÃO chama validate_job_csv; update_job_status com lead_count=0

### Dependencies

- Uazapi POST /chat/check (batch 50) — [docs](https://docs.uazapi.com/endpoint/post/chat~check)
- Token = apikey da instância Uazapi (instances.apikey)
- scraping_jobs: id, user_id, results_path, lead_count
- pandas, psycopg2, requests (via UazapiService)

### Testing Strategy

- **Unit:** Mock UazapiService.check_phone em test_validate_job_csv; CSV com 3 linhas, 1 inválido; assert CSV resultante tem 2 linhas; assert lead_count=2
- **Unit:** Mock sem token; assert validate_job_csv retorna None; CSV não modificado
- **Unit:** Verificar que logs contêm job_id, valid, invalid em retorno bem-sucedido (mock/capture print)
- **Integration:** Upload CSV real; verificar job.lead_count e arquivo sobrescrito
- **Manual:** Extração completa; verificar CSV em /jobs após worker concluir

### Notes

- Batch 50; timeout 90s; retry 2x; backoff 1s (conforme tech-spec validacao-chat-check)
- Upload: validação síncrona (user espera até 180s)
- Extração: validação no worker (background)
- extract_phone: duplicar em utils; usar _normalize_phone_for_api para garantir formato consistente (55+ dígitos)
- Write-atômico evita corrupção de CSV em falha
- Logs facilitam debug: job_id, valid, invalid, batches_skipped, partial, reason em skip
- Risco: timeout em upload para listas grandes; considerar async em fase 2
