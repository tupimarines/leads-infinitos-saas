# Resumo das melhorias: campanhas Uazapi e chunks iniciais

**Data:** 2026-03-19  
**Contexto:** Campanhas Uazapi não continuavam enviando após o primeiro chunk de 30 mensagens; botão Continuar e agendamento no edit não funcionavam.

---

## 1. Bug: próximo chunk nunca era agendado

**Problema:** Nenhuma lógica criava `campaign_stage_sends` com `scheduled_for` para o próximo chunk de 30 mensagens da etapa inicial. O rollover estava desabilitado para Uazapi e não havia botão "Gerar" para a etapa Inicial.

**Solução:** Função `schedule_next_initial_chunk` no `worker_cadence.py` que:
- Roda a cada ciclo para campanhas Uazapi com leads pendentes
- Cria `campaign_stage_sends` com `scheduled_for` no próximo horário de envio
- `_materialize_scheduled_stage_sends` materializa e chama a API Uazapi

---

## 2. Query `_materialize`: leads pending no stage initial

**Problema:** A query buscava apenas `status='sent'`, mas os leads do próximo chunk ainda estão `pending`.

**Solução:** Para stage `initial`, usar `status IN ('sent', 'pending')`.

---

## 3. Timezone na materialização

**Problema:** Servidor em BRT; `scheduled_for` em UTC. A comparação `NOW()` falhava.

**Solução:** Usar `(NOW() AT TIME ZONE 'UTC')` na query SQL.

---

## 4. Botão "Continuar" no card da campanha

**Problema:** Não havia forma de forçar o próximo chunk quando o horário já tinha passado.

**Solução:**
- Endpoint `POST /api/campaigns/<id>/continue-initial-chunk`
- Agenda para ~30 segundos à frente
- Botão no card quando `pending_initial > 0`
- Campo `pending_initial` na API de stats

---

## 5. Poll mais frequente

**Problema:** Worker verificava a cada 60 segundos; atraso para pegar agendamentos.

**Solução:** `CADENCE_POLL_INTERVAL` de 60 → 30 segundos.

---

## 6. `scheduled_start` no edit da campanha

**Problema:** Editar e definir horário futuro não disparava o próximo chunk no horário certo.

**Solução:**
- Converter `scheduled_start` BRT → UTC ao salvar
- Se `scheduled_start` passou há 0–90s, usar "agora + 30s" em vez do próximo dia
- Limpar `scheduled_start` após uso para evitar loop

---

## 7. Loop infinito de agendamentos

**Problema:** Janela 0–10 min fazia o worker criar novo agendamento a cada 30s indefinidamente.

**Solução:** Janela reduzida para 0–90 segundos; limpar `scheduled_start` após uso.

---

## 8. Limite diário por instância

**Problema:** `can_create_campaign_today` permitia apenas 1 campanha/instância/dia. Após o primeiro chunk, todos os demais falhavam em silêncio.

**Solução:**
- Limite aumentado para 8 chunks por instância por dia
- Constante `UAZAPI_CHUNKS_PER_INSTANCE_PER_DAY = 8`

---

## 9. Logs de diagnóstico

**Adicionados:**
- `📤 [Materialize]` ao processar agendamento
- `✅ [Materialize] folder_id=... (N msgs)` quando cria com sucesso
- `⚠️ [Materialize] limite diário atingido` quando `can_create_campaign_today` retorna False
- `⚠️ [Materialize] Uazapi create_advanced_campaign falhou` quando API não retorna folder_id
- `📅 [Initial Chunk]` com horário em BRT

---

## Arquivos alterados

| Arquivo | Alterações |
|---------|------------|
| `worker_cadence.py` | schedule_next_initial_chunk, _materialize (query, timezone, logs), poll 30s, scheduled_start, **sempre carregar mensagens de campaign_steps** |
| `app.py` | continue-initial-chunk endpoint, pending_initial em stats, update scheduled_start UTC |
| `templates/campaigns_list.html` | Botão Continuar, função continueInitialChunk |
| `utils/limits.py` | UAZAPI_CHUNKS_PER_INSTANCE_PER_DAY = 8 |
| `utils/sync_uazapi.py` | **_sync_folder_via_listfolders usa log_success para qualquer status** |

---

## Commits

1. `578db03` - fix(uazapi): agendar próximo chunk inicial + botão Continuar no card
2. `5f51030` - fix(uazapi): timezone UTC na materialização + delay 30s no Continuar
3. `d7a377a` - fix: log de agendamento em BRT para clareza
4. `fedae05` - fix: scheduled_start no edit + poll 30s + envio imediato quando horário recém passou
5. `47f37d3` - fix: loop infinito + limite 8 chunks/dia + logs materialize

---

## 10. Materialize: mensagens de campaign_steps (edit form)

**Problema:** Chunks 2+ podiam usar snapshot antigo (message_variations) em vez das mensagens atuais do edit.

**Solução:** Sempre carregar de `campaign_steps` (fonte de verdade); fallback para message_variations só se vazio.

---

## 11. Sync: list_folders como fonte de verdade

**Problema:** `list_messages` retorna só a 1ª mensagem do batch; contagens erradas.

**Solução:** `_sync_folder_via_listfolders` usa `log_success` de list_folders para qualquer status (done, scheduled, sending, running).

---

## Debug: mensagens não chegam no WhatsApp

**Sintoma:** Logs mostram ✅ folder_id na materialização, mas mensagens não chegam.

**Script de debug (chunks 2+):**
```bash
python scripts/run_sync_debug.py --chunks 140 141
# ou sem IDs para usar últimas 5 campanhas Uazapi
python scripts/run_sync_debug.py --chunks
```

O script verifica para cada `campaign_stage_send`:
- Status da instância (connected?)
- API: Sent/Failed/Scheduled
- list_folders: se o folder existe e status

**Logs adicionados no worker:**
- `API status=` e `count=` na linha ✅ [Materialize]
- Fallback `folderId` (camelCase) além de `folder_id`
- Em falha: `Response: {result}`

**Checklist de investigação:**
1. Instância `connected`? (script mostra)
2. Folder em `list_folders`? (script mostra)
3. `Scheduled > 0` na API? → Uazapi ainda não enviou (delay 5–15 min/msg)
4. `Sent > 0`? → Mensagens foram enviadas; verificar números/WhatsApp
5. Delay: primeira msg do chunk leva 5–15 min após create_advanced_campaign
