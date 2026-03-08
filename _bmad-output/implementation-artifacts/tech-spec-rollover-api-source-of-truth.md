---
title: 'Rollover: API como Fonte de Verdade (list_messages Sent)'
slug: 'rollover-api-source-of-truth'
created: '2026-03-08'
status: 'Implementation Complete'
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
tech_stack: ['Flask', 'PostgreSQL', 'Uazapi', 'worker_cadence', 'utils/sync_uazapi']
files_to_modify: ['worker_cadence.py', 'utils/sync_uazapi.py']
code_patterns: ['process_rollover', 'list_messages', '_fetch_all_phones_by_status', 'create_advanced_campaign']
test_patterns: []
---

# Tech-Spec: Rollover — API como Fonte de Verdade

**Created:** 2026-03-08

## Overview

### Problem Statement

O rollover atual depende de `campaign_leads.status = 'sent'`, que é atualizado pelo sync (`sync_campaign_leads_from_uazapi`). Quando o sync não consegue fazer match (diferenças de formato de telefone entre API e DB, ou outros motivos), os leads permanecem `pending` e o rollover não avança nenhum card para Follow-up 1, mesmo que a Uazapi tenha enviado todas as mensagens.

O dashboard mostra números corretos porque usa `get_uazapi_campaign_counts()` diretamente da API; o rollover usa o DB, gerando divergência.

### Solution (Party Mode)

1. **API como fonte de verdade**: Usar `list_messages(Sent)` para definir quem recebeu a mensagem inicial, em vez de `campaign_leads.status`.
2. **Match em tempo de rollover**: Cruzar os números retornados pela API com os leads em Inicial (current_step=1), usando normalização única.
3. **Função de normalização compartilhada**: Uma função para normalizar números tanto da API quanto do DB (phone/whatsapp_link).
4. **Criação de campanhas FU**: Montar `messages` da campanha FU com os números dos cards elegíveis (os que deram match).

### Scope

**In Scope:**
- Alterar `process_rollover()` para usar `_fetch_all_phones_by_status(..., "Sent")` como fonte de elegibilidade
- Buscar leads em Inicial (current_step=1, não converted/lost) e fazer match por número normalizado
- Função `normalize_phone_for_match()` em `sync_uazapi` usada por sync e rollover
- Manter sync para atualizar `status`/`sent_at` (Kanban, stats) — rollover não depende mais dele

**Out of Scope:**
- `process_rollover_fu_next` (FU1→FU2, FU2→Despedida) — continua usando `status='sent'`; pode ser evoluído depois
- Campanhas MegaAPI (não afetadas)
- Modo teste / rollover_test_delay_minutes (já implementado no spec anterior)

---

## Technical Design

### 1. Função de normalização compartilhada

**Arquivo:** `utils/sync_uazapi.py`

Criar `normalize_phone_for_match(raw: str) -> set[str]`:
- Extrai apenas dígitos de `raw` (phone, whatsapp_link, ou payload da API); ignora sufixo `@s.whatsapp.net`
- Se `len(clean) < 10`: retorna `set()` (inválido)
- Caso contrário retorna um set com todas as variantes usadas para match:
  - Sempre inclui `clean`
  - Se 10–11 dígitos e não começa com "55": adiciona `"55" + clean`
  - Se 12+ dígitos e começa com "55": adiciona `clean[2:]` (sem DDI)
- Isso garante match bidirecional: API "5511999999999" ↔ DB "11999999999"

Usar essa função em:
- `sync_campaign_leads_from_uazapi`: ao fazer match, normalizar phone/whatsapp_link do lead e verificar se intersecta com `sent_phones`/`failed_phones` (que também serão normalizados)
- Rollover: ao cruzar `sent_phones` (da API) com leads em Inicial

**Compatibilidade:** O sync atual usa `_phone_match_params(ph)` e `_phone_where()` com `IN (p1, p2)`. A nova abordagem deve produzir o mesmo conjunto de matches. A normalização da API já está em `_extract_phones_from_message`; precisamos garantir que os valores em `sent_phones`/`failed_phones` sejam normalizados da mesma forma para comparação com o DB.

### 2. Alteração em `process_rollover()`

**Arquivo:** `worker_cadence.py`

**Fluxo atual:**
1. Sync (se uazapi_folder_id)
2. Verificar `is_initial_campaign_finished`
3. Query leads com `current_step=1 AND status='sent'` → rollover_leads
4. Se 0 leads, log e return
5. Re-query antes de create_advanced_campaign
6. create_advanced_campaign com números dos leads
7. UPDATE current_step=2, cadence_status='snoozed', snooze_until

