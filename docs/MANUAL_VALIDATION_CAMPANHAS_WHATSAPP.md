# Validação manual — campanhas WhatsApp (outbox + cota diária)

Checklist operacional para validar em **staging/produção** o fluxo de envio inicial via `campaign_message_outbox`, respeitando `daily_limit` e retomada no dia seguinte.

> **Nota:** Este documento não substitui testes automatizados. A validação abaixo deve ser executada por um operador com acesso ao painel admin, Postgres e logs dos workers.

## Pré-requisitos

| Item | Verificação |
|------|-------------|
| `USE_MESSAGE_OUTBOX=1` | Env nos workers (`worker_message_outbox`, `worker_cadence`) |
| Instância Uazapi | Conectada (`status=connected`) e vinculada à campanha |
| Operador | Superadmin (criação outbox na Fase 1) ou fluxo admin para o utilizador alvo |
| Leads de teste | CSV com **≥ 12 números válidos** WhatsApp (para cobrir 2 dias com `daily_limit=5`) |
| Janela BRT | `send_hour_start` / `send_hour_end` cobrindo o horário do teste |

## 1. Criar campanha com `daily_limit=5`

1. Admin → **Nova campanha** (ou recriar utilizador de teste).
2. Marcar **Uazapi** + cadência conforme cenário desejado.
3. Definir **`daily_limit = 5`** (não confundir com limite do plano — política G3 usa só o teto da campanha).
4. Importar CSV de teste; confirmar leads `pending` no passo 1 (Inicial).
5. Iniciar campanha (`status=running`).

**Registar:** `campaign_id`, `user_id`, horário de criação (UTC/BRT).

## 2. Verificar exatamente 5 envios no dia 1

Aguardar o worker processar a fila (ou forçar chunk se aplicável).

### 2.1 Painel / API

- Kanban ou `GET /api/campaigns/<id>/stats`: **5 enviados** no dia, restantes `pending`.
- Admin: `GET /api/admin/campaigns/<id>/outbox-state` — estados coerentes (`sent` ≈ 5, `pending` no restante).

### 2.2 Script de diagnóstico

```bash
python scripts/diagnostico_campanha_uazapi.py <campaign_id> --no-api
```

Na seção **Outbox initial (visão consolidada)** conferir:

- `quota.campaign_cap` = 5
- `quota.sent_campaign_today` = 5 (após conclusão do lote)
- `quota.remaining_slots` = 0
- `quota.allows_more` = false
- `outbox_initial_by_status`: `sent` = 5 (e sem `pending`/`sending` ativos)

### 2.3 SQL (substituir `<CID>`)

```sql
-- Cota / envios iniciais hoje (campanha)
SELECT COUNT(*)::int
FROM campaign_leads cl
JOIN campaigns c ON c.id = cl.campaign_id
WHERE c.id = <CID>
  AND cl.status = 'sent'
  AND cl.current_step = 1
  AND COALESCE(cl.removed_from_funnel, FALSE) = FALSE
  AND (cl.sent_at AT TIME ZONE 'America/Sao_Paulo')::date =
      (NOW() AT TIME ZONE 'America/Sao_Paulo')::date;

-- Outbox initial por status
SELECT status, COUNT(*)::int AS n
FROM campaign_message_outbox
WHERE campaign_id = <CID> AND LOWER(TRIM(stage)) = 'initial'
GROUP BY status
ORDER BY status;

-- Leads elegíveis sem linha outbox (deve ser > 0 se ainda há pending)
SELECT COUNT(*)::int
FROM campaign_leads cl
WHERE cl.campaign_id = <CID>
  AND cl.status = 'pending' AND cl.current_step = 1
  AND COALESCE(cl.removed_from_funnel, FALSE) = FALSE
  AND COALESCE(cl.cadence_status, 'active') NOT IN ('converted', 'lost')
  AND NOT EXISTS (
      SELECT 1 FROM campaign_message_outbox o
      WHERE o.campaign_lead_id = cl.id
        AND LOWER(TRIM(o.stage)) = 'initial'
        AND o.status IN ('pending', 'sending', 'sent')
  );
```

**Critério de sucesso dia 1:** exatamente 5 mensagens enviadas; nenhum 6º envio no mesmo dia BRT; leads restantes permanecem `pending`.

## 3. Pausar e retomar (mesmo dia)

1. No Kanban ou API: `POST /api/campaigns/<id>/toggle_pause` → campanha `paused`.
2. Confirmar que **não** surgem novos `sent` na outbox enquanto pausada (aguardar 1–2 ciclos do worker).
3. Retomar: `POST /api/campaigns/<id>/toggle_pause` novamente → `running`.
4. Como a cota do dia já está esgotada (`allows_more=false`), **não** deve enfileirar mais envios até o dia seguinte.

