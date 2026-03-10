# Problem Solving Session: Validar Lista — WORKER TIMEOUT e 504 Uazapi

**Date:** 2026-03-09
**Problem Solver:** Augusto
**Problem Category:** Technical / Infraestrutura + Integração API

---

## 🎯 PROBLEM DEFINITION

### Initial Problem Statement

O botão "Validar lista" no Kanban (endpoint POST `/api/campaigns/<id>/validate-leads`) retorna "Erro ao validar lista" ao usuário. Os logs do container web mostram:

1. **504 Gateway Timeout** da API Uazapi (`POST /chat/check`)
2. **WORKER TIMEOUT (pid:14)** — Gunicorn mata o worker
3. **Traceback:** O worker é abortado durante `time.sleep(backoff)` em `_check_phone_with_retry`

### Refined Problem Statement

**O quê exatamente está errado:** O endpoint `validate-leads` executa de forma síncrona dentro do worker Gunicorn. A validação chama a API Uazapi `/chat/check` em batches de 50, com timeout de 90s por request e retry com backoff. O Gunicorn está configurado com `--timeout 30`, então o worker é morto (SIGABRT) quando a requisição total excede 30 segundos — o que ocorre facilmente quando a Uazapi retorna 504 (timeout deles) e o código entra em retry/sleep.

**Gap entre estado atual e desejado:**
- **Atual:** validate-leads falha com "Erro ao validar lista"; worker morto; usuário sem feedback útil
- **Desejado:** validate-leads completa a validação (ou retorna resultado parcial com batches_skipped) mesmo quando a Uazapi está lenta ou retorna 504

**Por que vale resolver:** A validação prévia é crítica para o fluxo de campanhas — evita enviar para números sem WhatsApp. Sem ela, leads inválidos infectam o envio e geram falhas silenciosas.

### Problem Context

- **Tech spec:** `tech-spec-validacao-chat-check-reorganizacao-envio-campanhas.md` — define batch 50, timeout 90s, retry 2x com backoff 1s
- **API Uazapi:** POST `/chat/check` aceita array de números; retorna 504 quando o gateway deles dá timeout
- **Infra:** `docker-compose.yml` — `gunicorn --timeout 30`
- **Fluxo:** `_run_validate_leads` → `_check_phone_with_retry` → `uazapi.check_phone`; em falha, retry com `time.sleep(1)`; múltiplos batches com `time.sleep(0.5)` entre eles

### Success Criteria

- [ ] validate-leads completa sem WORKER TIMEOUT para listas de até ~150 leads (3 batches)
- [ ] Quando Uazapi retorna 504, o endpoint retorna resultado parcial (batches_skipped) em vez de erro genérico
- [ ] Usuário recebe feedback claro (válidos, inválidos, batches pulados)

---

## 🔍 DIAGNOSIS AND ROOT CAUSE ANALYSIS

### Problem Boundaries (Is/Is Not)

**Onde o problema OCORRE:**
- Endpoint síncrono `/api/campaigns/<id>/validate-leads` executado no worker Gunicorn
- Quando a API Uazapi está lenta ou retorna 504
- Campanhas com `use_uazapi_sender=true` e leads pendentes

**Onde o problema NÃO ocorre:**
- Outros endpoints que retornam rápido
- Validação em ambiente de dev com Uazapi respondendo rápido
- Campanhas MegaAPI (não usam validate-leads Uazapi)

**Quando OCORRE:**
- Uazapi retorna 504 (Request timeout) — provavelmente timeout do gateway deles (~30–60s)
- Após o primeiro request falhar, o retry aciona `time.sleep(1)`; nesse momento o worker já está próximo ou além do timeout de 30s do Gunicorn

**O que É o problema:**
- **Causa raiz:** Gunicorn `--timeout 30` é insuficiente para validate-leads, que pode levar 90s+ por batch (request + retries + sleep)
- **Causa contribuinte:** Uazapi retorna 504; o código trata como "Timeout ou resposta vazia" e retenta, mas o worker é morto antes de concluir

**O que NÃO é o problema:**
- Bug na lógica de retry em si
- Bug no payload para `/chat/check` (a API é chamada corretamente)
- Problema de autenticação (504 é timeout, não 401/403)

