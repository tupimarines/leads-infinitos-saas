# Proposta: modelo de envio por mensagem individual (fila intercalada) — rascunho + perguntas

**Estado:** discussão de produto/arquitetura (não implementado).  
**Relacionado:** `doc-sistema-atual-campanhas-uazapi-chunks-cadencia.md`, `doc-mapeamento-codigo-nomenclatura-campanhas-uazapi.md`.

---

## Objetivo de negócio

- Substituir agregação via **`create_advanced_campaign`** (pastas com N mensagens) por **envio individual** (um endpoint por mensagem — contrato a definir no Uazapi ou camada interna).
- **Log explícito** por tentativa, **sucesso/falha em tempo quase real**, retomada clara após pausa/desligar.
- **Fase 1:** apenas **conta admin** (staging antes de produção para todos); feature flag ou gate por `SUPER_ADMIN_EMAILS` / `created_by_admin_id` a definir.

---

## Regras propostas (alinhamento com pedido)

| Tópico | Proposta |
|--------|----------|
| Volume por ciclo | No criar campanha, seletor de quantidade **≤ daily limit** da instância (já próximo do comportamento atual com `daily_limit`). |
| Agendamento | Mantém-se opcional (`scheduled_start` / janela BRT). |
| Ritmo | Manter delay aleatório entre mensagens + **pausa longa** a cada N envios (equivalente semântico a `uazapi_pacing.maybe_long_gap_minutes` / segmentos). |
| Multi-utilizador | **Fila global ou por worker:** intercalar `user1-msg1`, `user2-msg1`, `user3-msg1`, depois (após cooldown de instância ou de fila) `user2-msg2`, … para não “rafar” uma só instância. |
| Retomada | Estado persistido por lead/campanha (último índice ou `campaign_leads` + fila dedicada); recuperação se instância cair ou campanha pausada. |
| UI | Remover fluxos **deprecados** (botões/seções que só servem ao modelo antigo) após migração. |

---

## Impacto técnico (alto nível)

- **`campaign_stage_sends`** pode evoluir para 1 linha por mensagem, ou nova tabela `campaign_message_outbox`; decisão afeta sync e índices.
- **`worker_cadence`** deixaria de depender de `_materialize_scheduled_stage_sends` + `folder_id` ou passaria a usá-lo só para legado durante transição.
- **`utils/sync_uazapi.py`:** hoje acoplado a pastas/listfolders; envio individual exige novo canal de estado (webhook? polling por `message_id`?).
- **Testes:** `test_worker_stale_recovery`, quotas `INITIAL_CHUNK_*` precisam critérios novos (por mensagem vs por pasta).

---

## Perguntas relevantes (bugs, execução, segurança)

1. **Contrato Uazapi:** existe endpoint estável de “send one text/media” com idempotência? Sem isso, risco de duplicar mensagens em retry. - R: Sim, existe.
2. **Rate limit:** a fila intercalada respeita limites **por instância** no WhatsApp e **por token** no Uazapi? Quem é SSOT do throttle? R: Sim, respeita limite diário definido pelo seletor (user) sempre igual ou menor ao instance daily limit; 
3. **Fairness vs SLA:** fila global pode atrasar campanha “urgente”; há prioridade ou apenas FIFO? - R: sempre segue a fila
4. **Cooldown “10 min”:** é por `instance_id`, por número de telefone da instância, ou por `campaign_id`? Conflito com `INITIAL_CHUNK_ACTIVE_SEND_STATUSES`. - R: cool down é randômico definido entre  `delay_min_minutes`, `delay_max_minutes`. 10 minutos foi exemplo;
5. **Mídia:** primeiro passo com ficheiro grande — timeout e armazenamento temporário; mesmo problema que hoje no payload `file` base64? - para mídia, aumentar timeout e definir o endpoint de enviar mídia conforme documentação uazapi
6. **Segurança:** endpoint individual exposto sem gate admin na fase 1 — risco de abuso; confirmar auth + CSRF + rate limit por user. - R: adote as melhores práticas para evitar abusos.
7. **Cadência multi-etapa:** follow-ups continuam a usar outro canal ou unificam-se na mesma fila? R: follow up segue a mesma lógica, com hierarquia inferior: prioridade sempre inicial, depois follow 1, depois follow 2 e assim por diante.
8. **Migração:** campanhas com `uazapi_folder_id` ativo — congelar só leitura, migrar estado, ou suportar dual-write? R: para migração, todo chunk que estiver scheduled, failed ou queued, deve ser pulado, leads ignorados e então iniciar os envios individuais de onde parou. exemplo: daily limit 30. chunk com 10 done. chunk com 8 scheduled. quando o novo mecanismo for para produção, começará a disparar a partir do lead 19, ignorando os 8.
9. **Observabilidade:** métricas (Prometheus/logs estruturados) para cada POST; alertas em taxa de falha.   
10. **n8n / integrações:** jobs que assumem `folder_id` quebram; inventário de consumidores. - R: inventário = listar workflows n8n (e outros) que chamam rotas ou webhooks do app que passam ou leem `folder_id` / `uazapi_folder_id` / `campaign_stage_sends` como “pasta Uazapi”. Ao mudar o modelo, cada job precisa de branch (legado vs outbox) ou desativação; sem inventário há falhas silenciosas em produção.

