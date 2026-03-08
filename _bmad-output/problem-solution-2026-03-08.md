# Problem Solving Session: Match de telefone no Rollover/Sync Uazapi

**Date:** 2026-03-08
**Problem Solver:** Augusto
**Problem Category:** Technical / Integração API

---

## 🎯 PROBLEM DEFINITION

### Initial Problem Statement

O worker de cadência reporta dois logs consecutivos:
1. **Sync:** `API retornou 1 Sent mas 0 atualizados no DB (verificar match de telefone)`
2. **Rollover:** `API retornou 1 Sent mas 0 leads em Inicial deram match`

A suspeita é que **o parse pode estar dando problemas** — ou seja, a extração do número de telefone da resposta da API Uazapi (`list_messages`) pode estar falhando ou retornando formato incompatível com o que está no banco.

### Refined Problem Statement

**O quê exatamente está errado:** O match entre os telefones retornados pela API Uazapi (`list_messages` status Sent) e os leads em `campaign_leads` está falhando. A API indica 1 envio confirmado, mas nenhum lead é atualizado no sync nem considerado elegível no rollover.

**Gap entre estado atual e desejado:**
- **Atual:** API diz 1 Sent → 0 leads atualizados no DB, 0 leads em Inicial com match
- **Desejado:** API diz 1 Sent → 1 lead atualizado (sync) e, se estiver em Inicial, elegível para rollover

**Por que vale resolver:** O rollover usa a API como fonte de verdade. Se o match falha, leads que receberam a mensagem não avançam para Follow-up 1, gerando divergência entre dashboard (que usa API) e o fluxo de cadência.

### Problem Context

- **Campanha:** teste9999 (4 leads, cadência ativa)
- **Kanban:** João Silva (554137984966) em **Convertido** com status ✔ Enviado; Ana Costa, Maria Santos, Pedro Oliveira em **Inicial**
- **Fluxo:** Sync roda antes do rollover; ambos usam `normalize_phone_for_match` e `_extract_phones_from_message` para cruzar API ↔ DB
- **Hipótese do usuário:** O parse (extração do número da estrutura da API) pode estar incorreto — campo errado, estrutura aninhada diferente, ou formato retornado pela Uazapi não previsto

### Success Criteria

- [ ] Sync atualiza corretamente `campaign_leads.status` quando a API retorna Sent para um lead existente
- [ ] Rollover identifica leads em Inicial cujo telefone consta em `list_messages(Sent)`
- [ ] Match funciona independente do formato em que a API retorna o número (ex.: `number`, `chatid`, `chatId`, `sender`, com ou sem `@s.whatsapp.net`)

---

## 🔍 DIAGNOSIS AND ROOT CAUSE ANALYSIS

### Problem Boundaries (Is/Is Not)

**Onde o problema OCORRE:**
- Campanhas com `use_uazapi_sender=true` e `uazapi_folder_id`
- Fluxo sync (atualização de status) e rollover (elegibilidade para FU1)
- Match entre telefones da API `list_messages(Sent)` e `campaign_leads`

**Onde o problema NÃO ocorre:**
- Campanhas MegaAPI (não usam list_messages)
- Dashboard/stats (usa API diretamente, não depende do match)
- Campanhas Uazapi sem cadência (rollover não aplicável)

**Quando OCORRE:**
- Ao rodar sync antes do rollover
- Quando a API retorna Sent > 0 (há envios confirmados)
- Em todo ciclo do worker_cadence para campanhas elegíveis

**Quando NÃO ocorre:**
- Se API retorna 0 Sent (nada para fazer match)
- Se campanha não tem uazapi_folder_id ou instância Uazapi

**Quem É afetado:**
- João Silva: sempre o primeiro enviado na planilha; o 1 Sent da API confirma que é ele
- Ana, Maria, Pedro: em Inicial; **não atualizam por razões desconhecidas** quando eventualmente forem enviados

