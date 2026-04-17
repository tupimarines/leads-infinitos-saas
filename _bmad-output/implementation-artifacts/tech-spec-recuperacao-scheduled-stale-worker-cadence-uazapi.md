---
title: 'Recuperação resiliente de chunks scheduled fora da janela (worker_cadence / Uazapi)'
slug: recuperacao-scheduled-stale-worker-cadence-uazapi
created: '2026-04-16T12:00:00Z'
status: ready-for-dev
stepsCompleted:
  - 1
  - 2
  - 3
  - 4
  - 6
  - 7
  - 8
  - 9
  - 10
  - 11
party_mode_insights_merged: '2026-04-16'
party_mode_review_2: '2026-04-16'
advanced_elicitation_merged: '2026-04-16'
user_accepted_wip_changes: '2026-04-16'
elicitation_shuffle_round: 2
tech_stack:
  - Python 3.x
  - PostgreSQL (psycopg2, RealDictCursor)
  - Flask (rotas continue-initial-chunk; opcional rota admin)
  - Uazapi HTTP (uazapi_service)
files_to_modify:
  - worker_cadence.py
  - utils/initial_chunk_schedule_target.py
  - utils/campaign_send_policy.py
  - utils/limits.py
  - app.py
  - worker_sender.py
  - services/uazapi.py
  - tests/ (ex.: test_worker_cadence_initial_chunk.py ou novo módulo; expandir no Passo 3)
code_patterns:
  - 'Queries SQL com placeholders %s; scheduled_for em UTC naive alinhado a datetime.utcnow() no materialize'
  - 'INITIAL_CHUNK_ACTIVE_SEND_STATUSES como SSOT do bloqueio em schedule_next_initial_chunk'
  - 'force_send_ids ignora janela temporal (continue-initial-chunk)'
  - '_next_initial_send_slot: no máximo um slot automático “de manhã” por dia civil; use_immediate / scheduled_start já passou trata “agora + margem” em schedule_next_initial_chunk'
  - 'check_daily_limit / get_user_daily_limit — eixo separado do slot de calendário'
  - 'process_cadence: _materialize_scheduled_stage_sends(conn) linha ~944 antes do loop de campanhas; schedule_next_initial_chunk só no ramo use_uazapi_sender (~1003)'
  - 'create_advanced_campaign em worker_cadence ~757; UPDATE status running ~787–811 (API pode devolver queued mas linha local vai a running após sucesso)'
  - 'worker_sender.process_campaigns: loop ~294–311 só _sync_uazapi_usage + sleep 60 — sem check_instance_daily_limit no envio em massa Uazapi'
test_patterns:
  - 'pytest em tests/; fixtures conforme test_worker_cadence_initial_chunk.py'
  - 'Passo 3: expandir testes além de constantes (frozen time, DB integration conforme harness existente)'
---

# Tech-Spec: Recuperação resiliente de chunks scheduled fora da janela (worker_cadence / Uazapi)

**Created:** 2026-04-16

## Party Mode — síntese (rodada colaborativa)

*Perspectivas condensadas sobre o mesmo tema (cadência vs cota vs destrave).*

- **Produto / cadência:** O utilizador perceciona “1 envio por dia” porque `_next_initial_send_slot` coloca o próximo chunk automático no **primeiro** `send_hour_start` do dia ou no **D+1** — não confundir com o teto `~30` leads por pasta nem com `daily_sends` do plano. Destrave sem política de “same day” continua a cair em D+1 fora da janela da manhã, o que é coerente com o código atual mas frustra após incidente à tarde.
- **Engenharia:** `can_create_campaign_today` em `utils/limits.py` **sempre retorna `True`** (comentário L211–215). A docstring do slot matinal foi alinhada em **`utils/initial_chunk_schedule_target.py`** (`cadence_next_initial_send_slot`, sucessor de `worker_cadence._next_initial_send_slot`) + nota no import em `worker_cadence.py` (T11). O gating real de volume é `check_initial_chunk_daily_quota_for_campaign` / plano + `campaigns.daily_limit` (TD-12), não esse helper.
- **Operações / CS:** Qualquer “flush” ou admin precisa de **auditoria** + resposta JSON clara; se a cota diária esgotou, o resultado esperado é mensagem explícita ou agendamento D+1, não falha silenciosa.

---

## Glossário — conceitos **distintos** (não intercambiáveis)

1. **“1 chunk/dia” (cadência automática do worker):** Regra de **calendário** em `_next_initial_send_slot` + `schedule_next_initial_chunk`: tende a no máximo **um** novo `campaign_stage_sends` `initial` automático por **dia civil** no slot `send_hour_start` (BRT, respeitando sábado/domingo). Se o slot já passou, empurra para o próximo dia útil. É **anti-flood de agendamento**, não o número “30” do plano.

2. **Chunk (lote operacional):** Lote que a **materialização** monta para Uazapi — até `per_instance_limit` (hoje ~30 leads/instância/pasta), com delays da campanha. **Vários chunks no mesmo dia** só entram se outro mecanismo os criar (ex.: Continuar / `continue-initial-chunk` / futura política “same day after unlock”), não pela regra “só manhã automática” sozinha.

3. **Limite diário de disparos (plano vs campanha):** `get_user_daily_limit` define o teto do **plano/licença** (por utilizador, com variante por `instance_id` no plano infinite). `check_daily_limit(user_id, plan_limit)` em `utils/limits.py` conta **todos** os iniciais `sent` hoje (BRT) **de todas as campanhas** desse `user_id` — não é “por campanha” até existir query/helper explícito. O campo `campaigns.daily_limit` é o teto **escolhido na criação** (≤ plano). O loop atual de `worker_sender.process_campaigns` **não** aplica `check_instance_daily_limit` no envio em massa Uazapi (pastas). **Eixo separado** do “1 insert automático/dia”.

4. **`can_create_campaign_today`:** No código atual **sempre `True`** (`utils/limits.py` L211–215). Specs antigos que citavam “8 chunks/dia” via esse helper estão **desatualizados**; o gargalo “um slot automático/dia” vem de `_next_initial_send_slot`, não desse boolean.

5. **Manual + limite diário:** Mesmo após destrave (`failed`, `continue-initial-chunk`, admin), se `check_daily_limit` / política de produto disser que **não cabe** mais envio hoje, o comportamento esperado é **só no próximo dia** ou **erro HTTP explícito** (ex. 429/400 documentado), **sem** furar a cota.

---

## Overview

### Problem Statement

Em produção, campanhas Uazapi parecem “não recomeçar sozinhas” após queda ou restrição temporária do WhatsApp (minutos a horas, inclusive >24h). O fluxo de cadência grava `campaign_stage_sends` com `status='scheduled'` e `uazapi_folder_id` NULL; o `worker_cadence` materializa perto do horário via `_materialize_scheduled_stage_sends`.

Dois mecanismos **combinados** prendem o fluxo:

1. **Janela estreita de materialização automática:** query UTC **−15 min** … **+5 min** (`worker_cadence.py` ~454–458) + filtro Python `remaining` (~471–475). Fora da janela, o materialize automático não volta a pegar o send (salvo `force_send_ids`).