---

## Glossário: o que é “SSOT do throttle”

**SSOT** = *Single Source of Truth* (uma única fonte de verdade).  
**Throttle** = limitação de ritmo (não enviar mais rápido que X, ou não ultrapassar Y envios por dia).

“Quem é o SSOT do throttle?” pergunta: **onde o sistema decide de forma autoritária** quantos envios são permitidos e em que ritmo — para não haver duas regras a contradizerem-se (ex.: UI diz 50/dia, worker outro teto, Uazapi outro).

**SSOT do throttle (decisão explícita):** não é “Postgres *ou* app” em alternativa — é **Postgres como armazenamento autoritativo** dos números (`campaigns.daily_limit`, limites de plano/instância, contagens do dia em BD) **e** o **código do app (worker)** como único lugar que **interpreta** esses valores antes de cada envio. A UI só grava o que o utilizador escolheu (seletor ≤ teto da instância); depois disso a verdade é a linha na BD. Assim sobrevive a reinício do processo: o worker relê Postgres e mantém-se coerente com `check_daily_limit` / políticas em `utils.limits` e `utils/campaign_send_policy.py`.

---

## Decisões complementares (cooldown, instâncias, tempo real, logs)

| Tópico | Decisão |
|--------|---------|
| Cooldown / pausa entre mensagens | **Sempre aleatório**, definido **na criação da campanha** pelo app (não editável pelo utilizador como valor fixo). Faixa operacional típica **600–900 s** entre envios, com sorteio possível num **pool maior (ex. 500–1000 s)** para humanização. Persistir em BD (ex. `delay_min_minutes` / `delay_max_minutes` em segundos ou colunas dedicadas) para o worker e retries usarem o **mesmo** intervalo sorteado por janela/campanha conforme spec. |
| Várias instâncias | Reutilizar UI **“Instâncias de WhatsApp (Multi-instância)”**: checkboxes + toggle **Rotação** (`rotation_mode` / `campaign_instances`), capacidade exibida (ex. 30 × N). A fila intercalada **respeita** essa seleção e a rotação já definida na criação. |
| Atualização UI admin (sent/failed) | **Apenas polling** ao backend próprio (GET com `since_id` / `updated_after` ou equivalente); intervalo adaptativo opcional. **Sem** SSE/WebSocket da app na dashboard de campanha (decisão 2026). |
| Logs | Apenas **servidor**, armazenamento **protegido**, **acesso limitado** (RBAC, sem expor PII em cliente). |

### Atualização da UI admin — decisão: **polling**

- **Implementação:** HTTP GET periódico ao **teu** API, lendo estado já persistido em Postgres (envios / tentativas / campanha).
- **Parâmetros sugeridos na spec:** cursor temporal ou `since_id`, intervalo base (ex. 2–5 s) com **backoff** quando não há alterações.
- **SSE/WebSocket da aplicação:** fora de âmbito nesta fase; reavaliar só se métricas ou produto o exigirem.

### Polling / WebSocket / SSE — servem para quê? (sucesso/falha)

**Não** são o mecanismo que “recebe” o OK ou erro do WhatsApp/Uazapi no momento do envio.

Fluxo real:

1. **Worker (app)** executa o envio, recebe resposta da API (sucesso/falha), **grava** o resultado em Postgres (ex.: linha na outbox / `campaign_leads` / tabela de tentativas).
2. **Browser (UI admin)** quer **mostrar** esses estados atualizados.
3. **Polling** (decisão do projeto) = o browser, de X em X segundos, faz **HTTP GET** (ex. “estado da campanha desde `t=`”) e o servidor **devolve JSON** já lido da BD. O sucesso/falha **já estava** na BD; o polling só **copia** para o ecrã.

Resumo: **sucesso/falha da execução** fica registada no **backend + Postgres**; o dashboard usa **polling** para refrescar. Sem polling, **reload manual** da página ainda mostraria o estado certo.

### SSE do Uazapi (`GET /sse`) — faz sentido para “evitar polling”?

