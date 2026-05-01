# Sistema atual: campanhas Uazapi, `create_advanced_campaign`, cadência e chunks

**Objetivo:** descrever o fluxo real do repositório (não a API genérica) para substituição futura do mecanismo de envio.

**Data de referência:** 2026-04-29.

---

## Visão geral

1. **App (`app.py`)** persiste `campaigns`, `campaign_leads`, `campaign_instances`, opcionalmente `campaign_steps` + `cadence_config`, e dispara **Uazapi** via `UazapiService.create_advanced_campaign` (HTTP `POST /sender/advanced`).
2. **Cada chamada** cria uma “pasta” remota (`folder_id`) com vários destinatários e delays **dentro** da pasta (min/max em segundos entre mensagens).
3. **Estado local** de cada pasta fica em `campaign_stage_sends` (stage `initial`, follow-ups, etc.).
4. **`worker_cadence.py`** agenda próximos chunks (`schedule_next_initial_chunk`), materializa linhas `scheduled` (`_materialize_scheduled_stage_sends`), sincroniza contagens com a API e processa cadência multi-etapa quando `enable_cadence` é verdadeiro.

---

## `create_advanced_campaign` (API Uazapi)

- **Cliente:** `services/uazapi.py` → método `UazapiService.create_advanced_campaign`.
- **Endpoint:** `{base_url}/sender/advanced`.
- **Payload relevante:** `delayMin`, `delayMax` (segundos), `messages` (lista de `{number, type, text}` ou mídia), `info`, `scheduled_for` (minutos até início, quando aplicável).
- **Resposta típica:** `folder_id`, `count`, `status` (ex.: fila `queued` no provedor).
- **Controle remoto:** `edit_campaign` (`POST /sender/edit`: `stop` | `continue` | `delete`); listagens: `list_folders`, `listmessages`.

Importante: **criar a pasta não confirma entrega** por lead; o app mantém `campaign_leads.status` em grande parte `pending` até sync / find (comentário explícito em `app.py` após insert).

---

## Criação de campanha no app (`_create_campaign_core`)

Trecho conceitual (ordem lógica):

1. **INSERT** em `campaigns` com `use_uazapi_sender`, `daily_limit`, janela (`send_hour_*`, fins de semana), `scheduled_start`, `rotation_mode`, etc.
2. **Associação** `campaign_instances` (instâncias Uazapi do utilizador).
3. Se **`enable_cadence`:** `UPDATE` com `cadence_config`, `terms_accepted`; **INSERT** em `campaign_steps` (mensagens por etapa, mídia no storage).
4. **`CampaignLead.add_leads`**.
5. Se **`use_uazapi_sender`:** atribui **`send_batch`** aos leads (`per_instance_limit = daily_limit` na prática do loop de batch).
6. **Primeiro lote Uazapi:**
   - Filtra instâncias com `can_create_campaign_today`.
   - **`uazapi_initial_chunk_distribution_limits(daily_limit, n_inst)`** → `(per_instance_limit, total_limit)` com teto **30** por pasta (API).
   - Lê até `total_limit` leads pendentes; **`_chunk(leads, per_instance_limit)`** → um chunk por instância (índice alinhado à ordem das instâncias).
   - Delays iniciais: `default_inter_message_delay_range_minutes()` → convertidos para segundos (sobrescritos depois por `campaigns.delay_min/max_minutes` no worker quando aplicável).
   - Para cada chunk: monta `messages`, chama **`create_advanced_campaign`**, grava **`campaign_stage_sends`** (`stage='initial'`, `uazapi_folder_id`, `lead_ids`, `status='running'`), **`uazapi_instance_sends`**, atualiza `campaigns.uazapi_folder_id` (primeira pasta) e `current_step` dos leads.

**Agendamento na criação:** se existir `scheduled_start`, calcula-se `scheduled_for_param` em **minutos** até o instante (mínimo 1) para a API.

---

## Cadência (`enable_cadence`)

- **Flag:** `campaigns.enable_cadence` (boolean).
- **Config:** `campaigns.cadence_config` (JSON), ex.: `cadence_setup_mode` (`now` | `kanban_later`) para fluxo Uazapi-only.
- **Etapas:** `campaign_steps` (mensagens, `delay_days`, mídia).
- **`worker_cadence.process_cadence`:** seleciona campanhas `running`/`pending`/`completed` com janela de `scheduled_start` respeitada e:
  - **Com cadência completa:** matriz de decisão (labels Chatwoot, etc.), envios de follow-up, monitorização pós-envio.
  - **Sem cadência mas `use_uazapi_sender`:** foco em **`schedule_next_initial_chunk`** (apenas chunks etapa initial; sem rollover legado).

Chunks **subsequentes** do initial: linhas `campaign_stage_sends` com `status='scheduled'`, `uazapi_folder_id IS NULL`, `scheduled_for` preenchido; o worker chama **`_materialize_scheduled_stage_sends`**, que pode chamar **`create_advanced_campaign`** após pré-sync (`sync_campaign_stage_sends_before_new_chunk`).

---

## Ritmo humanizado (pacing)

- **`utils/uazapi_pacing.py`:** buckets ponderados para intervalos entre mensagens **dentro** de uma pasta; `maybe_long_gap_minutes` para pausa longa **entre segmentos** planejados (não cria pasta extra).
- Uso principal hoje: defaults na criação e segmentação em fluxos que montam vários segmentos (ex. materialização com pacing por leads).

---

## Limites e bloqueios

- **`utils/limits.py`:** `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` — estados de linha em `campaign_stage_sends` que **bloqueiam** novo chunk na mesma instância (`scheduled`, `running`, `partial`, `queued`).
- **`utils/campaign_send_policy.py`:** `INITIAL_CHUNK_DAILY_QUOTA_POLICY` (default `g2`), `initial_chunk_daily_quota_allows`, `uazapi_initial_chunk_distribution_limits`.
- **`can_create_campaign_today`**, **`check_initial_chunk_daily_quota_for_campaign`:** gating de criação/agendamento.

---

## Sincronização e recuperação

- **`utils/sync_uazapi.py`:** alinhamento de contagens/pastas com `listfolders` / `listmessages`, deteção de pastas órfãs, `sync_campaign_leads_from_uazapi`, etc.
- **`worker_cadence`:** recuperação de sends `scheduled` stale, telemetria de janela BRT (`next_valid_send_utc_naive`, `is_campaign_send_window`).

---

## Resumo mental

| Conceito | Onde vive |
|----------|-----------|
| Pasta remota (lote) | Uazapi `folder_id` + `campaign_stage_sends.uazapi_folder_id` |
| Um “chunk” inicial | Até `per_instance_limit` leads (≤30), 1 pasta por instância no 1º lote |
| Próximo chunk | `schedule_next_initial_chunk` → INSERT `scheduled` → `_materialize_scheduled_stage_sends` → `create_advanced_campaign` |
| Cadência multi-etapa | `enable_cadence` + `worker_cadence` + `campaign_steps` |
| Delays entre msgs na pasta | `delayMin`/`delayMax` na API; origem numérica em `uazapi_pacing` + overrides em `campaigns` |