2. **`scheduled` bloqueia novo chunk:** `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` inclui `'scheduled'` (`utils/limits.py` L13–16). `schedule_next_initial_chunk` não insere outro chunk para a mesma instância se já existir um ativo nesses status (~1339–1349).

**Corolário de produto:** Após marcar `failed` ou recuperar, `schedule_next_initial_chunk` volta a correr — mas `_next_initial_send_slot` pode colocar o próximo automático só em **D+1** após o `send_hour_start`, **ainda que** a instância esteja OK. Isso é **independente** do tamanho do chunk (~30) e **independente** do limite diário de mensagens do plano.

**Resultado:** registro eternamente `scheduled` sem pasta, fora da janela → instância “ocupada” → campanha parada até SQL manual, delete, ou `continue-initial-chunk`.

### Solution

- **Stale scheduled recovery** (TTL + política explícita, anti-duplicação Uazapi) como no spec base.
- **Unificação de constantes** da janela de materialize (SQL + Python).
- **Objetivo de produto pós-destrave (mesmo dia útil):** quando o último `scheduled` ativo sem pasta for removido/falhado e `now_brt` estiver em `[send_hour_start, send_hour_end]` com cota diária ainda permitindo envios iniciais hoje, permitir agendar o próximo chunk **no mesmo dia** em horário seguro (ex. agora + margem), com log `reason` estável (ex. `same_day_after_unlock`). Caso contrário: **D+1** ou erro documentado — **sem** violar cota.
- **Opções de implementação** D1–D3 (ver Implementation Plan); **telemetria** com `within_send_window`, `daily_limit_remaining` quando calculável.
- **Documentação:** corrigir referências a “8 chunks/dia via `can_create_campaign_today`” (código + artefatos internos listados em Notas).

### Scope

**In Scope:**

- Recovery stale + constantes + testes + eventos JSON (spec base).
- Especificação e, se aprovado no Passo 2/3, implementação de **“resume same day”** condicionado a gatilho (D1) e alinhamento a `check_daily_limit` / janela BRT.
- Manter e documentar `POST .../continue-initial-chunk` com `cancel_scheduled` (D2) e interação com limite diário.
- Opcional neste epic: endpoint admin **D3** (`POST /api/admin/campaigns/<id>/flush-stale-initial-chunk` nome ilustrativo): listar stale, `UPDATE ... failed` com auditoria, opcionalmente re-agendar mesmo dia conforme secção de produto; JSON `{ updated, next_scheduled_for, ... }`; `@admin_required`, CSRF, rate limit.

**Out of Scope:**

- Só alargar janela sem TTL/idade (rejeitado).
- Redesign completo do modelo de estados.

---

## Apêndice A — Problema (refino)

Chunks `initial` em `scheduled` sem `uazapi_folder_id` podem ficar fora da janela de materialização. Enquanto `status ∈ INITIAL_CHUNK_ACTIVE_SEND_STATUSES`, não se agenda outro chunk na mesma instância. Após `failed` ou recovery, `schedule_next_initial_chunk` reage — mas `_next_initial_send_slot` prioriza no máximo **um** agendamento automático por dia civil no `send_hour_start`; depois desse horário, próximo slot **D+1**, independentemente do chunk ~30 e independentemente do limite diário do plano (eixo `check_daily_limit`).

## Apêndice B — Objetivo de produto

Após destrave (manual, admin ou job): se `now_brt` ∈ janela de envio da campanha (`send_hour_start`–`send_hour_end`, sábado/domingo), permitir próximo chunk inicial **no mesmo dia** (UTC naive coerente com o pipeline), desde que a cota diária permita; senão D+1 ou erro explícito, sem furar cota. Não duplicar pasta: se existir `running`/`partial` com pasta na mesma instância, não inserir segundo chunk ativo.

## Apêndice C — Critérios de aceite (Given/When/Then)

- **AC-SAME-1:** Dado destrave que remove o último `scheduled` ativo **sem pasta** para `(campaign_id, instance_id)`, quando `now_brt` ∈ `[send_hour_start, send_hour_end]` e a cota diária ainda permite envios iniciais hoje, então `scheduled_for` do novo `campaign_stage_sends` cai no **mesmo dia** (UTC naive coerente), com log/evento `reason: same_day_after_unlock` (ou nome estável acordado).

- **AC-SAME-2:** Quando a cota diária **não** permitir (incluindo após `continue-initial-chunk` manual), então não agendar envio que viole o limite; retorno **429/400 documentado** ou agendamento **D+1**, com mensagem explícita.

- **AC-DUP-1:** Dado `running`/`partial` com pasta na mesma instância, quando o fluxo de destrave/re-agendar corre, então **não** inserir segundo chunk ativo que duplique pasta.

- **AC-RULE-1 (janela BRT):** Dado qualquer recovery, same-day ou `continue-initial-chunk`, quando o sistema agenda ou materializa um send Uazapi `initial`, então **não** resulta em `create_advanced_campaign` com `is_campaign_send_window(campaign) == false` no instante de materialização, **salvo** se a política explícita for “re-agendar para `next_valid`” com log `reason` documentado (nunca skip silencioso indefinido).

- **AC-RULE-2 (dias úteis):** Dado recovery ou `scheduled_for` recalculado num sábado ou domingo em que a campanha não permite envio, então o próximo `scheduled_for` cai no **próximo dia permitido** (mesma semântica que `_next_send_datetime` / flags da campanha).

- **AC-RULE-3 (cota diária):** Dado `campaign.daily_limit` e o teto efetivo do plano/instância (`get_user_daily_limit`), quando se insere ou materializa chunk que consome cota de iniciais no dia BRT, então a decisão de permitir/bloquear segue a fórmula **documentada no ADR-COTA-SSOT** (implementação obrigatória para fechar TD-8/TD-10); resposta HTTP ou estado `failed` com mensagem explícita se bloqueado.

- **AC-RULE-4 (tamanho do lote):** Dado campanha com `daily_limit` de criação inferior ao default de materialização (~30), quando o worker monta o lote Uazapi `initial`, então o tamanho respeita o **mesmo teto** usado na criação/expectativa do utilizador (corrigir hardcode ~488–489 ou justificar SSOT alternativo no código + spec).

- **AC-WHATIF-1:** Dado destrave ou recovery à volta da meia-noite BRT, quando a política de cota usa “hoje BRT”, então o resultado (permitir / bloquear / mensagem) é **determinístico** e coberto por teste de limite.

- **AC-WHATIF-2:** Dado campanha com `status` não processado pelo `process_cadence` (ex.: `draft`/`cancelled`), quando o job de stale **automático** corre, então **nenhum** `campaign_stage_sends` dessa campanha é alterado (salvo fluxo admin explícito com parâmetro `force` documentado e super-restrito).

- **AC-BASE-1:** Dado um `campaign_stage_sends` `scheduled` sem pasta com `scheduled_for` anterior ao **TTL** configurado, quando o `worker_cadence` executa após condições definidas (ex.: instância ok / política TD-1), então o sistema **não** deixa a campanha bloqueada indefinidamente por `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` sem ação automatizada ou transição explícita documentada.

