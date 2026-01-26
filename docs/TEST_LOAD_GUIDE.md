# 📖 Test Load Script - Guia de Uso

## Visão Geral

O script `test_load.py` automatiza a bateria de testes do **Item 4** do D4 Implementation Plan, validando a performance do sistema com múltiplas campanhas e usuários concorrentes.

## 🎯 Testes Implementados

### ✅ Teste 1: Campanha com 50 Leads
**O que valida:**
- ✅ Todos os 50 leads são processados
- ✅ Delays entre envios respeitados (300-600 segundos)
- ✅ Limite diário não é excedido

**Duração estimada:** 4-6 horas (dependendo dos delays configurados no worker_sender)

---

### ✅ Teste 2: 3 Usuários Simultâneos
**O que valida:**
- ✅ Criação simultânea de campanhas por 3 usuários
- ✅ Planos diferentes (Semestral/Anual)
- ✅ Sem deadlocks no banco de dados
- ✅ Processamento independente e justo

**Duração estimada:** 15-30 minutos

---

### ⚠️ Teste 3: Estresse de Daily Limit
**O que valida:**
- ✅ Campanha pausa após atingir limite diário
- ✅ Contador `sent_today` preciso
- ❌ **NÃO valida:** Retomada automática no dia seguinte (requer teste manual)

**Duração estimada:** 2-3 horas

---

## 🚀 Como Executar

### Pré-requisitos

1. **PostgreSQL e Redis rodando:**
   ```powershell
   docker-compose up -d
   ```

2. **Worker Sender ativo:**
   ```powershell
   # Se estiver rodando localmente
   python worker_sender.py
   
   # Ou via Docker
   docker-compose logs -f sender
   ```

3. **Variáveis de ambiente configuradas (`.env`):**
   ```bash
   DB_HOST=localhost
   DB_NAME=leads_infinitos
   DB_USER=postgres
   DB_PASSWORD=devpassword
   DB_PORT=5432
   ```

### Executar Testes

```powershell
cd "c:\Users\augus\AI Agency - Coding Projects\000 - NEURIX\000 - OKR 2026\leads-infinitos-saas"
python test_load.py
```

**Menu interativo:**
```
Escolha qual teste executar:
  1 - Teste 1: Campanha com 50 Leads
  2 - Teste 2: 3 Usuários Simultâneos
  3 - Teste 3: Estresse de Daily Limit
  4 - Executar todos os testes

Digite o número (1-4): 
```

---

## 📊 Exemplo de Output

```
🧪 TESTE 1: Campanha Única com 50 Leads
======================================================================
✅ Usuário criado: test_user_abc123@test.com (daily_limit=30)
✅ 50 leads fake gerados
✅ Campanha criada: ID=42

⏱️  Monitorando progresso (atualização a cada 30s)...
   [30s] Total: 50 | Enviados: 1 | Pendentes: 49 | Falhados: 0 | Status: running
   [60s] Total: 50 | Enviados: 2 | Pendentes: 48 | Falhados: 0 | Status: running
   ...
   [12600s] Total: 50 | Enviados: 50 | Pendentes: 0 | Falhados: 0 | Status: completed

✅ Todos os leads foram processados!
✅ Delays respeitados (49 envios verificados)
✅ Limite diário respeitado: 30/30

======================================================================
✅ TESTE 1 CONCLUÍDO
======================================================================
```

---

## 🛠️ Funções Principais

### Utilitários de Criação

```python
create_test_user(email, license_type='semestral')
# Retorna: {'user_id': int, 'email': str, 'daily_limit': int}

create_fake_leads(count)
# Retorna: [{'phone': '+5511999999999', 'name': 'João Silva'}, ...]

create_campaign(user_id, name, leads, daily_limit)
# Retorna: campaign_id (int)
```

### Monitoramento

```python
get_campaign_stats(campaign_id)
# Retorna: {'total': 50, 'sent': 25, 'pending': 25, 'failed': 0, 'status': 'running'}

check_delays_respected(campaign_id, min_delay=300, max_delay=600)
# Retorna: {'ok': True, 'violations': [], 'checked': 49}
```

### Limpeza

```python
cleanup_test_data([user_id1, user_id2, ...])
# Remove usuários de teste, licenças, campanhas e leads
```

---

## ⚠️ Limitações Conhecidas

### Teste 3: Retomada no Dia Seguinte
O script **NÃO** consegue validar a retomada automática após 24h. Alternativas:

1. **Aguardar 24h reais** (impraticável)
2. **Mockar datetime** no código (invasivo)
3. **Teste manual em produção** (recomendado)

### Worker Sender Precisa Estar Rodando
Os testes monitoram o banco de dados, mas dependem do `worker_sender.py` para processar os envios. Se o worker não estiver ativo, os testes vão timeout.

### Validação de Envio Real WhatsApp
O script valida que o banco foi atualizado, mas **NÃO valida** se a mensagem chegou no WhatsApp. Isso requer teste manual.

---

## 🔍 Troubleshooting

### Erro: "psycopg2.OperationalError: could not connect to server"
**Solução:** Certifique-se que o PostgreSQL está rodando:
```powershell
docker-compose up -d postgres
```

### Teste fica "travado" em "Pendentes: 50"
**Causa:** Worker sender não está processando
**Solução:**
```powershell
# Verificar logs do worker
docker-compose logs -f sender

# Ou rodar localmente
python worker_sender.py
```

### Timeout nos testes
**Causa:** Worker muito lento (delays grandes)
**Solução:** Ajustar variáveis no `.env` ou `worker_sender.py`:
```python
MIN_DELAY = 10  # Reduzir de 300 para 10 (apenas para testes)
MAX_DELAY = 20
```

---

## 📝 Próximos Passos

Após os testes automatizados passarem:

1. ✅ Validar logs do worker_sender: `docker logs leads-infinitos-sender`
2. ✅ Teste manual E2E em produção
3. ✅ Verificar dashboard de métricas (`/dashboard`)
4. ✅ Testar envio real para WhatsApp

---

## 🤝 Contribuindo

Para adicionar novos testes:

1. Criar função `test_X_description()`
2. Seguir padrão: setup → execute → monitor → cleanup
3. Retornar `True` (passou) ou `False` (falhou)
4. Adicionar ao menu em `main()`
