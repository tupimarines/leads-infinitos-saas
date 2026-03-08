# Problem Solving Session: Cards não avançam automaticamente no pipeline de cadência

**Date:** 2026-03-08
**Problem Solver:** Augusto
**Problem Category:** Bug técnico / Sincronização de dados

---

## 🎯 PROBLEM DEFINITION

### Initial Problem Statement

Os cards de leads não avançam automaticamente no Kanban após o período definido (Modo teste com delay de 5 min, rollover às 23:00). O worker_cadence roda mas reporta consistentemente "0 leads elegíveis (precisa status=sent). Inicial: pending=3" (ou pending=2, pending=4).

### Refined Problem Statement

**O que EXATAMENTE está errado:** O rollover Inicial→FU1 exige `campaign_leads.status = 'sent'`, mas os leads permanecem com `status = 'pending'` no banco, mesmo quando a Uazapi já reportou os envios como concluídos (dashboard mostra "Enviados: 4, Pendentes: 0").

**Gap:** A fonte de verdade para stats (API Uazapi list_messages) indica envios concluídos, mas a tabela `campaign_leads` não é atualizada pelo sync, deixando os leads "presos" em pending e bloqueando o rollover.

### Problem Context

- **Campanhas afetadas:** teste225, teste-roul
- **Config:** Modo teste ativo, rollover 23:00, delay 5 min, horário de envio 8h–20h (dias úteis)
- **Evidências:**
  - Dashboard: "Enviados: 4, Pendentes: 0" (stats vêm da Uazapi)
  - Kanban: 3 cards em Inicial sem indicador "Enviado"; 1 em Convertido com "✓ Enviado 08/03, 01:36"
  - Logs: "0 leads elegíveis (precisa status=sent). Inicial: pending=3"
  - Kanban header: "1 Envios concluídos" (uazapi_stats.initial_campaign_finished)

### Success Criteria

- Leads com envio reportado pela Uazapi como Sent devem ter `campaign_leads.status = 'sent'`
- Rollover deve mover leads elegíveis (status=sent, não converted/lost) para FU1
- Cards no Kanban devem refletir o status real (enviado vs agendado)

---

## 🔍 DIAGNOSIS AND ROOT CAUSE ANALYSIS

### Problem Boundaries (Is/Is Not)

| Dimensão | É | Não é |
|----------|---|-------|
| **Onde** | Campanhas Uazapi com cadência (teste225, teste-roul) | Campanhas MegaAPI; campanhas sem cadência |
| **Quando** | Após create_advanced_campaign da mensagem inicial; durante "Off hours" | Durante horário comercial de envio |
| **Quem** | Leads em Inicial (current_step=1) | Leads em FU1, FU2, Convertido, Perdido |
| **O quê** | Sync Uazapi→DB não atualiza campaign_leads; rollover não encontra leads elegíveis | Problema de envio na Uazapi; problema de UI pura |

**Padrão:** A Uazapi reporta envios (list_messages Sent), mas o sync não grava `status='sent'` nos leads correspondentes. O worker depende do DB, não da API.

### Root Cause Analysis (Five Whys)

1. **Por que os cards não avançam?** → Porque o rollover não encontra leads com status=sent.
2. **Por que não há leads com status=sent?** → Porque o sync (list_messages → UPDATE campaign_leads) não está atualizando os registros.
3. **Por que o sync não atualiza?** → Hipóteses: (a) sync não roda no worker, (b) matching de telefone falha, (c) API retorna formato inesperado, (d) folder_id incorreto.
4. **Por que o matching falharia?** → Diferença de formato entre número na API (ex: "5511999999999") e no DB (ex: "11999999999", ou em whatsapp_link).
5. **Causa raiz provável:** O sync executa, mas o **match por telefone** entre `list_messages` e `campaign_leads` falha por diferença de formato/normalização, ou a API retorna estrutura diferente da esperada.

### Contributing Factors

- Stats (dashboard) vêm de `get_uazapi_campaign_counts` (conta mensagens); Kanban e rollover vêm de `campaign_leads` (status por lead).
- Duas fontes de verdade: API Uazapi vs DB. O sync é a ponte; se falha, as fontes divergem.
- Logs não mostram "sync Uazapi → {...}" com updated_sent > 0, sugerindo que o sync retorna 0 atualizações.

### System Dynamics

```
[Uazapi API] list_messages(Sent) → N mensagens
       ↓
[sync_campaign_leads_from_uazapi] extrai phones, UPDATE campaign_leads
       ↓ (match por regexp_replace(phone) IN (p1,p2))
[campaign_leads] status = 'sent'  ← FALHA AQUI (0 rows updated)
       ↓
[process_rollover] SELECT ... WHERE status='sent' → 0 rows
       ↓
"0 leads elegíveis (precisa status=sent)"
```

---

## 📊 ANALYSIS

### Force Field Analysis

