# 🏗️ D1 Architecture Migration (21/01/2026)

Este documento detalha as mudanças arquiteturais realizadas no **Dia 1** para transformar o script local em um SaaS escalável, alinhado com o [SAAS_IMPLEMENTATION_CHECKLIST.md](./SAAS_IMPLEMENTATION_CHECKLIST.md) original.

## 1. Mudança de Arquitetura: Filas vs Threads

### ❌ Antes (Problemático)
- O Scraper rodava em `threading.Thread` dentro do processo do Flask.
- **Risco:** Com 50 usuários, o servidor ficaria sem RAM e o Flask poderia travar.

### ✅ Agora (Implementado)
- **Redis + RQ (Redis Queue):** Implementamos uma fila de tarefas assíncrona.
- **Worker Dedicado (`worker_scraper.py`):** Processo separado que consome a fila.

## 2. Mudança de Banco de Dados: SQLite -> PostgreSQL

### ❌ Antes (SQLite)
- Arquivo local `app.db`.
- **Problema Crítico:** Não suporta concorrência de escrita. Se o Web Server e o Worker tentassem escrever ao mesmo tempo (ex: status do job), um deles daria erro "Database Locked".

### ✅ Agora (PostgreSQL)
- **Container Docker:** Adicionado serviço `db` (Postgres 15) no `docker-compose.yml`.
- **Drivers:** Migramos de `sqlite3` para `psycopg2-binary`.
- **Refatoração Completa do Código:**
    - Substituição de `?` por `%s`.
    - Uso de `RETURNING id` para INSERTs.
    - Sessões transacionais robustas.

## 3. Banco de Dados (Schema Updates)

As tabelas foram adaptadas para PostgreSQL (`SERIAL`, `TIMESTAMP`, etc):

### 🆕 `instances` (Conexão WhatsApp)
Gerencia as sessões da Evolution API.

### 🆕 `campaigns` (Motor de Disparo)
Substitui a lógica que ficaria no N8n.

### 🆕 `campaign_leads` (Fila de Envio)
Fila individual de contatos para envio.

## 4. Status do Checklist Original

| Item | Status | Detalhes |
| :--- | :---: | :--- |
| **MVP 1 (Auth)** | ✅ Concluído | Login/Register/Logout migrado para Postgres. |
| **MVP 2 (Fila)** | ✅ Concluído | Redis configurado. Scraper usando Postgres. |
| **Database** | ✅ Migrado | **SQLite abandonado. PostgreSQL ativo.** |
| **Engine Disparo** | 🆕 Novo | `worker_sender.py` já preparado para Postgres. |

## 5. Como Rodar a Nova Arquitetura

### 1. Infraestrutura
```bash
docker-compose up -d
# Isso sobe Redis e PostgreSQL
```

### 2. Workers
```bash
# Scraper (Extração do Google Maps)
python worker_scraper.py

# Sender (Disparo WhatsApp)
python worker_sender.py
```

## 6. Próximos Passos (Dia 2)
1.  **Conexão WhatsApp:** Criar tela para gerar QR Code.
2.  **Sender Engine:** Testar o `worker_sender.py` com uma instância real.
