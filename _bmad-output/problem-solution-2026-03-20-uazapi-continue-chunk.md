# Problem solving: Continuar chunk inicial sem folder na Uazapi

**Data:** 2026-03-20  
**Workflow:** CIS problem-solving (síntese)

---

## 1. Definição do problema

**Sintoma:** Após `POST /api/campaigns/<id>/continue-initial-chunk`, o log mostrava agendamento (`1 instâncias agendadas para …`), mas `list_folders` na instância não exibia nova campanha (folder) para o próximo chunk inicial.

**Impacto:** Operador não consegue confiar no botão Continuar; próximo lote de 30 mensagens não parte quando esperado.

---

## 2. Análise de causa raiz

| Hipótese | Evidência |
|----------|-----------|
| **A. Dependência exclusiva do worker** | O fluxo antigo só fazia `INSERT` em `campaign_stage_sends` com `scheduled_for ≈ now+30s` e dependia do `worker_cadence` chamar `_materialize_scheduled_stage_sends`, que só materializa linhas na **janela Python** `remaining ≤ 5 min` e `remaining ≥ -15 min` após o SELECT SQL. |
| **B. Race / atraso** | Poll de 30s, deadlock, deploy ou fila podem fazer o worker passar **fora** da janela ou competir com outro processo. |
| **C. `schedule_next_initial_chunk`** | Continua agendando o **próximo dia** (slot diário); não substitui a necessidade de materializar o `continue`, mas o operador pode confundir timestamps no log. |
| **D. Toggle Start** | `toggle_pause` só chamava `edit_campaign(continue)` na Uazapi para folders **já existentes**; **não** criava novo chunk para leads `pending` no estágio inicial. |

**Conclusão:** O gargalo principal é **materialização assíncrona e condicionada a janela curta**, não o INSERT em si.

---

## 3. Critérios de solução (definitiva e funcional)

1. **Continuar (botão):** após sucesso HTTP, a campanha Uazapi (`create_advanced_campaign`) deve ser criada **no mesmo request** sempre que possível.  
2. **Start (pause/start):** ao voltar para `running`, tentar o mesmo fluxo de próximo chunk inicial (sem falhar o toggle se não houver pendentes).  
3. **Horário do dia seguinte:** manter `schedule_next_initial_chunk` + materialização no worker (janela 2–5 min) sem regressão.  
4. **DRY:** uma função core reutilizada pela API e pelo toggle.

---

## 4. Solução implementada

1. **`worker_cadence._materialize_scheduled_stage_sends(conn, force_send_ids=None)`**  
   - Com `force_send_ids`, busca esses `campaign_stage_sends` e **ignora** o filtro de janela 2–5 min (ainda ignora agendamentos com mais de 24h de atraso).  
   - Retorna `{"folders_created": n}`.

2. **`app._continue_initial_chunk_core(campaign_id, user_id, log_label=...)`**  
   - Valida campanha, pendentes, instâncias, limite diário, mensagens.  
   - `INSERT … RETURNING id` para coletar ids.  
   - `commit` e em seguida chama `_materialize_scheduled_stage_sends(conn_m, force_send_ids=created_ids)`.  
   - Resposta JSON inclui `folders_created` e mensagem explícita.

3. **Rota** `continue-initial-chunk` delega ao core.

4. **`toggle_pause`** (ramo Uazapi, `new_status == running` e `enable_cadence`): chama o mesmo core e opcionalmente devolve `initial_chunk` no JSON.

---

## 5. Verificação sugerida

1. Campanha Uazapi com `pending` no step 1; clicar **Continuar** → em até segundos, `list_folders` deve mostrar novo folder `Campaign {id} initial inst {instance_id}`.  
2. Pausar / **Start** com pendentes → mesmo efeito (se não houver agendamento duplicado na janela de 15 min).  
3. Sem pendentes → toggle continua 200; core retorna 400 apenas internamente (não usado para falhar o toggle).  
4. Worker ainda materializa envios agendados automaticamente no dia seguinte.

---

## 6. Riscos / notas

- **Limite diário (atualizado 2026-04):** `can_create_campaign_today` é sempre `True` em `utils/limits.py`; o gate real de cota em destrave/materialize é `check_initial_chunk_daily_quota_for_campaign` e afins (ver `tech-spec-recuperacao-scheduled-stale-worker-cadence-uazapi.md`).  
- **Duplicata 15 min:** dedupe por instância evita spam de INSERTs.  
- **Import `worker_cadence` no `app`:** aceitável; alternativa futura seria extrair materialização para `utils/`.
