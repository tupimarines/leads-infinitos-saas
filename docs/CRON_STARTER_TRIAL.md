# Cron: Expiração de licenças Starter Trial

O plano **Starter Trial** tem validade de 7 dias a partir da data em que o admin aplica o plano ao email do usuário. No 8º dia, as funções param de funcionar (via `expires_at > NOW()`) e a instância WhatsApp deve ser deletada na Uazapi.

## Execução diária

### Opção 1: Script Python (cron no servidor)

```bash
# Rodar manualmente
python scripts/expire_starter_trial.py

# Cron diário às 2h da manhã (exemplo)
0 2 * * * cd /caminho/do/projeto && python scripts/expire_starter_trial.py
```

### Opção 2: Endpoint HTTP (cron externo)

Configure a variável de ambiente:

```env
CRON_SECRET=seu-token-secreto-aqui
```

Chame o endpoint diariamente (ex: via cron-job.org, GitHub Actions, ou cron no servidor):

```bash
curl -s "https://seu-dominio.com/cron/expire-starter-trial?token=seu-token-secreto-aqui"
```

Resposta esperada: `{"ok": true, "processed": 0}`

## O que o job faz

1. Busca licenças `starter_trial` com `expires_at <= NOW()` e `status='active'`
2. Para cada licença:
   - Deleta cada instância Uazapi via `DELETE /instance` (header `token`)
   - Remove as instâncias do banco local
   - Atualiza a licença para `status='expired'`

## Variáveis necessárias

- `DB_*` (conexão PostgreSQL)
- `UAZAPI_URL`, `UAZAPI_ADMIN_TOKEN` (para delete na Uazapi)
- `CRON_SECRET` (apenas para a opção 2)