**Registar:** timestamps de pause/resume; `outbox_schedule_skip` / `daily_quota_exceeded` nos logs (ver secção 5).

## 4. Retomada no dia 2 (cota renovada)

1. Avançar para o **próximo dia BRT** (ou aguardar virada natural).
2. Garantir janela de envio aberta (`send_hour_start`–`send_hour_end`).
3. Com campanha `running`, o worker (`maybe_schedule_outbox_initial_batches`) deve enfileirar até **5** novos leads.
4. Repetir diagnóstico:

```bash
python scripts/diagnostico_campanha_uazapi.py <CID> --no-api
```

**Esperado no dia 2:**

- `quota.sent_campaign_today` reinicia (contagem só do dia BRT corrente).
- `quota.remaining_slots` > 0 até completar o 2º lote de 5.
- Evento `outbox_next_day_deferred` **não** deve bloquear se dentro da janela.

**Critério de sucesso:** mais 5 envios no dia 2; total acumulado 10 se havia 12 leads no CSV.

## 5. Eventos nos logs (grep)

Nos logs de `worker_message_outbox` / `worker_cadence` / `app` (JSON estruturado):

```bash
# Substitua CID e caminho do log
grep '"campaign_id": <CID>' /var/log/worker_message_outbox.log

# Enfileiramento e adiamento
grep 'outbox_schedule_skip' worker.log | grep '<CID>'
grep 'outbox_next_day_deferred' worker.log | grep '<CID>'
grep 'daily_quota_exceeded' worker.log | grep '<CID>'

# Lote enfileirado após dia anterior
grep 'schedule_next_initial_outbox_batch' worker.log | grep '<CID>'
grep 'próximo lote enfileirado' worker.log | grep '<CID>'

# Auditoria por campanha (arquivo local)
grep '.' storage/<user_id>/campaigns/<CID>/dispatch_audit.jsonl | tail -20
```

Eventos úteis:

| Evento | Significado |
|--------|-------------|
| `outbox_schedule_skip` + `daily_quota_exceeded` | Cota do dia esgotada — comportamento esperado após 5 envios |
| `outbox_next_day_deferred` + `outside_send_window` | Fora da janela BRT — retoma no próximo slot válido |
| `outbox_next_day_deferred` + `reason` variado | Próximo lote adiado (não confundir com bug) |
| `legacy_advanced_campaign` | Fluxo legado por pasta — **não** deve aparecer em campanhas 100% outbox |

## 6. Chunks legados (sanity)

Campanhas criadas só com outbox não devem ter chunks `initial` ativos em `campaign_stage_sends`.

```sql
SELECT id, status, planned_count, success_count, failed_count, uazapi_folder_id
FROM campaign_stage_sends
WHERE campaign_id = <CID> AND LOWER(TRIM(stage)) = 'initial'
  AND status IN ('scheduled', 'running', 'partial', 'queued', 'waiting_reconnect');
```

**Esperado:** 0 linhas (ou apenas `failed`/`done` históricos de migração). O diagnóstico mostra `legacy_initial_chunks.in_flight_count` e `legacy_offset_positions`.

## 7. Checklist resumido

| # | Passo | OK? | Notas |
|---|--------|-----|-------|
| 1 | Campanha criada `daily_limit=5`, ≥12 leads | ☐ | `campaign_id`: _____ |
| 2 | Dia 1: exatamente 5 `sent`, cota `allows_more=false` | ☐ | |
| 3 | Pause: sem novos envios | ☐ | |
| 4 | Resume mesmo dia: ainda sem 6º envio | ☐ | |
| 5 | Dia 2: +5 envios, cota renovada | ☐ | |
| 6 | Logs: `daily_quota_exceeded` / `outbox_next_day_deferred` coerentes | ☐ | |
| 7 | Sem chunks legados em voo | ☐ | |
| 8 | `dispatch_audit.jsonl` com 10 entradas de envio | ☐ | |

## 8. Rollback / escalação

Se o comportamento divergir:

1. Gravar saída completa: `python scripts/diagnostico_campanha_uazapi.py <CID> --json-out /tmp/diag-<CID>.json`
2. Anexar trechos de log da secção 5.
3. Consultar `docs/UAZAPI_ROLLOUT_ROLLBACK_GUIDE.md` se suspeita de flag/env.

---

**Última atualização:** validação manual preparada no repositório; execução em produção fica a cargo do operador.