**Contexto:** o endpoint documentado é **SSE do lado Uazapi**: uma ligação HTTP longa com **token da instância**, subscrição a `events` (`messages`, `chats`, `connection`, `messages_update`, etc.). O fluxo é **Uazapi → cliente** (eventos do WhatsApp / bridge), não “o teu Flask empurra JSON para o browser admin”.

| Pergunta | Resposta curta |
|----------|----------------|
| Substitui **polling da UI admin** ao teu próprio backend? | **Não.** A UI admin usa **REST + polling** à tua BD. O SSE Uazapi não expõe de forma óbvia “linha 7 da campanha X = failed” a menos que correlacionês eventos com IDs de envio. |
| Reduz polling **a outras fontes** (ex. ir buscar mensagens ao Uazapi de X em X s)? | **Sim, onde o teu produto hoje “polia” o Uazapi** para *entrada* (novas mensagens, chats, etiquetas, estado `connection`), um consumidor **no servidor** ligado ao `/sse?events=...` pode substituir parte desse polling — com filtro `events` e `excludeMessages` para ruído. |
| Ajuda com **ack de envio em massa / individual**? | **Só se** a documentação Uazapi garantir que `messages_update` (ou outro tipo) inclui **receipts / estado de mensagens enviadas** pelo teu fluxo e IDs correlacionáveis. A lista que colaste privilegia **recebimento** (`messages`, `history`) e **metadados de conversa**; tratar como **complemento**, não como única prova de envio — mantém resposta síncrona do POST de envio + persistência na BD como SSOT do “tentámos enviar”. |
| Segurança | `EventSource` com `token` na **query string** expõe o token a **logs de proxy, histórico de servidor e referer**. Em produção: **ligar ao SSE a partir do teu worker** (token só em servidor), processar eventos, gravar o que for relevante na BD; **não** expor o token Uazapi no browser. |
| Reconnect | SSE corta; é preciso lógica de **reconnect + gap** (histórico ou cursor) para não perder eventos entre quedas. |

**Conclusão para spec:** **SSE Uazapi** (se usado) é **opcional no backend** para enriquecer tempo real / reduzir polling **ao Uazapi** em domínios de eventos; **dashboard de campanha (sent/failed por lead)** = **Postgres + polling HTTP** à app — **sem** SSE/WebSocket da app nesta fase.

---

## Pacote de regras e decisões — por perspectiva (para colar na spec futura)

### Winston (Arquiteto)

- Uma **fila de trabalho** (tabela ou Redis) com chave de ordenação global; consumidor único ou pool pequeno com **lock por `instance_id`** para respeitar cooldown por instância.
- **Cooldown persistido** na campanha (sorteio na criação); worker lê BD, não recalcula a cada mensagem (evita drift e duplicidade semântica).
- **Rotação:** mapear explicitamente o modelo de dados atual (`campaign_instances` + `rotation_mode`) para a ordem de dequeue entre instâncias da mesma campanha.
- **Idempotência:** chave `(campaign_id, campaign_lead_id, step, attempt_id)` no envio; retries só com mesma chave ou estado `failed` explícito.
- Migração: formalizar a regra dos **chunks a saltar** + “começar no lead 19” como **algoritmo de reconciliação** em doc + testes de propriedade (nunca enviar lead já `sent`).

### John (PM)

- **FIFO único** para fairness; sem fila prioritária na v1.
- **Admin-only** até validação de métricas; critério de saída: taxa de erro + satisfação operacional documentados.
- **UI admin:** atualização de estado de envio **só por polling** (sem SSE/WebSocket da app nesta fase).
- Comunicação ao utilizador: “ritmo aleatório definido pelo sistema” (sem campo livre para cooldown em segundos).

### Murat (QA / risco)

- Casos de teste: retry de rede, **duplo clique** em retomar, instância **Desconectada** no meio do lote, daily limit esgotado a meio do dia BRT.
- Propriedade: **no máximo um** estado terminal `sent` por `(lead, step)` na v1 individual.
- Segurança: rate limit por `user_id` + sessão; auditoria de leitura de logs com PII.
- Observabilidade: evento estruturado por POST (sucesso/falha, `instance_id`, `campaign_id`, latência); alerta se falha > limiar.

### BMad Master (orquestração / entrega)

- Ordem de implementação: **(1)** contrato envio individual + outbox BD **(2)** worker dequeue + throttle SSOT app **(3)** UI admin leitura estado **via polling** **(4)** migração chunk → lead index **(5)** inventário n8n **(6)** deprecações UI.
- Uma **feature flag** por ambiente (`USE_MESSAGE_OUTBOX` ou gate admin) até dual-run estar estável.

---

## Próximo passo sugerido

Spike técnico: 1 instância, 1 campanha admin, fila em memória/Redis com intercalação e persistência mínima; medir latência e erros antes de tocar em `app._create_campaign_core`.