**Quem NÃO é afetado:**
- Leads em campanhas MegaAPI
- Stats/dashboard (fonte é a API)

**O que É o problema:**
- Falha no match telefone API ↔ DB
- Possíveis causas: (a) parse extrai campo errado ou formato diferente, (b) normalização não cobre o formato retornado, (c) estrutura da resposta da API diferente do esperado

**O que NÃO é o problema:**
- API não retorna dados (retorna 1 Sent)
- list_messages funciona (conta está correta)
- normalize_phone_for_match em si (lógica testada com formatos comuns)

### Root Cause Analysis

**Método: Five Whys**

1. **Por que o sync retorna 0 atualizados?**  
   → Porque nenhum lead no DB deu match com os telefones retornados pela API.

2. **Por que não há match?**  
   → Porque o telefone extraído da API (via `_extract_phones_from_message`) não coincide com o telefone armazenado no DB, mesmo após normalização.

3. **Por que não coincide?**  
   → Porque (a) a API retorna o número em campo ou formato diferente do esperado, ou (b) o valor extraído está incorreto/nulo.

4. **Por que o valor extraído está incorreto?**  
   → Porque `_extract_phones_from_message` usa `number`, `chatid`, `chatId`, `sender` — a Uazapi pode usar outro campo (ex.: `recipient`, `to`, `jid`) ou estrutura aninhada para mensagens de campanha.

5. **Por que isso é a causa raiz?**  
   → Porque sem ver a estrutura real da resposta de `list_messages` para campanhas, estamos assumindo campos que podem não existir ou ter nomes diferentes.

**Causa raiz provável:** O parse (`_extract_phones_from_message`) não considera todos os campos possíveis da resposta da Uazapi para mensagens de campanha, ou a estrutura retornada difere do schema genérico de Message.

### Contributing Factors

- Falta de log da estrutura bruta da primeira mensagem em ambiente de debug
- Schema OpenAPI da Uazapi (Message) descreve chat genérico; mensagens de campanha podem ter estrutura própria
- Rota `/api/campaigns/<id>/sync-debug` existe mas usa `_fetch_all_phones_by_status` (renomeado para `fetch_all_phones_by_status`) — import quebrado pode impedir diagnóstico

### System Dynamics

- **Feedback loop:** Sync falha → status não atualiza → rollover não avança → leads ficam em Inicial
- **Leverage point:** Corrigir o parse para extrair o número corretamente da estrutura real da API

---

## 📊 ANALYSIS

### Force Field Analysis

**Driving Forces (Supporting Solution):**
- Rota sync-debug já existe — basta corrigir import e usar para inspecionar estrutura real da API
- Código de parse é centralizado em `_extract_phones_from_message` — alteração em um ponto beneficia sync e rollover
- OpenAPI da Uazapi disponível no projeto — referência para campos possíveis
- Solução é localizada (utils/sync_uazapi.py) — baixo risco de regressão

**Restraining Forces (Blocking Solution):**
- Estrutura real da API não documentada para `list_messages` de campanhas — schema Message pode não refletir o payload
- Sem acesso direto à API em tempo real — diagnóstico depende de log ou sync-debug em ambiente com dados

### Constraint Identification

- **Gargalo:** Parse depende de conhecer os campos exatos retornados pela Uazapi para mensagens de campanha
- **Limite real:** Documentação da API pode estar incompleta ou desatualizada
- **Limite assumido:** Que `number`, `chatid`, `sender` cobrem todos os casos — a verificar

### Key Insights

- **Leverage point:** Corrigir import do sync-debug e chamar a rota com a campanha teste9999 para obter `first_message_structure` — isso revela os campos reais
- **Solução defensiva:** Expandir `_extract_phones_from_message` para tentar mais campos (`recipient`, `to`, `jid`, `chatId`, etc.) e estruturas aninhadas
- **Validação:** Após correção, sync-debug deve mostrar `matched_count > 0` quando API retornar Sent para leads existentes