### Root Cause Analysis (Five Whys)

1. **Por que o usuário vê "Erro ao validar lista"?**  
   → O worker Gunicorn é morto (WORKER TIMEOUT) e a exceção não é tratada graciosamente.

2. **Por que o worker é morto?**  
   → Gunicorn envia SIGABRT quando a requisição excede `--timeout 30` segundos.

3. **Por que a requisição excede 30s?**  
   → O request à Uazapi pode levar até 90s (timeout do requests); se a Uazapi retorna 504, o código faz retry com `time.sleep(1)`; um único batch com 1 retry já pode ultrapassar 30s.

4. **Por que o timeout do Gunicorn é 30s?**  
   → Configuração padrão/legada no `docker-compose.yml`; não foi ajustada para endpoints longos como validate-leads.

5. **Por que validate-leads precisa de mais tempo?**  
   → A tech spec define timeout 90s por request e múltiplos batches; é uma operação intrinsecamente longa (chamada externa + retries).

**Causa raiz:** Incompatibilidade entre o tempo necessário para validate-leads (90s+ por batch) e o timeout do worker Gunicorn (30s).

### Contributing Factors

- Uazapi pode ser lenta ou instável (504)
- Validação é síncrona — bloqueia o worker durante toda a operação
- Retry com sleep aumenta o tempo total
- Não há tratamento de 504 como retry (apenas 429); 504 faz o código retornar None e tentar retry, mas o worker morre antes

### System Dynamics

```
[Usuário clica Validar] → [Worker recebe request] → [check_phone batch 1]
                                                          ↓
                                              [Uazapi lenta/504 após ~30s]
                                                          ↓
                                              [Retry: time.sleep(1)]
                                                          ↓
                                              [Gunicorn: 30s excedido → SIGABRT]
                                                          ↓
                                              [Worker morto, resposta 500/erro]
```

---

## 💡 SOLUTION GENERATION

### Recommended Solution

**Abordagem em duas camadas:**

1. **Correção imediata (infra):** Aumentar o timeout do Gunicorn para **180 segundos** no `docker-compose.yml`, permitindo que validate-leads complete mesmo com retries e múltiplos batches.

2. **Melhoria de resiliência (código):** Tratar 504 explicitamente em `_check_phone_with_retry` — retry com backoff (como timeout), e garantir que `uazapi.check_phone` propague HTTPError em vez de engolir e retornar None (para que possamos distinguir 504 de outros erros). Alternativamente: manter retorno None mas adicionar retry para 504 no fluxo.

3. **Melhoria futura (opcional):** Mover validate-leads para task assíncrona (Celery/Redis) com polling ou webhook — elimina dependência do timeout do worker.

### Rationale

- **180s:** Cobre 2 batches com 2 retries cada (90+1+90+1+90 ≈ 272s no pior caso) — 180s é um compromisso; para listas muito grandes, a task assíncrona seria ideal.
- **Tratar 504:** A Uazapi retorna 504 quando o gateway dá timeout; é um erro retryable, similar a timeout de rede.
- **Propagar HTTPError:** Hoje `uazapi.check_phone` captura tudo e retorna None; perdemos a informação do status code. Podemos fazer `raise_for_status` antes do return e deixar o chamador tratar, ou retornar uma estrutura `(result, status_code, error)`.

---

## 🚀 IMPLEMENTATION PLAN

### Action Steps

1. **Aumentar timeout Gunicorn** em `docker-compose.yml`: `--timeout 180`
2. **Ajustar `uazapi.check_phone`** para não engolir HTTPError 504 — re-raise ou retornar info de status
3. **Ajustar `_check_phone_with_retry`** para tratar 504 como retryable (como timeout)
4. **Retornar resultado parcial** em `_run_validate_leads` quando batches forem pulados: `{valid, invalid, batches_skipped, partial: true}`

### Validation Plan

- Testar validate-leads com campanha de 100+ leads
- Simular 504 (mock ou Uazapi lenta) e verificar que retry ocorre e worker não morre
- Verificar que a UI exibe resultado parcial quando houver batches_skipped

---

_Generated using BMAD Creative Intelligence Suite - Problem Solving Workflow_
