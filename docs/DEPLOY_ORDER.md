# Ordem de deploy (migração vs web vs workers)

## Problema que isso resolve

Se o **Gunicorn** roda `init_db()` no mesmo instante em que **cadence/worker** já estão usando o banco, a migração pode **travar no `LOCK TABLE`** (esperando os workers soltarem a tabela) ou gerar **deadlock**.

## Com Docker Compose (recomendado)

O arquivo `docker-compose.yml` inclui o serviço **`migrate`**:

1. **`migrate`** — `python scripts/run_migrate_db.py` — roda **uma vez** e encerra (`restart: "no"`).
2. **`web`**, **`worker`**, **`sender`**, **`cadence`** — só sobem depois que `migrate` termina com sucesso (`depends_on: condition: service_completed_successfully`).

Exige **Docker Compose v2.20+** (suporte a `service_completed_successfully`).

```bash
docker compose up -d
```

Na **primeira subida** após mudança, se ainda existirem containers antigos com workers rodando, pare o stack e suba de novo:

```bash
docker compose down && docker compose up -d
```

## Só Dockerfile / painel (Dokploy, Railway, etc.)

O `Dockerfile` sobe **só o Gunicorn** — **não** roda migração.

Configure **comando de deploy** ou **pre-deploy hook** para executar **antes** de liberar tráfego:

```bash
python scripts/run_migrate_db.py && gunicorn -b 0.0.0.0:8000 ...
```

Ou um job separado que rode `python scripts/run_migrate_db.py` e só então marque o release como saudável.

## Migração manual

```bash
cd /app   # ou raiz do projeto
python scripts/run_migrate_db.py
```

## `lock_timeout`

A sessão de migração usa `lock_timeout = 120s`. Se após 2 minutos ainda não for possível obter lock (ex.: transação zumbi no Postgres), a migração **falha** com erro explícito em vez de ficar parada para sempre.

## Variável opcional `RUN_INIT_DB`

O boot do Flask **não** chama `init_db()` por padrão. Toda migração deve passar por `scripts/run_migrate_db.py` ou pelo serviço `migrate` no Compose.