- **AC-BASE-2:** Dado materialize automático, quando `scheduled_for` está dentro da janela unificada (lookback/lookahead documentados), então o comportamento permanece equivalente ao atual (regressão zero nos casos já cobertos por testes).

- **AC-BASE-3:** Dado send stale tratado pelo recovery, então não há segunda pasta Uazapi duplicada para o mesmo batch quando a API/sync já indica pasta existente (verificável por teste ou mock).

## Apêndice D — Opções de implementação

| ID | Descrição |
| -- | --------- |
| **D1** | Modo “resume same day” só após **gatilho explícito** (flag admin, env operacional, ou coluna transitória na campanha), reutilizando padrão `scheduled_start` já passou (`worker_cadence.py` ~1273–1293) ou `target_dt = now_brt + timedelta(minutes=2)` alinhado a `_next_send_datetime` com `delay_days<=0` onde aplicável. |
| **D2** | Manter `POST /api/campaigns/<id>/continue-initial-chunk` com `cancel_scheduled` para operador autenticado; documentar interação com limite diário e janela BRT. |
| **D3** | Botão/admin: `POST /api/admin/campaigns/<id>/flush-stale-initial-chunk` (nome ilustrativo): (i) listar `scheduled` + `uazapi_folder_id` IS NULL fora da janela ou idade > TTL; (ii) `UPDATE … failed` com auditoria (`admin_user_id`, timestamp); (iii) opcionalmente mesmo dia conforme Apêndice B; (iv) JSON `{ "updated": n, "next_scheduled_for": … }`. CSRF + `@admin_required` + rate limit. |

## Apêndice E — Telemetria

Evento estruturado sugerido: `campaign_id`, `send_ids`, `reason`, `within_send_window` (BRT), `within_materialize_window` (UTC), `sent_today_user`, `sent_today_campaign` (se G2), `daily_limit_remaining`, `effective_cap` (interpretação conforme G1–G3 + TD-10), `campaign_daily_limit`, `quota_policy` (`g1`|`g2`|`g3`), `skipped_outside_window` (boolean), `next_valid_scheduled_for` (quando aplicável).

## Apêndice F — Documentação interna

- [x] **T11:** Docstring de `cadence_next_initial_send_slot` (`utils/initial_chunk_schedule_target.py`, sucessor de `_next_initial_send_slot`) + comentário de navegação em `worker_cadence.py`; alinhado a `can_create_campaign_today` sempre True e aos eixos do glossário.
- [x] **T11:** Artefatos `_bmad-output/` (`tech-spec-validacao-chat-check-reorganizacao-envio-campanhas.md`, `tech-spec-uazapi-campanhas-n8n-sync-observabilidade.md`, `problem-solution-2026-03-20-uazapi-continue-chunk.md`) — notas de legado / redação atualizada.

## Apêndice G — Regras de envio no código (para evitar conflito com recovery / same-day)

| Regra | Onde vive hoje | Comportamento relevante para esta spec |
| ----- | -------------- | --------------------------------------- |
| **Janela horária + fim de semana** | `worker_cadence.is_campaign_send_window` (~L131–147): `send_hour_start` ≤ hora BRT \< `send_hour_end`; sábado/domingo bloqueados se flags `send_saturday` / `send_sunday` forem falsas. | Em `_materialize_scheduled_stage_sends`, se `use_uazapi_sender` e **fora** da janela: **só** `continue` (skip) com log (~620–624) — **não** altera `status` nem `scheduled_for`. Combinação perigosa com janela UTC −15/+5: o send pode ficar `scheduled` indefinidamente fora do materialize **e** fora da janela BRT até o relógio alinhar os dois. |
| **Agendamento automático de chunk** | `schedule_next_initial_chunk` — **não** está guardado por `is_campaign_send_window` no loop principal (`process_cadence` ~999–1003); corre para todas as campanhas Uazapi da lista. | Pode inserir `campaign_stage_sends` com `scheduled_for` no futuro (slot manhã) mesmo quando “agora” está fora da janela; o materialize é quem aplica a janela na hora de criar pasta. |
| **Cota “plano” (usuário/instância)** | `get_user_daily_limit` em `utils/limits.py`; `check_daily_limit(user_id, plan_limit)` compara contagens **de todo o user** (iniciais `sent` hoje BRT) vs `plan_limit` passado como argumento. | **Não** é chamado por `worker_cadence` nem por `_continue_initial_chunk_core` hoje. |
| **`campaigns.daily_limit` (valor escolhido na criação)** | `_create_campaign_core` (`app.py` ~4955–4960): `daily_limit = max(5, min(int(submitted), plan_limit))`; usado no fluxo de criação de campanha/lotes (`per_instance_limit` derivado, ~5090–5118). | Em `_materialize_scheduled_stage_sends` o `per_instance_limit` para montar pastas está **fixo em 30** (`worker_cadence.py` ~488–489) — **desalinhado** do teto menor que o user escolheu na campanha; risco de montar pasta maior que a intenção de “throttle” da campanha (TD-10). |
| **`can_create_campaign_today`** | `utils/limits.py` — sempre `True`. | Ramo 429 em `app.py` ~5974–5978 e `failed` no materialize ~675–682 são **lógica legada** sem efeito hoje. |
| **`check_instance_daily_limit`** | `worker_sender.py` — definido, **sem call sites** no código de produção (só testes). | Não assumir que o sender “já aplica” este gate em runtime até confirmar call path na implementação atual do disparo Uazapi. |

---

## Advanced Elicitation — métodos 1 a 5 (consolidado) + riscos citados pelo utilizador

### 1) Pre-mortem (falhas em produção se a spec estiver errada)

| Cenário de falha | Causa provável no desenho | Prevenção no spec/implementação |
| ---------------- | ------------------------- | -------------------------------- |
| Campanha **nunca** dispara após recovery | `scheduled_for` ou `force_send_ids` corre materialize, mas `is_campaign_send_window` é falso **sempre** (ex. same-day às 22h com `send_hour_end`=20) — skip silencioso em ciclo. | Qualquer “bump” para `now+N` deve usar **`next_valid_send_utc_naive(campaign)`** (nome ilustrativo) que combine BRT janela + dias permitidos + margem materialize; telemetria `skipped_outside_window`. |
| Disparo **fora do horário** | Recovery força materialização sem revalidar janela BRT; ou `continue` com `utcnow+30s` quando já passou `send_hour_end`. | Gate único: antes de `create_advanced_campaign`, `is_campaign_send_window` **ou** reagendar explicitamente. |
| Disparo em **domingo proibido** | `schedule_next_initial_chunk` / same-day não chama `_next_send_datetime` com flags sat/sun. | Reutilizar a mesma lógica de dia útil que `_next_initial_send_slot` / `_next_send_datetime`. |
| **Além da cota** do utilizador | Só `get_user_daily_limit`/`check_daily_limit` com limite certo **e** eventual teto `campaign.daily_limit` por campanha. | TD-8 + TD-10: definir fórmula documentada **antes** de INSERT/materialize. |
| Pasta duplicada / chunk duplo | Recovery + `schedule_next` concorrente; `queued` não em `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`. | TD-7 + transação + pré-sync já existente. |