---

## 💡 SOLUTION GENERATION

### Methods Used

- **Assumption Busting:** Desafiar a premissa de que `number`/`chatid`/`sender` cobrem todos os casos
- **SCAMPER (Substitute, Combine, Adapt):** Adaptar o parse para múltiplos formatos; combinar extração com fallbacks

### Generated Solutions

1. **Expandir campos no parse** — Adicionar `recipient`, `to`, `jid`, `chatId` (camelCase), `wa_id`, `phoneNumber` à ordem de tentativa em `_extract_phones_from_message`
2. **Parse recursivo em objetos** — Se o valor for dict, buscar recursivamente por chaves conhecidas (number, chatid, etc.)
3. **Corrigir sync-debug** — Atualizar import para `fetch_all_phones_by_status`; usuário chama a rota e inspeciona `first_message_structure` para descobrir o campo real
4. **Log da estrutura bruta** — Com `DEBUG_SYNC_UAZAPI=1`, logar a primeira mensagem retornada pela API para diagnóstico
5. **Fallback por posição** — Se mensagem for lista/array, tentar índice 0 como número (para estruturas não-padrão)
6. **Normalização mais agressiva** — Extrair qualquer sequência de 10+ dígitos do JSON serializado da mensagem como último recurso
7. **Webhook Uazapi** — Se a Uazapi enviar webhooks de confirmação de envio, usar como fonte alternativa (fora do escopo imediato)
8. **Teste unitário com mock** — Criar teste que simula resposta da API com estrutura real (após descobrir via sync-debug)
9. **Documentar estrutura** — Após descobrir, documentar em `_extract_phones_from_message` ou em docstring os campos suportados
10. **Múltiplas extrações por mensagem** — Tentar todos os campos; retornar o primeiro que passar validação (>= 10 dígitos)

### Creative Alternatives

- **Reverse:** Em vez de parse da API, usar o payload enviado em `create_advanced_campaign` — armazenar `{folder_id: [numbers]}` e cruzar por folder (requer mudança de arquitetura)
- **Assumption bust:** E se a API retornar o número em base64 ou em campo aninhado? — Adicionar tentativa de decode e navegação em objetos aninhados
- **Lateral:** Usar regex no JSON bruto da resposta para capturar padrões `"number":"5511999999999"` ou `"chatid":"55...@s.whatsapp.net"` — independente da estrutura

---

## ⚖️ SOLUTION EVALUATION

### Evaluation Criteria

- **Efetividade** — Resolve o match?
- **Viabilidade** — Implementação simples?
- **Risco** — Regressão em outros fluxos?
- **Manutenção** — Código claro e documentado?

### Solution Analysis

| Solução | Efetividade | Viabilidade | Risco | Nota |
|---------|-------------|-------------|-------|------|
| Expandir parse (chatid, senderpn, jid) | Alta | Alta | Baixo | **Alinhado com feedback do usuário** |
| Corrigir sync-debug | Média (diagnóstico) | Alta | Nenhum | Complementar |
| Log DEBUG_SYNC_UAZAPI | Média (diagnóstico) | Alta | Nenhum | Complementar |
| Regex no JSON bruto | Alta | Média | Médio | Fallback se estrutura variar |

### Recommended Solution

**Expandir `_extract_phones_from_message` para priorizar `chatid`, `senderpn`, `jid`** — a API retorna o número em formato **remotejid** (ex.: `554137984966@s.whatsapp.net`). Normalizar após extração: remover sufixo `@s.whatsapp.net` ou `@g.us`, extrair apenas dígitos.

**Ordem de tentativa:** `number` → `chatid` → `chatId` → `sender` → `senderpn` → `jid` → `recipient` → `to`

**Normalização:** `str(val).split("@")[0]` seguido de `re.sub(r"\D", "", ...)` — já parcialmente implementado; garantir que remotejid seja tratado.

### Rationale

