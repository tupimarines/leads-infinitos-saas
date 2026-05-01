# Mapeamento de código e nomenclatura — campanhas Uazapi

Referência rápida para localizar símbolos e nomes canônicos no repositório.

---

## Backend principal

| Nome canônico | Ficheiro | Função / papel |
|---------------|----------|----------------|
| `_create_campaign_core` | `app.py` | Núcleo HTTP de criação de campanha (BD + primeiro lote Uazapi). |
| `_continue_initial_chunk_core` | `app.py` | Continua chunk initial: valida `use_uazapi_sender` + `enable_cadence`, agenda/materializa `campaign_stage_sends`. |
| `_create_stage_campaign` | `app.py` | Criação de envios por etapa (ex. stage campaign) com `create_advanced_campaign`. |
| `Campaign` (modelo) | `app.py` | Classe com `enable_cadence`, `use_uazapi_sender`, `uazapi_folder_id`, etc. |
| Rotas admin campanhas | `app.py` | Procurar por `campaign_id`, `continue-initial-chunk`, endpoints JSON associados. |

---

## Serviço Uazapi

| Método | Ficheiro | API HTTP |
|--------|----------|----------|
| `create_advanced_campaign` | `services/uazapi.py` | `POST /sender/advanced` |
| `edit_campaign` | `services/uazapi.py` | `POST /sender/edit` |
| `list_folders` | `services/uazapi.py` | `GET /sender/listfolders` |
| (listagens de mensagens) | `services/uazapi.py` | `POST /sender/listmessages` |

---

## Worker cadência

| Nome | Ficheiro | Papel |
|------|----------|--------|
| `process_cadence` | `worker_cadence.py` | Loop principal; filtra campanhas; chama agendamento/materialização/cadência. |
| `_materialize_scheduled_stage_sends` | `worker_cadence.py` | Converte `campaign_stage_sends` `scheduled` em pastas via API. |
| `schedule_next_initial_chunk` | `worker_cadence.py` | Próximo chunk initial (quota, janela BRT, INSERT `scheduled`). |
| `_materialize_scheduled_stage_sends` pré-sync | `worker_cadence.py` | Chama `sync_campaign_stage_sends_before_new_chunk`. |

---

## Utilitários

| Módulo | Ficheiro | Conteúdo relevante |
|--------|----------|-------------------|
| Pacing | `utils/uazapi_pacing.py` | `default_inter_message_delay_range_minutes`, `build_pacing_segments_for_leads`, `maybe_long_gap_minutes`, `stagger_scheduled_utc_naive` |
| Limites chunk | `utils/limits.py` | `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`, `can_create_campaign_today`, integração com quotas |
| Política envio | `utils/campaign_send_policy.py` | `uazapi_initial_chunk_distribution_limits`, `INITIAL_CHUNK_DAILY_QUOTA_POLICY`, `initial_chunk_daily_quota_allows` |
| Janela envio | `utils/next_valid_uazapi_send.py` | `is_campaign_send_window`, `next_valid_send_utc_naive` |
| Alvo agendamento initial | `utils/initial_chunk_schedule_target.py` | `resolve_initial_chunk_schedule_target` |
| Sync Uazapi | `utils/sync_uazapi.py` | `sync_campaign_stage_sends_before_new_chunk`, `sync_campaign_leads_from_uazapi`, `get_uazapi_campaign_counts`, … |

---

## Base de dados (nomes de tabela/coluna)

- **`campaigns`:** `use_uazapi_sender`, `enable_cadence`, `cadence_config`, `daily_limit`, `delay_min_minutes`, `delay_max_minutes`, `uazapi_folder_id`, `uazapi_last_send_lead_ids`, `scheduled_start`, janela de envio.
- **`campaign_leads`:** `send_batch`, `status`, `current_step`, `cadence_status`, `removed_from_funnel`.
- **`campaign_stage_sends`:** `stage` (`initial`, `follow1`, …), `instance_id`, `uazapi_folder_id`, `status`, `scheduled_for`, `lead_ids` (JSONB), `planned_count`, `success_count`, `failed_count`, delays e variações por linha.
- **`campaign_instances`:** ligação campanha ↔ `instances`.
- **`uazapi_instance_sends`:** registo de envio por instância/campanha.

DDL evolutivo: ver `app.py` (migrações inline `CREATE TABLE` / `ALTER TABLE` próximos de `campaign_stage_sends`).

---

## Frontend (admin)

- **`templates/admin/campaigns_new.html`** — formulário criação (flags, steps, limites).
- Procurar strings: `enable_cadence`, `use_uazapi_sender`, `cadence_setup_mode`, limites diários.

---

## Testes úteis

- `tests/test_admin_campaign_crud.py` — criação admin.
- `tests/test_worker_stale_recovery.py` — recovery `campaign_stage_sends`.

---

## OpenAPI local

- `uazapi-openapi-spec (1).yaml` — operationId `sendAdvancedCampaign` em `/sender/advanced` (referência de contrato; validar sempre com `services/uazapi.py`).
