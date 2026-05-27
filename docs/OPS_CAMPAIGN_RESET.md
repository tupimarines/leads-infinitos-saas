# Reset operacional de campanhas (admin)

## Ordem recomendada (painel admin)

1. **Backup ZIP** — Admin → Campanhas → **Backup pendentes** → Dry-run ON → conferir contagens por usuário/campanha.
2. **Baixar ZIP** — Dry-run OFF → **Baixar ZIP** (um CSV por campanha com leads `pending` no passo 1; campanhas sem pendentes não entram no ZIP).
3. **(Opcional) Purge dry-run** — `GET /api/admin/campaigns/purge-active/dry-run?user_id=<id>` para ver o que seria excluído manualmente.
4. **Excluir campanhas** — No admin, excluir campanhas antigas (não há delete em massa automático nesta fase).
5. **Recriar campanha** — Admin → Nova campanha → importar CSV (colunas `phone`, `name`, `whatsapp_link`, `status=1`).
6. **Disparo Uazapi** — Se cadência: **Forçar chunk (Uazapi)** na campanha nova; confirmar limite diário **30** envios.

## API

| Método | Rota | Notas |
|--------|------|--------|
| GET | `/api/admin/campaigns/export-pending-initial-backup` | `dry_run=1` → JSON; sem dry_run → ZIP |
| GET | `/api/admin/campaigns/purge-active/dry-run` | Só simulação |
| GET | `/api/admin/campaigns/<id>/export-remanent-csv?scope=pending_initial` | Uma campanha |
| GET | `/api/admin/campaigns/<id>/export-restore-snapshot` | JSON: mensagens, cadência, payload para recriar |

## Export pontual (IDs conhecidos)

```bash
python scripts/export_campaign_restore_bundle.py --campaign-ids 202,207,269,270
```

Gera em `backups/campaign_restore_<UTC>/`:
- `campaign_<id>_<nome>_restore_snapshot.json` — mensagens iniciais, steps, instâncias, `create_campaign_payload`
- `campaign_<id>_<nome>_pending_initial-pending-admin.csv` — leads ainda sem 1º envio

Query comum: `user_id`, `statuses=running,pending,paused` (default).

## Script — 4 usuários (10, 13, 41, 40)

Na raiz do projeto, com `.env` e Postgres:

```bash
python scripts/ops_reset_four_users_campaigns.py --dry-run
python scripts/ops_reset_four_users_campaigns.py --execute
```

O script grava em `backups/ops_reset_<UTC>/`:

- `user_<id>_<email>/` — CSVs `*-pending-admin.csv`, snapshot `*_campaign_snapshot.json`, CSV combinado para import.
- Com `--execute`: apaga todas as campanhas do usuário, recria a **última** campanha (mensagens, cadência, instâncias, `daily_limit=30`) e chama chunk inicial Uazapi.

**Atenção:** `--execute` é destrutivo. Rode `--dry-run` antes e guarde a pasta de backup.

## Usuários desta rodada

| ID | Email |
|----|--------|
| 10 | atendimento@gctalentos.com.br |
| 13 | keltenner@hotmail.com |
| 41 | julianodiego@hotmail.com |
| 40 | bonton@bontonvest.com.br |