### 2) Failure Mode Analysis (por componente)

- **`_materialize_scheduled_stage_sends`:** falha “janela UTC” + “janela BRT” ortogonais; `per_instance_limit` fixo 30 ignora `campaigns.daily_limit`.
- **`schedule_next_initial_chunk`:** não consulta cota nem janela BRT antes do INSERT; depende de materialize + sender.
- **`_continue_initial_chunk_core`:** não usa `check_daily_limit`; busy inclui `queued` ≠ limits.py.
- **`check_daily_limit`:** agrega **todos** os envios iniciais do `user_id` — se passar `plan_limit` errado (ex. só plano, ignorando `campaign.daily_limit`), pode furar intenção “campanha com teto 5”.
- **`worker_sender` (disparo real):** confirmar no Passo 2 qual função efetivamente bloqueia envio (não assumir `check_instance_daily_limit` ativo).

### 3) ADR (síntese — decisões a fechar)

| ID | Opções | Trade-off | Recomendação provisória para implementação |
| -- | ------ | --------- | ------------------------------------------- |
| ADR-RECOVERY | A force / B failed / C bump | A acelera mas exige gates fortes; B liberta slot com UX “falhou”; C minimiza surpresa se `next_valid` bem definido | **B + C** como default seguro (failed ou bump para próximo slot válido), **A** só com instância ok + dentro da janela + cota ok |
| ADR-COTA-SSOT | **G1 / G2 / G3** (secção “Cota para gate”) | Ver tabela na secção **Ordem no tick e cota**; G2 = duplo user+campaign | **Default recomendado para especificação:** **G2** até produto decidir diferente; documentar no PR |
| ADR-SAME-DAY | Só após gatilho explícito (D1) | Evita flood acidental | Manter D1; obrigar `is_campaign_send_window` no momento alvo |

### 4) Challenge (advogado do diabo)

- “Same day after unlock” com `now+2min` **sem** verificar `send_hour_end` **replica** o bug de skip infinito em materialize.
- Confiar em `check_daily_limit(user, plan_limit)` sem filtrar por `campaign_id` pode bloquear campanha A porque campanha B esgotou o teto do plano — pode ser desejável ou não; tem de estar **escrito**.
- Aumentar só TTL/janela materialize **sem** `next_valid` BRT aumenta risco de `create_advanced` fora da política de produto.

### 5) Matriz comparativa (critérios × política de recovery)

Critérios em linhas: respeita janela BRT; respeita sáb/dom; não excede plano; não excede `campaign.daily_limit`; não duplica pasta; destrava stale \< 24h.  
Políticas em colunas: **A** force materialize | **B** failed | **C** bump `scheduled_for`.  
Resultado: só **C com `next_valid`** e **B** marcam checks fortes em janela/calendário sem mentir ao utilizador; **A** só com todos os gates + cota OK.

### Shuffle — método 3 (First Principles)

**Invariantes (só mudam com decisão de produto explícita):**

- Não duplicar pasta / chunk ativo para o mesmo lote de leads na mesma instância.
- Não avançar `create_advanced_campaign` quando a **janela da campanha** (BRT + sáb/dom) falha, exceto com re-agendamento **explícito** (`next_valid`, TD-9) ou `failed` com mensagem — **nunca** skip silencioso infinito.
- Cota e teto de lote: o utilizador com `campaign.daily_limit` abaixo do plano deve ver comportamento coerente com a criação da campanha (AC-RULE-4).

**Legado a não confundir com invariantes:** `can_create_campaign_today` sempre `True`; `per_instance_limit = 30` hardcoded no materialize.

**Desenho mínimo recomendado:** um núcleo único (função ou módulo pequeno, nome ilustrativo `assert_send_policy_ok(campaign, instance_id, proposed_utc_naive)`) que concentre janela BRT, dia permitido, cota segundo **G1/G2/G3** (default de spec **G2** até decisão de produto) e opcionalmente janela materialize — chamado por recovery, `continue-initial-chunk`, same-day e D3 — para evitar regras divergentes em 4 sítios.

### Shuffle — método 5 (What If)

| Cenário | Implicação |
| --------| ----------- |
| **Skew / fonte de tempo** (`NOW()` SQL vs `datetime.utcnow()` Python) | `next_valid` e testes devem usar a **mesma convenção** documentada; preferir testes com tempo congelado; considerar alinhar código a UTC aware numa fase posterior (fora do núcleo do PR se grande). |
| **N instâncias na campanha** | Cota por instância (`get_user_daily_limit(..., instance_id)`) vs cota user-wide: fechar no TD-8 para não bloquear instância B por erro de interpretação na instância A. |
| **Campanha não elegível ao worker** (`status` não em `running`/`pending`/`completed`, ou cadência off) | Recovery **automático** não deve alterar sends de campanhas arquivadas/canceladas sem critério explícito; D3 admin pode exigir `status` whitelist no body. |
| **`enable_cadence=true` + Uazapi** | Stale sem pasta em `follow1` / `follow2` / `breakup` — o spec de **fase 1** restringe recovery a `stage='initial'` **ou** documenta ordem de prioridade FU vs initial para evitar efeitos colaterais nos rollovers. |
| **Transição meia-noite BRT** | Contagem de “dia” para cota e mensagens “tente amanhã” devem ser testadas nos limites `23:59` / `00:01` BRT. |
| **Soma de várias campanhas do mesmo user** | Se o plano for teto **global**, `check_daily_limit` sem filtro de campanha está correto; se cada campanha tem teto **independente** além do plano, a query tem de mudar — **explicitar no ADR-COTA-SSOT** (já notado no Challenge §4). |

*(AC formais **AC-WHATIF-1..2** no Apêndice C.)*

---

## Context for Development

### Codebase Patterns

- Monolito Flask + workers; cadência em `worker_cadence.py`; limites em `utils/limits.py`.
- Três eixos independentes: **slot calendário** (`_next_initial_send_slot`); **janela BRT + fins de semana** (`is_campaign_send_window` na materialização); **cotas** (plano/`get_user_daily_limit`, `campaigns.daily_limit`, e eventual `check_daily_limit` — ver Apêndice G).

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `utils/limits.py` | `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`, `can_create_campaign_today`, `check_daily_limit` |
| `worker_cadence.py` | `_materialize_scheduled_stage_sends`, `schedule_next_initial_chunk`, `_next_initial_send_slot`, `use_immediate` / `scheduled_start` |
| `app.py` | `_continue_initial_chunk_core`, `continue-initial-chunk` |
| `utils/sync_uazapi.py` | pré-sync / anti-duplicidade |
| `worker_sender.py` | `_sync_uazapi_usage` (~243–286), loop `process_campaigns` (~294–311): **sem** gate de cota no loop principal; envio em massa é pela API Uazapi (pastas). |
| `services/uazapi.py` | `create_advanced_campaign`, `edit_campaign`, `get_status` — erros da API alimentam ramos `failed` no materialize. |

### Passo 2 — Ancoragens no código (ground truth)