**Fluxo novo:**
1. Sync (manter — para Kanban/stats)
2. Verificar `is_initial_campaign_finished`
3. Obter `sent_phones = _fetch_all_phones_by_status(..., "Sent")` (já paginado)
4. Buscar leads em Inicial: `current_step=1`, `cadence_status NOT IN ('converted','lost')` (sem filtro de status)
5. Para cada lead, normalizar `phone` e `whatsapp_link` com `normalize_phone_for_match`; lead é elegível se a interseção com `sent_phones` (normalizados) não for vazia
6. Se 0 leads elegíveis, log e return
7. Re-query leads elegíveis (por id) imediatamente antes de `create_advanced_campaign` — excluir qualquer um que foi movido para Convertido/Perdido
8. create_advanced_campaign com números dos leads elegíveis
9. UPDATE current_step=2, cadence_status='snoozed', snooze_until, status='sent' (opcional: manter consistência) para os leads que foram incluídos na campanha FU1

**Detalhe:** `sent_phones` da API já vem como dígitos (de `_extract_phones_from_message`). Precisamos normalizar para o mesmo formato que usamos no DB — ou seja, o set de comparação deve incluir tanto `5511999999999` quanto `11999999999` quando aplicável. A função `normalize_phone_for_match` aplicada ao número da API deve retornar o mesmo set que aplicada ao número do DB, para que o match funcione em ambos os sentidos.

**Estratégia de match:**
- `sent_phones` da API: cada `ph` é uma string de dígitos. Criar `sent_normalized = set()` e para cada `ph` em sent_phones, adicionar `normalize_phone_for_match(ph)` ao set (ou iterar os retornados).
- Para cada lead em Inicial, `lead_canonical = normalize_phone_for_match(lead['phone']) | normalize_phone_for_match(lead['whatsapp_link'])`
- Lead elegível se `lead_canonical & sent_normalized` não vazio

Precisamos que `sent_phones` seja um set de strings normalizadas (todas as variantes). Então:
- `normalize_phone_for_match` retorna set de variantes
- `sent_normalized = set()`; for ph in sent_phones: sent_normalized |= normalize_phone_for_match(ph)
- Para lead: lead_variants = normalize_phone_for_match(phone) | normalize_phone_for_match(whatsapp_link)
- Match: bool(lead_variants & sent_normalized)

### 3. Exportar `_fetch_all_phones_by_status`

A função já existe em `sync_uazapi` mas é privada. O worker precisa importá-la. Opções:
- Renomear para `fetch_all_phones_by_status` (pública) e usar no worker
- Ou manter `_fetch_all_phones_by_status` e importar explicitamente (Python permite)

Recomendação: exportar como `fetch_all_phones_by_status` para uso no worker.

---

## Tasks

| # | Task | Arquivo | Critério de aceite |
|---|------|---------|--------------------|
| 1 | [x] Criar `normalize_phone_for_match(raw) -> set[str]` | sync_uazapi.py | Extrai dígitos; retorna variantes (com/sem 55) para números válidos; usado no sync |
| 2 | [x] Refatorar sync para usar `normalize_phone_for_match` no match | sync_uazapi.py | Match continua funcionando; sync atualiza sent/failed corretamente |
| 3 | [x] Exportar `fetch_all_phones_by_status` (renomear `_fetch`) | sync_uazapi.py | Worker pode importar e chamar |
| 4 | [x] Alterar `process_rollover`: obter sent_phones da API | worker_cadence.py | Chama fetch_all_phones_by_status(..., "Sent") |
| 5 | [x] Alterar `process_rollover`: query leads Inicial sem status=sent | worker_cadence.py | Filtra current_step=1, não converted/lost |
| 6 | [x] Alterar `process_rollover`: match por normalização | worker_cadence.py | Lead elegível se phone/whatsapp_link normalizado intersecta sent_phones |
| 7 | [x] Manter re-query antes de create_advanced_campaign | worker_cadence.py | Exclui leads movidos para Convertido/Perdido |
| 8 | [x] Atualizar status dos leads no rollover | worker_cadence.py | Marcar status='sent' nos que foram para FU1 (consistência) |

---

## Acceptance Criteria

- [ ] Com campanha Uazapi onde todos os envios foram confirmados (list_messages Sent = N), o rollover move N leads de Inicial para FU1 e cria a campanha FU1 com esses N números
- [ ] Leads em Convertido/Perdido não entram na campanha FU1
- [ ] Match funciona com phone em formatos 11999999999, 5511999999999, +55 11 99999-9999
- [ ] Match funciona com whatsapp_link (wa.me/5511999999999, etc.)
- [ ] Sync continua atualizando campaign_leads.status para Kanban/stats
- [ ] Se a API retornar 0 Sent (campanha não iniciada ou falha), rollover não avança nenhum lead

---

## Referências

- `_bmad-output/problem-solution-2026-03-08.md` — diagnóstico e Five Whys
- `tech-spec-rollover-modoteste-convertido-perdido.md` — spec anterior (sync, delay, re-query)
- Party Mode: API as source of truth, match at rollover time