**Driving Forces:**
- Código de sync já existe (utils/sync_uazapi.py) com paginação e match por whatsapp_link
- Kanban chama sync ao carregar; worker chama sync antes do rollover
- Stats da Uazapi funcionam (dashboard mostra 4 enviados)

**Restraining Forces:**
- Formato de retorno da API Uazapi pode variar (number, chatid, chatId, sender)
- Telefones no DB podem vir de CSV/upload com formatos diversos
- Falta de logs detalhados no sync dificulta diagnóstico

### Constraint Identification

- **Gargalo:** O UPDATE no sync afeta 0 linhas — o WHERE com regexp_replace + IN (p1,p2) não encontra correspondência.
- **Limite real:** Precisamos garantir que o número extraído da API e o número no DB (phone ou whatsapp_link) normalizem para o mesmo valor.
- **Limite assumido:** "Já adicionamos whatsapp_link" — pode não cobrir todos os casos (ex: phone com espaços, parênteses, hífen).

### Key Insights

1. **Divergência de fontes:** Dashboard (API) ≠ Kanban/rollover (DB). O sync é o elo.
2. **Sync silencioso:** Se updated_sent=0, não há log explícito. O worker só loga quando updated_sent ou updated_failed > 0.
3. **Debug necessário:** Inserir logs no sync com: phones retornados pela API, amostra de phones no DB, e rowcount do UPDATE.

---

## 💡 SOLUTION GENERATION

### Método: Assumption Busting + Debug Instrumentado

**Soluções geradas:**

1. **Adicionar logs de debug no sync** — Logar `sent_phones` (amostra), `failed_phones`, e rowcount após cada UPDATE. Permitir diagnóstico sem alterar lógica.
2. **Expandir normalização de telefone** — Incluir mais formatos: remover parênteses, hífens, espaços; aceitar "55 11 99999-9999" → "5511999999999".
3. **Match por múltiplos campos** — Se phone vazio, tentar extrair de whatsapp_link (wa.me/5511999999999) e usar no UPDATE.
4. **Rota de debug** — GET /api/campaigns/<id>/sync-debug que retorna: phones da API, phones no DB (amostra), e resultado do match simulado.
5. **Fallback: marcar como sent por current_step** — Se sync falhar mas stats mostrarem sent=N, considerar marcar leads em Inicial como sent após timeout. (Arriscado — pode marcar antes do envio real.)
6. **Unificar fonte de verdade** — Rollover consultar API diretamente em vez de DB. (Mudança arquitetural grande.)
7. **Validação na criação da campanha** — Ao criar campanha Uazapi, garantir que phones em campaign_leads estão no formato que a API usa (5511999999999).

### Recomendação

**Solução 1 + 2 + 4:** Instrumentar o sync com logs, ampliar a normalização e criar rota de debug para validar o match. Isso permite confirmar a causa e corrigir sem mudanças estruturais.

---

## 🚀 IMPLEMENTATION PLAN

### Action Steps

1. Adicionar logs no `sync_campaign_leads_from_uazapi`: logar len(sent_phones), amostra de 2–3 phones, e rowcount do UPDATE.
2. Revisar `_extract_phones_from_message` e `_phone_match_params` para cobrir mais formatos.
3. Criar rota GET `/api/campaigns/<id>/sync-debug` (ou parâmetro ?debug=1 no sync) que retorna phones da API vs phones no DB.
4. Executar sync manualmente (botão no Kanban) e verificar logs.
5. Se match falhar, ajustar regex/normalização com base no output do debug.

### Recursos

- Acesso ao código (utils/sync_uazapi.py, app.py)
- Logs do worker_cadence
- Possibilidade de testar com campanha real (teste225)

---

## ✅ IMPLEMENTADO (Debug)

1. **Rota GET `/api/campaigns/<id>/sync-debug`** — Retorna:
   - `api.sent_phones`: telefones extraídos da Uazapi (list_messages Sent)
   - `api.first_message_structure`: estrutura bruta da primeira mensagem (para ver keys)
   - `db_leads_sample`: leads no DB com phone_norm e wa_norm
   - `match.unmatched_from_api`: phones da API que não encontraram match no DB
   - `match.unmatched_from_db`: leads no DB que não batem com nenhum phone da API

2. **Log no worker** — Quando API retorna Sent > 0 mas updated_sent = 0: `⚠️ API retornou N Sent mas 0 atualizados no DB (verificar match de telefone)`

3. **DEBUG_SYNC_UAZAPI=1** — Variável de ambiente para logar sent_phones no sync

### Como usar o debug

1. Acesse `GET /api/campaigns/117/sync-debug` (substitua 117 pelo ID da campanha teste225)
2. Analise `unmatched_from_api` e `unmatched_from_db` — se houver itens, o formato difere
3. Compare `first_message_structure` com os campos que usamos (number, chatid, chatId, sender)
4. Ajuste `_extract_phones_from_message` ou `_phone_match_params` conforme necessário

---

_Generated using BMAD Creative Intelligence Suite - Problem Solving Workflow_