| Âncora | Localização | Notas |
| ------ | ----------- | ----- |
| Materialize automático | `worker_cadence.process_cadence` ~944 `_materialize_scheduled_stage_sends(conn)` | Corre **antes** do `SELECT` de campanhas; afeta **todas** as linhas elegíveis na BD, não só a lista do tick. |
| Materialize forçado | `app.py` ~6117–6120 import `worker_cadence as wc`; `wc._materialize_scheduled_stage_sends(conn_m, force_send_ids=created_ids)` | Após INSERTs do `_continue_initial_chunk_core`. |
| Janela BRT no materialize | `worker_cadence.py` ~611–624 | `is_campaign_send_window(camp_win)`; fora → `continue` sem UPDATE (risco de stale + Apêndice G). |
| Janela UTC SQL + Python | ~454–458, ~471–475 | Constantes a unificar (spec). |
| `schedule_next_initial_chunk` | ~1003 no loop `for campaign in campaigns` | Só campanhas `use_uazapi_sender`; **não** recebe `daily_limit` no `SELECT` atual (~974–988) — falta coluna para TD-10. |
| Campanhas elegíveis ao worker | `WHERE c.status IN ('running', 'pending', 'completed')` ~979 | Recovery automático deve respeitar o mesmo conjunto (AC-WHATIF-2) salvo exceção admin. |
| `continue-initial-chunk` HTTP | `app.py` ~6153–6167 | Body JSON `cancel_scheduled`; `@login_required`. |
| Toggle running + cadência | `app.py` ~4568–4573 | Chama `_continue_initial_chunk_core` ao despausar com `enable_cadence` — mesmo núcleo que o botão dedicado. |
| Pós-`create_advanced` | ~787–811 | `status = 'running'`; API pode reportar `queued` mas estado local é `running`. Busy check em continue inclui `queued` (~6001) — alinhar com TD-7. |

**Investigação adicional sugerida (para o Passo 3 / implementação):** `services/uazapi.py` (timeouts, erros); migrações `init_db` / índices em `campaign_stage_sends`; qualquer outro `INSERT INTO campaign_stage_sends` além de `schedule_next_initial_chunk` e `_continue_initial_chunk_core` / pacing no materialize.

### Technical Decisions

| ID | Decisão | Status |
| -- | ------- | ------ |
| TD-1 | Política recovery stale: (A) force materialize, (B) failed + log, (C) bump `scheduled_for` — combinações com anti-duplicação | Pendente |
| TD-2 | TTL stale + env vars | Pendente |
| TD-3 | Incluir `queued` na recuperação | Opcional |
| TD-4 | **D1:** quando e como ativar “same day after unlock” (flag/env/coluna) | Pendente |
| TD-5 | Onde invocar `check_daily_limit` (ou equivalente) antes de INSERT do novo chunk pós-destrave | Pendente |
| TD-6 | Escopo do PR: **só worker + limits + tests** (`yes_d3_admin`) **vs** incluir D3 admin no mesmo PR — **decidir antes do merge**; se não, T10 cancelado | Fechado (T10): rota ``POST /api/admin/campaigns/<id>/flush-stale-initial-chunk`` + auditoria ``admin_uazapi_stale_flush_audit`` + CSRF sessão + rate limit Redis |
| TD-7 | Alinhar **`queued`** entre SSOT e busy em `app.py` (divergência / chunk duplicado se `queued` existir no BD) | Fechado (T8): `queued` em `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`; busy e admin sync usam `ANY(%s)` com a constante |
| TD-8 | **Cota diária real:** hoje `continue-initial-chunk` **não** chama `check_daily_limit`; só filtra por `can_create_campaign_today` (sempre `True`) — o 429 “limite diário por instância” (`app.py` ~5974–5978) está **efetivamente morto**. `worker_cadence` também **não** importa `check_daily_limit`. AC-SAME-2 / glossário (5) exigem **implementar** gating com `check_daily_limit` + `plan_limit` (ou helper existente usado pelo sender) nos caminhos de destrave/agendamento, não só documentar | Pendente |
| TD-9 | **`next_valid` obrigatório:** qualquer política que defina `scheduled_for = now + N` (same-day, bump stale) deve calcular o próximo instante que satisfaz **simultaneamente** (i) janela materialize ou `force_send_ids`, (ii) `is_campaign_send_window`, (iii) dias permitidos — ou falhar explicitamente | Pendente |
| TD-10 | **`per_instance_limit` vs `campaigns.daily_limit`:** materialize usa 30 fixo; criação da campanha usa `daily_limit` para lotes — alinhar SSOT (query `daily_limit` no materialize ou helper compartilhado) para cumprir AC-RULE-4 | Pendente |
| TD-11 | **Núcleo único de política** (`assert_send_policy_ok` ou equivalente): unificar chamadas para janela BRT, dia, cota e (se aplicável) janela materialize — evitar cópia em worker vs app vs admin | Pendente |
| TD-12 | **Cota global vs por campanha:** **G2** fechado em T2 — gate duplo (`get_user_daily_limit` + `campaigns.daily_limit`) com contagens `get_sent_today_count` / `get_sent_today_campaign_initial_count`; múltiplas campanhas do mesmo user competem pelo teto global do user **e** cada uma pelo seu `daily_limit` | Implementado (T2) |

---

## Revisão Party Mode 2 — restante da spec (lacunas e riscos)

*Consolidação cruzada (produto / backend / QA) sobre o que ainda não está fechado ou está implicitamente assumido.*

1. **AC-SAME-2 vs código atual:** O spec pede 429/400 quando a cota diária não permite; o fluxo `_continue_initial_chunk_core` não invoca `check_daily_limit` — apenas `can_create_campaign_today`. Até TD-8 ser implementado, os ACs de cota no destrave manual são **aspiracionais**. O materialize marca `failed` se `can_create_campaign_today` for falso (`worker_cadence.py` ~675–682), mas com o helper sempre `True` esse ramo **não corre** hoje.

2. **`queued` em dois lugares:** Bloqueio de “instância ocupada” no continue difere do bloqueio em `schedule_next_initial_chunk` (TD-7). Qualquer recovery que só olhe `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` pode ignorar `queued` e violar intenção do endpoint manual.

3. **Escopo de estágio:** `_materialize_scheduled_stage_sends` cobre `initial`, follow-ups, etc. O spec centra-se em **initial** + `schedule_next_initial_chunk`. Explicitar na implementação se recovery stale aplica-se só a `stage = 'initial'` ou a todos os stages com pasta NULL (implica risco diferente em FU).

4. **D3 / auditoria:** “`admin_user_id` + timestamp” pode exigir coluna ou tabela de auditoria não existente — falta subtarefa de schema/migração no plano de tasks.

5. **Índice / performance:** Query de stale (`scheduled`, folder NULL, `scheduled_for` antigo) em todo tick do worker — avaliar índice composto ou limite de batch por tick para não varrer a tabela inteira.

6. **Concorrência:** Dois ticks marcam o mesmo send como stale + `schedule_next` insere duplicado — transações / `SELECT … FOR UPDATE` ou idempotência por `send_id` no recovery.

7. **OpenAPI:** A task D2 menciona OpenAPI; confirmar se o repositório gera spec OpenAPI ou se “documentar” = docstring da rota + README interno.