- Usuário confirmou: API usa **chatid, senderpn ou jid**, valor em formato **remotejid**
- Solução direta: adicionar esses campos e normalizar (lógica já existe para `@`)
- Baixo risco: apenas expande campos tentados; não altera lógica de match
- Corrigir sync-debug em paralelo para validar e futura diagnose

---

## 🚀 IMPLEMENTATION PLAN

### Implementation Approach

Implementação incremental: (1) expandir parse com chatid/senderpn/jid e normalização remotejid; (2) corrigir sync-debug para validação.

### Action Steps

1. **Alterar `_extract_phones_from_message` em `utils/sync_uazapi.py`:** ✅
   - Ordem de campos: `number`, `chatid`, `chatId`, `sender`, `senderpn`, `jid`, `recipient`, `to`, `wa_id`, `phoneNumber`
   - Para cada valor: `raw = str(val).split("@")[0]` (remove remotejid), `clean = re.sub(r"\D", "", raw)`
   - Retornar `clean` se `len(clean) >= 10`
   - Parse recursivo: se valor for dict, buscar recursivamente por chaves conhecidas

2. **Corrigir import em `app.py` (sync-debug):** ✅ (já estava correto: `fetch_all_phones_by_status`)

3. **Corrigir `scripts/run_sync_debug.py`:** ✅ (já estava correto)

4. **Log DEBUG_SYNC_UAZAPI:** ✅ Adicionado log de `first_message_structure` quando `DEBUG_SYNC_UAZAPI=1`

5. **Validar:** Rodar sync-debug na campanha teste9999; verificar `matched_count > 0` e `first_message_structure` exibindo o campo usado

### Timeline and Milestones

- Milestone 1: Parse expandido e sync-debug corrigido
- Milestone 2: Validação em campanha real (teste9999)

### Resource Requirements

- Acesso à campanha teste9999 e instância Uazapi conectada
- Ambiente com DB e API Uazapi disponíveis

### Responsible Parties

- Desenvolvedor: implementação
- Augusto: validação em campanha real

---

## 📈 MONITORING AND VALIDATION

### Success Metrics

- Sync atualiza ≥1 lead quando API retorna Sent para leads existentes
- Rollover identifica leads em Inicial que constam em list_messages(Sent)
- Logs não exibem mais "0 atualizados no DB" ou "0 leads em Inicial deram match" quando há Sent na API

### Validation Plan

1. Chamar `GET /api/campaigns/<id>/sync-debug` na campanha teste9999 — verificar `matched_count > 0`, `first_message_structure` com chatid/senderpn/jid
2. Rodar worker_cadence — verificar que sync atualiza e rollover avança quando aplicável
3. Conferir Kanban — leads com status Sent na API devem aparecer atualizados

### Risk Mitigation

- Manter fallback para `number`/`chatid`/`sender` — não remover campos existentes
- Se novo formato surgir: sync-debug permite inspecionar estrutura e adicionar campo

### Adjustment Triggers

- Se matched_count continuar 0 após alteração: inspecionar `first_message_structure` e adicionar o campo real
- Se API mudar formato: expandir parse ou considerar regex no JSON bruto

---

## 📝 LESSONS LEARNED

### Key Learnings

- API Uazapi usa `chatid`, `senderpn` ou `jid` com formato remotejid para mensagens de campanha
- Sync-debug é essencial para diagnosticar divergências API ↔ DB
- Manter imports atualizados ao renomear funções (fetch_all_phones_by_status)

### What Worked

- Five Whys para chegar à causa raiz (parse)
- Is/Is Not para delimitar escopo
- Feedback do usuário confirmando campos da API (chatid, senderpn, jid, remotejid)

### What to Avoid

- Assumir que schema OpenAPI reflete payload real de todos os endpoints
- Renomear funções sem atualizar todos os imports (app.py, scripts)

---

_Generated using BMAD Creative Intelligence Suite - Problem Solving Workflow_
