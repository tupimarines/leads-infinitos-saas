---
title: 'Addendum: Debug Rollover + Fluxo de Follow-up em Minutos'
slug: 'addendum-rollover-debug-fluxo-minutos'
created: '2026-03-07'
extends: 'tech-spec-follow-up-uazapi-kanban-limite-diario'
status: 'ready-for-dev'
---

# Addendum: Debug Rollover + Fluxo de Follow-up em Minutos

**Contexto:** Campanha "teste123" com 3 leads em Inicial, 0 em Follow-up 1/2 — rollover não executou ontem (06/03). Precisamos debugar e configurar fluxo de poucos minutos para validar transição entre etapas.

## Problemas Identificados

| # | Problema | Impacto |
|---|----------|---------|
| 1 | **SQL NULL em cadence_status** | `cadence_status NOT IN ('converted','lost')` exclui NULL (em SQL, NULL NOT IN retorna NULL). Leads com cadence_status=NULL nunca entram no rollover. |
| 2 | **Rollover silencioso** | Quando `get_campaign_instance` retorna MegaAPI ou sem instância Uazapi, `process_rollover` retorna sem log. Impossível debugar. |
| 3 | **Time picker step=60** | `step="60"` no input type="time" permite só horas inteiras. Tech spec prevê 23:34, 9:01 para testes — UI não permite. |
| 4 | **rollover_time=23:00** | Para testar em horário próximo, usuário precisa esperar 23h. Falta opção "sempre rodar" (ex: 00:00) para testes. |

## Tarefas de Implementação

### T1: Corrigir query SQL do rollover (worker_cadence.py)

**Antes:**
```sql
AND (cl.cadence_status IS NULL OR cl.cadence_status IN ('snoozed', 'pending'))
AND cl.cadence_status NOT IN ('converted', 'lost')
```

**Depois:** Usar `COALESCE` ou reescrever para incluir NULL explicitamente:
```sql
AND (cl.cadence_status IS NULL OR cl.cadence_status IN ('snoozed', 'pending'))
AND COALESCE(cl.cadence_status, '') NOT IN ('converted', 'lost')
```

### T2: Adicionar logs de debug no process_rollover

Quando retornar early, logar motivo:
- `rollover_time` ainda não atingido
- Instância não é Uazapi (ou ausente)
- `uazapi_service` não disponível
- Nenhum lead elegível
- Step 2 não configurado
- Nenhum telefone válido
- create_advanced_campaign falhou

### T3: Permitir rollover_time=00:00 para "sempre rodar"

Quando `rollover_time` = "00:00", interpretar como "sempre executar" (para testes). Ajustar condição:
```python
if rollover_str and rollover_str != '00:00':
    if now_minutes < rollover_minutes:
        return
# 00:00 = modo teste: roda em todo ciclo
```

### T4: Time picker com step=1 (templates/campaigns_new.html)

Alterar `step="60"` para `step="1"` no input rollover_time para permitir 23:34, 9:01, etc.

### T5: Documentar checklist de validação

No addendum ou em comentário: para rollover funcionar, campanha precisa:
1. `enable_cadence = TRUE`
2. Instância Uazapi vinculada em campaign_instances (api_provider='uazapi')
3. campaign_steps com step_number=2 configurado
4. Leads com status='sent', current_step=1

## Configuração para Teste Rápido (Fluxo em Minutos)

| Config | Valor | Efeito |
|--------|-------|--------|
| rollover_time | 00:00 | Rollover roda em todo ciclo (~2 min) |
| delay_days (step 2) | 0 | Follow-up 1 agendado para +2 min |
| Horário comercial | 8h-20h | Worker envia apenas nesse horário; rollover roda 24h |

**Passos para testar:**
1. Criar campanha com cadência, rollover_time=00:00, delay_days=0 no Follow-up 1
2. Vincular instância Uazapi
3. Enviar 1ª mensagem para 1-2 leads
4. Aguardar ~2 min: rollover move Inicial → Follow-up 1
5. Dentro do horário comercial: em ~2 min, Follow-up 1 é enviado
6. Verificar Kanban: cards devem transitar Inicial → Follow-up 1 → (após delay) Follow-up 2

## AC Adicionais

- [ ] **AC-D1**: Leads com cadence_status=NULL em Inicial são incluídos no rollover
- [ ] **AC-D2**: Quando rollover não executa por instância MegaAPI, log "⏭️ [Rollover] Campaign X: instância MegaAPI, pulando (requer Uazapi)"
- [ ] **AC-D3**: rollover_time=00:00 faz rollover rodar em todo ciclo (modo teste)
- [ ] **AC-D4**: Time picker permite 9:01, 23:34 (step=1)