---

## Ordem no tick, cota e `force_send_ids` (fechamento F1, F2, F8, F10)

### Ordem em `process_cadence` (F1)

1. **Recovery stale (T7)** — corre **antes** de `_materialize_scheduled_stage_sends(conn)` no mesmo tick. Saídas permitidas por linha: `failed`, `scheduled_for` atualizado para `next_valid`, ou (política A rara) marcar `send_id` para materialização exclusiva **na mesma transação** que exclui o registo da query automática (ex.: commit + `force_send_ids` interno na mesma função — evitar o mesmo `id` ser elegível ao SELECT automático no passo 2).
2. **`_materialize_scheduled_stage_sends(conn)`** — materialização automática habitual.
3. Resto do loop (sync, `schedule_next_initial_chunk`, etc.).

Regra: **nunca** deixar o mesmo `send_id` em estado onde o passo 1 e o 2 possam ambos chamar `create_advanced_campaign` sem exclusão mútua (lock por linha, `SKIP LOCKED`, ou transição de estado intermédia `processing` se no futuro existir).

### Cota para gate de **novo chunk desta campanha** (F2)

O implementador deve expor explicitamente (em código + teste):

- `sent_today_user_initial(user_id)` — já espelhado mentalmente por `check_daily_limit` / `get_sent_today_count`.
- `sent_today_campaign_initial(campaign_id)` — **nova** contagem `COUNT(*)` equivalente à de `check_daily_limit` mas com `AND c.id = %s` no JOIN.

**Política a fechar no TD-12 (uma escolha documentada):**

- **(G1)** Gate só com teto global: `sent_today_user < min(get_user_daily_limit(...), ???)` — *não* aplica `campaign.daily_limit` por campanha.
- **(G2)** Gate duplo: `sent_today_user < get_user_daily_limit(...)` **e** `sent_today_campaign_initial < campaigns.daily_limit`.
- **(G3)** Gate só campanha: raramente desejável se o plano for global.

O spec de implementação deve **nomear a opção** no PR e refletir nos ACs de cota.

### `force_send_ids` e `remaining < -86400` (F8)

Em `worker_cadence`, o ramo `force_set` ignora sends com `remaining < -86400` (~476–477). Recovery que faz **bump** de `scheduled_for` para `next_valid` **não** deve deixar o send com `scheduled_for` há >24h no passado se a intenção for permitir `continue-initial-chunk` com `force_send_ids` — ou alinhar o limite −86400 com a política de stale, ou documentar exceção (“admin requeue” atualiza `scheduled_for` para `utcnow+30s` antes do force).

### TTL stale — fronteira (F10)

Documentar na implementação: critério **`scheduled_for < (now_utc_naive - timedelta(minutes=TTL))`** (estritamente menor), com `now_utc_naive` definido igual à fonte usada no materialize (`datetime.utcnow()` até eventual refactor). “Dia” de cota continua a ser **data BRT** nas queries SQL existentes.

---

## Implementation Plan

### Tasks (ordem sugerida)

- [x] **T1 — Constantes materialize:** Extrair lookback/lookahead da query (~454–458) e do filtro `remaining` (~471–475) para constantes nomeadas partilhadas (ex. `MATERIALIZE_LOOKBACK_MIN`, `MATERIALIZE_LOOKAHEAD_MIN`) e documentar na docstring de `_materialize_scheduled_stage_sends`.
  - File: `worker_cadence.py`
  - Action: Uma fonte de verdade para minutos; SQL `INTERVAL` e Python alinhados.

- [x] **T2 — Produto TD-12 + fórmula TD-8:** Fechar **G1/G2/G3** (secção “Cota para gate”); implementar `sent_today_campaign_initial(campaign_id)` + uso em gate; helper `effective_initial_daily_cap` conforme opção escolhida.
  - Files: `utils/limits.py` (helpers), eventual `utils/campaign_send_policy.py` (novo, preferível a inchamento de `app.py`)
  - Action: Função pura + testes unitários sem DB se possível.
  - **Feito (2026-04-16):** política default **G2** em `utils/campaign_send_policy.py` (`INITIAL_CHUNK_DAILY_QUOTA_POLICY`, `effective_initial_daily_caps`, `initial_chunk_daily_quota_allows`); BD: `get_sent_today_campaign_initial_count` + `check_initial_chunk_daily_quota_for_campaign` em `utils/limits.py`; testes `tests/test_campaign_send_policy.py`.

- [x] **T3 — Núcleo TD-11 `next_valid` (TD-9):** Implementar cálculo do próximo `datetime` UTC naive que satisfaz `is_campaign_send_window` + dias úteis + (opcional) margem para `force_send_ids`; expor para worker e Flask.
  - Files: novo módulo pequeno **ou** `worker_cadence.py` + import em `app.py`
  - Action: Testes com **`unittest.mock.patch` de `datetime`** ou adicionar **`freezegun`** como dependência de **dev** em `requirements.txt` / grupo de testes — o projeto **não** inclui `freezegun` hoje.
  - **Feito (2026-04-16):** `utils/next_valid_uazapi_send.py` — `next_valid_send_utc_naive`, `is_campaign_send_window` (SSOT partilhado); `worker_cadence` importa a janela deste módulo; testes `tests/test_next_valid_uazapi_send.py` (sem freezegun).

- [x] **T4 — TD-10 `per_instance_limit`:** Substituir hardcode 30 em `_materialize_scheduled_stage_sends` (~488) por valor derivado de `campaigns.daily_limit` + nº instâncias (espelhar lógica de `_create_campaign_core` ~5090–5118) via `SELECT` já existente ou JOIN.
  - File: `worker_cadence.py`
  - Action: Incluir `c.daily_limit` no SELECT de sends se ainda não estiver.
  - **Feito (2026-04-16):** `uazapi_initial_chunk_distribution_limits` em `utils/campaign_send_policy.py`; SELECT com `c.daily_limit`; materialize `initial`+Uazapi usa o helper; `_create_campaign_core` importa o mesmo helper (~5116–5118). Testes em `tests/test_campaign_send_policy.py`.

- [x] **T5 — Gating em `_continue_initial_chunk_core`:** Antes do INSERT (~6069+), chamar política TD-11; se cota esgotada → 429/400 com corpo JSON estável (AC-SAME-2); se fora da janela BRT → não inserir ou `scheduled_for = next_valid` conforme decisão.
  - File: `app.py`
  - Action: Remover ou repor ramo morto `can_create_campaign_today` com lógica real (TD-8).
  - **Feito (2026-04-16):** `check_initial_chunk_daily_quota_for_campaign` antes de `cancel_scheduled`; resposta 429 com `code`/`quota_policy`; janela BRT via `is_campaign_send_window` / `next_valid_send_utc_naive` + `MATERIALIZE_LOOKAHEAD_MIN` para `scheduled_for` quando fora da janela.

