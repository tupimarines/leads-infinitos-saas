---
title: 'Logs identificáveis no container sender para envios'
slug: 'logs-sender-envios-identificaveis'
created: '2026-03-06'
status: 'implementation-complete'
tech_stack: ['Python 3.x', 'worker_sender.py']
files_to_modify: ['worker_sender.py']
code_patterns: ['print() para logs', 'send_message() retorna success/log']
test_patterns: ['teste manual: criar campanha running, verificar logs']
---

# Tech-Spec: Logs identificáveis no container sender para envios

**Created:** 2026-03-06

## Overview

### Problem Statement

O usuário não consegue identificar nos logs do container `leads_infinitos_sender` quando mensagens são efetivamente enviadas. Os prints atuais misturam-se com outras mensagens (horário comercial, cooldown, etc.) e não têm um prefixo ou formato que permita filtrar rapidamente os eventos de envio.

### Solution

Adicionar linhas de log com prefixo consistente `[ENVIO]` e formato estruturado (campaign_id, lead_id, phone, status) em todos os pontos de envio. Garantir `flush=True` para saída imediata no stdout. Opcionalmente reduzir verbosidade dos logs de debug da MegaAPI para não poluir.

### Scope

**In Scope:**
1. Prefixo `[ENVIO]` em todas as linhas relacionadas a envio de mensagem
2. Log estruturado antes do envio: `[ENVIO] INICIANDO campaign_id=X lead_id=Y phone=Z`
3. Log estruturado após envio: `[ENVIO] OK campaign_id=X lead_id=Y` ou `[ENVIO] FALHA campaign_id=X lead_id=Y motivo=...`
4. `flush=True` em todos os prints de envio para garantir visibilidade imediata no Docker
5. Manter logs existentes de erro/aviso; apenas padronizar os de envio

**Out of Scope:**
- Migrar para módulo `logging` (manter print por simplicidade)
- Sistema de log centralizado (Filebeat, Loki, etc.)
- Alterar outros containers (web, cadence, worker)

## Context for Development

### Codebase Patterns

- **worker_sender.py**: usa `print()` para logs; `send_message()` retorna `(success: bool, log)`
- **Fluxo de envio**: `process_campaigns()` → loop campanhas → loop leads → `send_message()` → UPDATE campaign_leads
- **Pontos de log atuais**: linha ~956 `print(f"Sending to {phone_jid}...")`; dentro de `send_message()` linhas 566-590 (MegaAPI) e 542/547 (Uazapi)
- **Docker**: sender usa `build: .`; Dockerfile já tem `PYTHONUNBUFFERED=1`; stdout vai para stdout do container

### Files to Reference

| File | Purpose |
| ---- | ------- |
| worker_sender.py | send_message(), process_campaigns(), loop de envio |
| docker-compose.yml | serviço sender, command: python worker_sender.py |

### Technical Decisions

1. **Prefixo `[ENVIO]`**: permite `grep "[ENVIO]"` ou filtro na UI de logs para ver apenas envios
2. **Formato**: `[ENVIO] INICIANDO|OK|FALHA campaign_id=X lead_id=Y phone=Z` — campos fixos para parsing
3. **flush=True**: garantir que cada print seja escrito imediatamente (Python pode bufferizar stdout em alguns cenários)
4. **MegaAPI verbose**: os prints "=== SENDING MESSAGE ===", "Payload", "Response Body" são debug. Opcional: reduzir ou mover para nível "verbose" (env var). Por ora manter, mas adicionar [ENVIO] nas linhas principais

## Implementation Plan

### Tasks

- [x] **Task 1**: Adicionar constante e helper de log
  - File: `worker_sender.py`
  - Action: No topo (após imports), adicionar `LOG_PREFIX = "[ENVIO]"` e função `def log_envio(msg, flush=True): print(f"{LOG_PREFIX} {msg}", flush=flush)` para padronizar

- [x] **Task 2**: Log estruturado antes do envio (process_campaigns)
  - File: `worker_sender.py`
  - Action: Substituir `print(f"Sending to {phone_jid} (User {user_id})...")` por `log_envio(f"INICIANDO campaign_id={campaign['id']} lead_id={lead['id']} phone={phone_jid} user_id={user_id}")`

- [x] **Task 3**: Log estruturado após envio (process_campaigns)
  - File: `worker_sender.py`
  - Action: Após `success, log = send_message(...)`, adicionar `log_envio(f"OK campaign_id={campaign['id']} lead_id={lead['id']}")` se success, ou `log_envio(f"FALHA campaign_id={campaign['id']} lead_id={lead['id']} motivo={str(log)[:80]}")` se falha

- [x] **Task 4**: Logs dentro de send_message (MegaAPI)
  - File: `worker_sender.py`
  - Action: Na função send_message, para MegaAPI: substituir prints de "=== SENDING MESSAGE ===" por uma única linha `log_envio(f"MegaAPI POST To={phone_jid}")` antes do request; substituir "✅ Message sent successfully!" por `log_envio("MegaAPI OK")`; manter prints de erro com prefixo [ENVIO] ou log_envio

- [x] **Task 5**: Logs dentro de send_message (Uazapi)
  - File: `worker_sender.py`
  - Action: Substituir `print(f"✅ [Uazapi] Message sent successfully!")` por `log_envio("Uazapi OK")`; substituir `print(f"❌ [Uazapi] Exception...")` por `log_envio(f"Uazapi FALHA {error_msg}")`

- [x] **Task 6**: Garantir flush em logs críticos
  - File: `worker_sender.py`
  - Action: A função log_envio já usa flush=True. Verificar que todos os pontos de envio usam log_envio (não print direto)

- [ ] **Task 7** (opcional): Reduzir verbosidade MegaAPI
  - File: `worker_sender.py`
  - Action: Remover ou condicionar a `if os.environ.get('DEBUG_SENDER'):` os prints de "URL", "Payload", "Response Body" para não poluir logs em produção

### Acceptance Criteria

- [ ] AC 1: Given campanha running com leads pendentes, when worker envia mensagem, then log contém `[ENVIO] INICIANDO campaign_id=... lead_id=... phone=...`
- [ ] AC 2: Given envio bem-sucedido, when mensagem enviada, then log contém `[ENVIO] OK campaign_id=... lead_id=...`
- [ ] AC 3: Given envio com falha, when send_message retorna False, then log contém `[ENVIO] FALHA campaign_id=... lead_id=... motivo=...`
- [ ] AC 4: Given usuário filtra logs por `[ENVIO]`, when visualiza logs do container sender, then vê apenas linhas de envio (iniciando, ok, falha)
- [ ] AC 5: Given envio em andamento, when log é escrito, then aparece imediatamente no stdout (flush)

## Additional Context

### Dependencies

- Nenhuma dependência externa nova
- Dockerfile já tem PYTHONUNBUFFERED=1

### Testing Strategy

1. Criar campanha com status "running" (não pausada)
2. Garantir que há leads pendentes e que está dentro da janela de horário
3. Selecionar container `leads_infinitos_sender` na UI de logs
4. Verificar que linhas `[ENVIO]` aparecem quando mensagens são enviadas
5. Testar grep: `docker logs leads_infinitos_sender 2>&1 | grep "\[ENVIO\]"`

### Notes

- **Campanha pausada**: Se a campanha estiver "Pausada", o worker não processa e nenhum envio ocorre — portanto nenhum log [ENVIO] aparecerá. O usuário deve despausar para testar.
- **Complexidade técnica: Baixa** — alterações apenas em worker_sender.py, sem mudanças de schema ou integração.
