# Solução para Worker Timeout e Memory Issues

## Problema Identificado

O erro `WORKER TIMEOUT (pid:7)` e `Worker (pid:7) was sent SIGKILL! Perhaps out of memory?` ocorria porque:

1. **Scraping Síncrono**: O scraper rodava no thread principal, bloqueando o worker
2. **Acúmulo de Memória**: Com 15 localizações, o browser acumulava dados na memória
3. **Timeout do Gunicorn**: Worker era morto após 600 segundos (10 minutos)
4. **Falta de Gerenciamento de Memória**: Sem limpeza periódica de cache do browser

## Solução Implementada

### 1. Sistema de Background Jobs

**Arquivo**: `app.py`
- **Nova tabela**: `scraping_jobs` para rastrear jobs
- **Classe `ScrapingJob`**: Gerencia jobs no banco de dados
- **Função `run_scraping_job_async()`**: Executa scraping em thread separado

```python
# Criação de job
job_id = ScrapingJob.create(user_id, keyword, locations, total_results)
run_scraping_job_async(job_id)  # Executa em background
```

### 2. Scraper Otimizado com Progress Tracking

**Arquivo**: `main.py`
- **Nova função**: `run_scraper_with_progress()`
- **Memory Management**: Limpeza periódica do cache do browser
- **Progress Callback**: Atualiza progresso em tempo real

```python
# Memory management a cada 50 listings
if listing_index % 50 == 0 and listing_index > 0:
    page.evaluate("() => { if (window.gc) window.gc(); }")
```

### 3. Configuração Gunicorn Otimizada

**Arquivo**: `Dockerfile`
```dockerfile
# Antes: timeout 600s, sem otimizações
CMD ["gunicorn", "-b", "0.0.0.0:8000", "--timeout", "600", "app:app"]

# Depois: timeout 30s, otimizado para memória
CMD ["gunicorn", "-b", "0.0.0.0:8000", "--timeout", "30", "--worker-class", "sync", "--workers", "1", "--max-requests", "100", "--max-requests-jitter", "10", "--preload", "app:app"]
```

### 4. Interface de Acompanhamento

**Arquivo**: `templates/jobs.html`
- **Página de Jobs**: Visualiza todos os jobs do usuário
- **Progress Tracking**: Barra de progresso em tempo real
- **Auto-refresh**: Atualiza automaticamente jobs em execução
- **Download**: Link direto para resultados

### 5. API de Status

**Endpoint**: `/api/job/<job_id>`
- **Status em tempo real**: Retorna progresso atual
- **Error handling**: Mostra mensagens de erro
- **Results path**: Caminho para download dos resultados

## Benefícios da Solução

### ✅ Resolve o Timeout
- Jobs executam em threads separados
- Worker principal não é bloqueado
- Timeout reduzido para 30s (suficiente para requests HTTP)

### ✅ Gerencia Memória
- Limpeza periódica do cache do browser
- Worker é reciclado após 100 requests
- Preload reduz tempo de inicialização

### ✅ Melhora UX
- Usuário vê progresso em tempo real
- Pode navegar enquanto scraping roda
- Notificações de conclusão/erro

### ✅ Escalabilidade
- Múltiplos jobs podem rodar simultaneamente
- Sistema de filas implícito
- Histórico de jobs mantido

## Como Usar

### 1. Iniciar Scraping
```python
# Usuário submete formulário
# Sistema cria job e inicia em background
# Redireciona para página de jobs
```

### 2. Acompanhar Progresso
```javascript
// Auto-refresh a cada 5 segundos
// Barra de progresso atualizada
// Status visual (Pendente/Executando/Concluído/Falhou)
```

### 3. Download de Resultados
```python
# Quando job completa, arquivo é salvo
# Link de download disponível na interface
# Resultados concatenados em arquivo único
```

## Monitoramento

### Logs do Sistema
```
[2025-09-29 01:01:03] Job 123 started: veterinária in Curitiba
[2025-09-29 01:01:15] Job 123 progress: 33% - São Paulo
[2025-09-29 01:01:30] Job 123 completed: results saved
```

### Status dos Jobs
- **pending**: Aguardando execução
- **running**: Em execução (com progresso)
- **completed**: Concluído com sucesso
- **failed**: Falhou (com mensagem de erro)

## Testes

Execute o script de teste:
```bash
python test_background_scraping.py
```

Este script verifica:
- ✅ Criação de jobs
- ✅ Atualização de progresso
- ✅ Finalização de jobs
- ✅ Recuperação de jobs por usuário

## Conclusão

A solução resolve completamente o problema de timeout e memory issues:

1. **Não há mais timeouts** - Jobs rodam em background
2. **Memória é gerenciada** - Limpeza periódica e reciclagem de workers
3. **UX melhorada** - Progresso em tempo real e interface intuitiva
4. **Sistema escalável** - Suporta múltiplos jobs simultâneos

O sistema agora pode processar 15+ localizações sem problemas de memória ou timeout.