- [x] **T6 — `schedule_next_initial_chunk`:** Após destrave / same-day (D1): quando gatilho ativo e política OK, usar `target_dt` no mesmo dia BRT em vez de só `_next_initial_send_slot`; aplicar TD-11 ao gravar `scheduled_for`.
  - Files: `worker_cadence.py`, `utils/initial_chunk_schedule_target.py`
  - Action: ``UAZAPI_SAME_DAY_INITIAL_CHUNK_AFTER_UNLOCK`` (default ``0``); ``resolve_initial_chunk_schedule_target`` + cota + ``next_valid_send_utc_naive`` ao gravar ``scheduled_for``; evento JSON ``same_day_after_unlock``.
  - **Feito (2026-04-16):** núcleo em ``utils/initial_chunk_schedule_target.py`` (``cadence_next_*`` extraídos do worker para testes sem ``load_dotenv``).

- [x] **T7 — Recovery stale (TD-1, TD-2, F1, F6, F10):** Query batelada por `status='scheduled'`, `uazapi_folder_id IS NULL`, **`scheduled_for < now_utc - TTL`** (ver secção TTL), `stage='initial'` (fase 1), campanha em `status IN (...)` igual ao worker (~979); política B/C/A conforme ADR; logs JSON Apêndice E.
  - File: `worker_cadence.py` — invocar **antes** de `_materialize_scheduled_stage_sends` conforme “Ordem no tick”.
  - Action: **`UAZAPI_STALE_RECOVERY_ENABLED`** (default `1`, `0` desliga) + **`UAZAPI_STALE_RECOVERY_MAX_PER_TICK`** (cap de linhas); `FOR UPDATE SKIP LOCKED` ou exclusão mútua com materialize; nunca duplicar `create_advanced` no mesmo tick para o mesmo `send_id`.
  - **Feito (2026-04-16):** `_recover_stale_scheduled_initial_uazapi_sends` + env `UAZAPI_STALE_RECOVERY_TTL_MINUTES` (default 90); política bump `next_valid` + margem materialize, senão `failed` (sem apikey / `next_valid` inválido); `FOR UPDATE OF campaign_stage_sends SKIP LOCKED`; testes `tests/test_worker_stale_recovery.py`; `tests/conftest.py` protege `load_dotenv` em pytest contra `.env` inválido.

- [x] **T8 — TD-7 `queued`:** Alinhar `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` com o busy de `app.py` **ou** remover `queued` do busy de continue se nunca persistido; documentar estado pós-`create_advanced`.
  - Files: `utils/limits.py`, `app.py`
  - **Feito (2026-04-16):** `queued` no SSOT; docstring em `limits` + `schedule_next_initial_chunk`; `continue` e `admin_sync_campaigns` usam `status = ANY(%s)` com a constante.

- [x] **T9 — Fora da janela BRT no materialize (~620):** Em vez de `continue` silencioso, opcionalmente re-agendar send com `scheduled_for = next_valid` (política C) ou marcar `failed` com reason (B) — fechar com produto; mínimo: telemetria `skipped_outside_window`.
  - File: `worker_cadence.py`
  - **Feito (2026-04-17):** evento JSON `uazapi_materialize_outside_send_window` com `skipped_outside_window`; bump via `next_valid_send_utc_naive` + `MATERIALIZE_LOOKAHEAD_MIN`; `failed` se janela inválida; testes `tests/test_worker_materialize_outside_brt.py`.

- [x] **T10 — D3 (opcional, F7):** Incluir **só** se TD-6 = “sim no epic”: rota admin + auditoria + rate limit + CSRF; só campanhas `running|pending|completed` salvo `force` explícito; checklist OWASP mínimo (IDOR: `campaign_id` pertence ao tenant; não expor `apikey` em JSON de resposta).
  - File: `app.py` (+ migração se tabela de auditoria). **Se TD-6 = não:** fechar epic sem T10; flush manual continua via SQL / suporte até fase 2.
  - **Feito (2026-04-17):** ``POST /api/admin/campaigns/<id>/flush-stale-initial-chunk``; ``admin_uazapi_stale_flush_audit`` em ``init_db``; CSRF (``X-CSRF-Token`` / body ``csrf_token``, sessão após login); rate limit Redis 15/min/admin; corpo JSON ``dry_run``, ``force``, ``mode`` (``recovery`` \| ``mark_failed``), ``max_rows``; ``worker_cadence._recover_stale_scheduled_initial_uazapi_sends`` estendido (``only_campaign_id``, etc.).

- [x] **T11 — Doc (F):** Docstring `cadence_next_initial_send_slot` (+ referência no worker); artefatos `_bmad-output` listados no Apêndice F. **Feito (2026-04-17).**

### Acceptance Criteria (checklist implementação — espelha Apêndice C)

- [ ] **AC1 (AC-SAME-1):** Dado destrave do último `scheduled` sem pasta para `(campaign_id, instance_id)` e `now_brt` ∈ janela com cota OK, quando `schedule_next` ou continue corre, então novo `scheduled_for` cai no mesmo dia civil BRT com evento `reason: same_day_after_unlock` (ou nome fechado).
- [ ] **AC2 (AC-SAME-2):** Dado cota esgotada segundo TD-8 / opção G1–G3, quando `continue-initial-chunk` ou admin tenta novo chunk, então HTTP 429/400 com mensagem explícita **ou** agendamento D+1 documentado, sem INSERT que viole a política.
- [ ] **AC3 (AC-DUP-1):** Dado `running` com pasta na instância, quando recovery/continue corre, então não há segundo send `initial` ativo concorrente.
- [ ] **AC4 (AC-RULE-1):** Dado recovery/same-day/continue, quando se materializa `initial` Uazapi, então não ocorre `create_advanced_campaign` com `is_campaign_send_window == false` no instante decisório, salvo re-agendamento explícito com `reason` (sem skip silencioso indefinido).
- [ ] **AC5 (AC-RULE-2):** Dado bump num sábado/domingo com envio não permitido, quando o sistema recalcula `scheduled_for`, então cai no próximo dia permitido (semântica `_next_send_datetime` / flags).
- [ ] **AC6 (AC-RULE-3):** Dado opção G1–G3 fechada em T2, quando se valida cota antes de INSERT/materialize, então a decisão coincide com as contagens documentadas (`sent_today_user` e, se G2, `sent_today_campaign_initial`).
- [ ] **AC7 (AC-RULE-4):** Dado `campaign.daily_limit` < teto default de lote, quando o materialize monta o chunk `initial`, então `per_instance_limit` (ou total limit) não excede o SSOT alinhado à criação da campanha (T4).
- [ ] **AC8 (AC-BASE-1):** Dado send `scheduled` sem pasta com idade > TTL, quando recovery corre com flag habilitada, então a campanha não fica bloqueada indefinidamente por `INITIAL_CHUNK_ACTIVE_SEND_STATUSES` sem transição explícita.
- [ ] **AC9 (AC-BASE-2):** Dado `scheduled_for` dentro da janela UTC unificada (pós-T1), quando materialize automático corre, então comportamento equivalente ao baseline (regressão).
- [ ] **AC10 (AC-BASE-3):** Dado sync indica pasta existente para o batch, quando recovery/stale materializa, então não há segunda pasta duplicada (mock Uazapi).
- [ ] **AC11 (AC-WHATIF-1):** Dado limite meia-noite BRT na contagem de cota, quando teste de borda corre, então resultado é determinístico.
- [ ] **AC12 (AC-WHATIF-2):** Dado campanha `draft`/`cancelled`, quando recovery automático corre, então nenhum `campaign_stage_sends` é alterado.
- [ ] **AC13 (F11):** Dado duas instâncias e falha de materialização só na segunda após sucesso na primeira, quando `continue-initial-chunk` retorna, então corpo JSON documenta **quais** `instance_id` / `send_id` criados vs falhos (sem estado ambíguo “success” se metade falhou).
- [ ] **AC14 (F12):** Dado campanha `created_by_admin_id` não nulo, quando gate de cota corre, então usa `campaigns.user_id` (dono) para `user_id` nas contagens — sem bypass inadvertido.

*(Narrativa Given/When/Then: **Apêndice C**.)*

---

## Additional Context

### Dependencies

- Serviço Uazapi (`services/uazapi.py`): `create_advanced_campaign`, `get_status`; falhas de rede/timeouts propagam para `failed` ou retry policy definida em T7.
- PostgreSQL: locks / índice em `(status, uazapi_folder_id, scheduled_for)` ou equivalente para query de stale (avaliar em T7).
- Decisão de produto **TD-12** antes de codificar T2/T5 de forma irreversível.

### Testing Strategy

- **Unitário:** `next_valid` (T3), `effective_initial_daily_cap` (T2), constantes materialize (T1); mocks `UazapiService`.
- **Integração (se harness existir):** DB real ou fixture: stale send + worker tick; continue com cota mockada.
- **Manual:** UAZAPI_DEBUG=1; instância desconectada → reconectada; verificar logs Apêndice E.
- Matriz: janela BRT, sábado, meia-noite, 2 instâncias, `daily_limit` 5 vs plano 30; **AC13** — mock Uazapi a falhar só na segunda instância após sucesso na primeira.

### Notes

- **Risco alto:** Ordem entre recovery stale e `_materialize_scheduled_stage_sends` — evitar dupla criação de pasta; sempre `sync_campaign_stage_sends_before_new_chunk` antes de `create_advanced` onde já existe.
- **Risco:** `process_cadence` materialize global (~944) pode interagir com sends acabados de re-agendar; testes de corrida.
- `check_instance_daily_limit` em `worker_sender.py` não está no loop principal — não contar como implementado até T2 mapear disparo real.

---

## Adversarial Review (2026-04-16)

| ID | Gravidade | Validade | Descrição |
| -- | --------- | -------- | ----------- |
| F1 | Alta | Real | **T7 vs materialize (~944):** a ordem exata (recovery antes vs depois, interação com a mesma linha `scheduled`) não é uma máquina de estados fechada — risco de `create_advanced` duplo ou corrida com `force_send_ids` se o mesmo `send_id` for “curado” e ainda elegível na query automática no mesmo tick. |
| F2 | Alta | Real | **Cota “por campanha” inexistente na BD:** `check_daily_limit` conta todos os `sent` iniciais do `user_id` no dia; o spec propõe `min(plan, campaign.daily_limit)` para *gate* de chunk, mas **não** define query para “quantos envios **desta campanha** hoje” nem como combinar com o teto global — implementador pode codificar a fórmula errada. |
| F3 | Alta | Real | **AC4–7 agregados:** um único checkbox cobre quatro AC-RULE; viola o critério “testável” do workflow (cada AC deveria ser verificável isoladamente no PR). |
| F4 | Média | Real | **Glossário §3** ainda sugere uso de `check_daily_limit` / `worker_sender` como eixo ativo; contradiz o mapa do Passo 2 (sender sem gate no loop) — arrisca decisões erradas em implementação paralela. |
| F5 | Média | Real | **Dependência `freezegun` (T3):** o plano assume testes com tempo congelado sem verificar `requirements.txt` / lockfile — pode bloquear o PR ou empurrar para mocks frágeis. |
| F6 | Média | Real | **TTL sem “circuit breaker”:** recovery com TTL mal calibrado pode marcar `failed` em massa em incidente de relógio ou migração de dados — o spec não exige `UAZAPI_STALE_RECOVERY_ENABLED` ou limite máximo de UPDATEs por tick além de uma frase genérica. |
| F7 | Média | Parcial | **D3 “opcional” sem critério de corte:** TD-6 permanece vago; sem regra, o epic dilata-se (admin + migração + UI) ou fica sub-spec de segurança (IDOR, rate limit por IP). |
| F8 | Média | Real | **`force_send_ids` e `remaining < -86400`:** o ramo forçado ignora sends com mais de 24h no passado; interação com recovery “bump” para `next_valid` não está escrita — pode deixar sends eternamente inelegíveis a `force` e a automática. |
| F9 | Baixa | Real | **Resumo Passo 4:** texto diz “~12 tasks” mas a lista é **T1–T11** (onze) — descuido para auditoria de completude. |
| F10 | Baixa | Real | **Fronteira exata do TTL:** não define se `scheduled_for < now - TTL` usa `<` ou `<=`, nem timezone de corte — regressões off-by-one na meia-noite. |
| F11 | Média | Real | **Falha parcial multi-instância:** `_continue_initial_chunk_core` pode criar pastas para instância A e falhar B; nenhum AC cobre rollback parcial ou estado “metade running”. |
| F12 | Média | Real | **Super-admin / `created_by_admin_id`:** campanhas criadas por admin podem ter limites ou fluxos diferentes; o spec não menciona se o gate de cota usa `user_id` do dono ou exceções. |

*Nenhum achado zero — a revisão assume que ambiguidade residual é risco de produção.*

### Mitigações aplicadas no spec (pós-revisão)

| ID | Mitigação |
| -- | ----------- |
| F1 | Secção **“Ordem no tick”** + reforço em **T7** (antes do materialize, exclusão mútua). |
| F2 | **G1/G2/G3** + `sent_today_campaign_initial` em **T2** e **AC6**. |
| F3 | **AC4–AC7** desagregados; **AC13–AC14** para F11/F12. |
| F4 | **Glossário §3** corrigido. |
| F5 | **T3** — mock ou dev dependency explícita. |
| F6 | **`UAZAPI_STALE_RECOVERY_*`** em **T7**. |
| F7 | **T10** condicionado a **TD-6**. |
| F8 | Secção **`force_send_ids` e −86400**. |
| F9 | Resumo com **11** tasks e **14** ACs. |
| F10 | Critério **`<`** e fonte de tempo na secção TTL. |

---

## Passo 4 — Revisão

**Spec final:** `_bmad-output/implementation-artifacts/tech-spec-recuperacao-scheduled-stale-worker-cadence-uazapi.md` (**status: ready-for-dev**, **stepsCompleted: [1, 2, 3, 4]**).

**Resumo:** **11** tasks (T1–T11), **14** ACs checklist, **6+** ficheiros tocáveis.

**Workflow quick-spec concluído.** Para implementação num contexto limpo, usar o ficheiro final acima (ou prompt `quick-dev` com esse path).

**Select (opcional pós-final):** **[A]** Advanced Elicitation **[P]** Party Mode **[D]** Sair
