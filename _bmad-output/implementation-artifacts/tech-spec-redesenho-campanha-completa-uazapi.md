---
title: 'Redesenho do fluxo completo de campanha Uazapi (Inicial + Follow-ups)'
slug: 'redesenho-campanha-completa-uazapi'
created: '2026-03-10'
status: 'implementation-in-progress'
stepsCompleted: ['worker-pre-disparo-deterministico', 'ui-capacidade-multi-instancia', 'progresso-por-instancia-kanban-lista', 'worker-sync-periodico-15min', 'kanban-modal-unificado-stage-campaign', 'schema-unicidade-stage-send-janela']
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Uazapi API', 'JavaScript']
files_to_modify: ['app.py', 'utils/sync_uazapi.py', 'services/uazapi.py', 'templates/campaigns_kanban.html', 'templates/campaigns_new.html', 'templates/campaigns_list.html', 'worker_cadence.py']
code_patterns: ['create_advanced_campaign', 'list_folders log_sucess', 'campaign_leads send_batch', 'campaign_instances uazapi', 'move_campaign_lead']
test_patterns: ['pytest tests/', 'test_*.py', 'manual kanban funnel flow']
depends_on: 'tech-spec-validacao-chat-check-reorganizacao-envio-campanhas'
---

# Tech-Spec: Redesenho do fluxo completo de campanha Uazapi (Inicial + Follow-ups)

**Created:** 2026-03-10

## Overview

### Problema

1. A lista da campanha inicial agora já chega validada, mas o sistema ainda precisa respeitar limite diário por instância para não disparar acima do permitido.
2. Planos com múltiplas instâncias (3 ou 5 números) exigem rotação correta por instância, com rastreabilidade por card de campanha.
3. No fluxo atual, não há separação rígida entre "já recebeu etapa anterior" e "ainda não recebeu", causando risco de avanço indevido para follow-up.
4. Leads movidos manualmente para convertido/perdido no Kanban podem continuar em campanhas pendentes, gerando mensagens fora de contexto.
5. Falta operação padronizada no topo de cada coluna do Kanban para gerar/agendar campanha por etapa com os mesmos controles da campanha avançada.

### Objetivo da solução

1. Redesenhar o funil completo (Inicial -> Follow-up 1 -> Follow-up 2 -> Break-up) com controle por etapa e por instância.
2. Garantir que cada envio respeite o limite diário por instância (30/dia/instância) e escale proporcionalmente ao número de instâncias selecionadas.
3. Manter atualização confiável dos cards via `listfolders` usando `folder_id` e `log_sucess` em polling periódico.
4. Taggear cada lead após envio com etapa e instância para bloquear reenvio indevido e orientar passagem correta para próxima etapa.
5. Permitir remover leads do funil operacional ao mover para convertido/perdido em qualquer etapa.

## Regras de negócio (definitivas)

1. **Limite diário por instância:** cada instância pode disparar até 30 mensagens iniciais por dia.
2. **Campanha multi-instância:** se usuário selecionar N instâncias na rotação, capacidade diária da etapa = `30 * N`.
3. **Estratégia de execução recomendada:** gerar **1 campanha Uazapi por instância** (em vez de uma única campanha com rotação interna), para manter controle claro de `folder_id`, progresso e falhas por instância.
4. **Atualização de progresso:** para cada `folder_id` criado, chamar `listfolders` e ler `log_sucess` para atualizar card de campanha.
5. **Polling de progresso:** executar sincronização a cada 15 minutos (padrão fixo), por `folder_id` ativo.
6. **Progressão entre etapas:** somente leads com tag de envio da etapa atual concluída podem ser elegíveis para próxima etapa.
7. **Bloqueio de duplicidade:** lead que já recebeu `inicial` nunca deve voltar para fila de envio inicial.
8. **Remoção operacional imediata:** ao mover lead para `convertido` ou `perdido`, remover lead das filas futuras de campanha.

## Modelo de tags e estado do lead

### Tags obrigatórias por envio

- `sent_stage`: `inicial` | `follow1` | `follow2` | `breakup`
- `sent_instance_id`: id interno da instância que realizou o envio
- `sent_instance_remote_jid`: `remoteJid`/identificador do número ativo da instância no momento do envio (protege cenário de logout e reutilização da instância)
- `sent_folder_id`: folder da campanha Uazapi daquele envio
- `sent_at`: timestamp de confirmação operacional por sync (momento da chamada `listfolders` que confirmou progresso; não é timestamp individual da mensagem)

### Estado mínimo recomendado em `campaign_leads`

- `current_step` (1..4)
- `status` (`pending`, `sent`, `failed`, `invalid`, `converted`, `lost`)
- `last_sent_stage`
- `last_sent_instance_id`
- `last_sent_instance_remote_jid`
- `last_sent_folder_id`
- `send_batch` (ordem de corte diário para listas grandes)
- `removed_from_funnel` (bool; true ao converter/perder)

## Fluxo funcional ponta a ponta

### 1) Etapa Inicial (lista validada)

1. Usuário extrai lista (ex.: 500 leads) e validação prévia reduz para válidos (ex.: 470).
2. A primeira geração da campanha inicial ocorre na página de criação de campanha (fora do Kanban), com modal/formulário que permite:
   - Data de início
   - Horário
   - 5 variações de mensagem
   - Intervalo de envios (delay min/max)
   - Ação: gerar agora ou agendar
   - Seletores da campanha avançada Uazapi (mesma experiência da aba de criação)
3. Usuário escolhe 1..N instâncias para rotação operacional.
4. Backend distribui leads elegíveis por instância e cria campanhas separadas (1 folder por instância).
5. Cada lead enviado recebe tags `sent_stage=inicial` + `sent_instance_id`.

### 2) Sync do card de campanha

1. Job periódico consulta `listfolders` por `folder_id` de cada instância/campanha da etapa.
2. Para cada folder:
   - atualizar contadores no card (`log_sucess`, `log_failed`, `log_total`)
   - persistir progresso por instância
3. Card da campanha mostra visão agregada e detalhamento por instância:
   - Instância A: 30/30
   - Instância B: 28/30
   - Total etapa: 58/60

### 3) Avanço para Follow-up 1

1. Após conclusão da etapa inicial (ou ao atingir critério mínimo configurado), apenas leads com `sent_stage=inicial` entram na elegibilidade de Follow-up 1.
2. Leads sem envio inicial não entram em Follow-up 1.
3. Leads convertidos/perdidos ficam excluídos.

### 4) Follow-up 1 -> Follow-up 2 -> Break-up

1. A partir do Kanban, cada etapa de follow-up usa:
   - botão por coluna
   - modal com agendamento e 5 variações
   - seleção de instâncias
   - criação por instância
   - polling via `listfolders`
2. A cada envio, atualizar tags de etapa e instância (`follow1`, `follow2`, `breakup`).

### 5) Intervenção manual no Kanban

1. Se o usuário mover lead para `convertido` ou `perdido` em qualquer etapa:
   - marcar `removed_from_funnel=true`
   - remover lead das filas de campanhas futuras
   - impedir inclusão em novos lotes de envio
2. Se existir lista CSV transitória por etapa, remover a linha correspondente; preferível migrar para seleção 100% por query no banco (fonte única de verdade).

## Estratégia para listas grandes (500+ leads)

1. Após validação, atribuir `send_batch` determinístico aos leads válidos.
2. Exemplo com 2 instâncias selecionadas:
   - capacidade do dia = 60 (30 por instância)
   - dia 1 envia batchs equivalentes aos primeiros 60 elegíveis
   - dia 2 continua dos próximos elegíveis não enviados
3. A passagem entre etapas considera sempre:
   - etapa anterior enviada (tag)
   - não convertido/perdido
   - não removido do funil

## Mudanças de UI (Kanban)

1. Exibir botão **Gerar Campanha** acima das colunas:
   - Follow-up 1
   - Follow-up 2
   - Break-up
   - (Inicial não terá botão no Kanban; inicial nasce na tela de criação de campanha)
2. Ao clicar, abrir modal único reutilizável com:
   - data
   - horário
   - 5 variações de mensagem
   - intervalo de envios
   - gerar agora ou agendar
   - mesmos seletores da campanha avançada Uazapi
3. Card da campanha por etapa deve mostrar:
   - total planejado
   - total enviado
   - progresso por instância
   - última atualização

## Regra de transição entre etapas

1. A etapa seguinte só é liberada quando a etapa anterior estiver `done` em **todas** as instâncias participantes.
2. Não usar threshold parcial para avanço automático entre etapas.
3. Leads convertidos/perdidos permanecem excluídos da próxima etapa mesmo com etapa anterior concluída.

## Arquitetura técnica recomendada

1. **Criação de campanha por etapa:**
   - endpoint dedicado `POST /api/campaigns/<id>/stage-campaign`
   - payload inclui etapa, agendamento, variações e instâncias selecionadas
2. **Persistência de folders por instância:**
   - nova tabela de tracking (recomendado) para múltiplos folders por etapa
3. **Sync assíncrono:**
   - worker periódico para `listfolders` por folder ativo
4. **Idempotência:**
   - evitar criar campanha duplicada para mesma etapa/instância/janela
   - para campanhas agendadas, salvar intenção de envio e montar payload final somente em janela curta pré-disparo
5. **Desacoplamento de CSV:**
   - usar DB como verdade para elegibilidade, mantendo CSV apenas como input inicial

### Estratégia determinística para agendamentos (anti-falhas)

1. **Não pré-materializar lote final muito cedo.** Salvar apenas a intenção (`campaign_stage_sends` com parâmetros do envio).
2. **Janela de confirmação pré-disparo (ex.: 2-5 min antes):**
   - recalcular elegíveis no banco
   - excluir `converted/lost/removed_from_funnel`
   - só então montar payload definitivo e criar folder Uazapi
3. **Se o lead mudar de estado após folder criado:** respeitar bloqueio local para próximas etapas; não reencaminhar.
4. Esse modelo é mais determinístico que tentar "editar lote já montado", pois reduz dependência de remoção remota no provedor.

## Estrutura de dados sugerida (nova tabela)

Tabela sugerida: `campaign_stage_sends`

- `id`
- `campaign_id`
- `stage` (`initial`, `follow1`, `follow2`, `breakup`)
- `instance_id`
- `uazapi_folder_id`
- `scheduled_for`
- `status` (`scheduled`, `running`, `done`, `partial`, `failed`)
- `planned_count`
- `success_count`
- `failed_count`
- `last_sync_at`
- `created_at`
- `updated_at`

Objetivo: permitir que o card exiba progresso real por instância sem ambiguidade.

## Endpoint de sync recomendado

`POST /api/campaigns/<id>/sync-stage-status` (interno/worker)

Passos:
1. buscar sends ativos em `campaign_stage_sends`
2. para cada send, chamar `listfolders` e localizar `uazapi_folder_id`
3. atualizar `success_count`, `failed_count`, `status`, `last_sync_at`
4. refletir no card do kanban/lista de campanhas

## Critérios de aceite

- [ ] **AC1:** campanha inicial com 2 instâncias dispara no máximo 60 leads/dia (30 por instância).
- [ ] **AC2:** card exibe progresso separado por instância com base em `log_sucess` do folder correspondente.
- [ ] **AC3:** lead só entra no Follow-up 1 se tiver tag de envio da etapa inicial.
- [ ] **AC4:** lead convertido/perdido é removido das filas e não recebe novas mensagens do funil.
- [ ] **AC5:** botão "Gerar Campanha" existe no Kanban para Follow-up 1, Follow-up 2 e Break-up; etapa Inicial é criada na tela de criação de campanha.
- [ ] **AC6:** cada envio grava tags de etapa e instância no lead.
- [ ] **AC7:** sync periódico de 15 min atualiza card sem intervenção manual.
- [ ] **AC8:** follow1/follow2/breakup seguem a mesma regra de elegibilidade e exclusão por estado.
- [ ] **AC9:** transição para etapa seguinte só ocorre quando todas as instâncias da etapa anterior estiverem `done`.
- [ ] **AC10:** `sent_instance_remote_jid` é persistido junto do envio para diferenciar número ativo mesmo com reaproveitamento da instância.

## Riscos e mitigação

1. **Rotação antiga incompatível com batch do servidor Uazapi**
   - Mitigação: criar folder por instância e abandonar rotação implícita antiga.
2. **Desalinhamento entre CSV e estado real do Kanban**
   - Mitigação: tornar DB a fonte principal para elegibilidade.
3. **Poll excessivo em alto volume**
   - Mitigação: backoff incremental + sincronizar apenas folders ativos.
4. **Corrida entre edição manual do Kanban e envio agendado**
   - Mitigação: revalidar elegibilidade imediatamente antes de gerar payload final.

## Fases de implementação

1. **Fase 1 (core):** modelo de tracking por instância + botão/modal por etapa + criação de campanhas por instância.
2. **Fase 2 (sync):** polling `listfolders` e atualização de cards em tempo quase real.
3. **Fase 3 (governança):** idempotência forte, observabilidade e métricas de funil por etapa/instância.

## Checklist de implementação por arquivo

### `app.py`

- [x] Adicionar migração `ALTER TABLE campaign_leads` para colunas explícitas: `last_sent_stage`, `last_sent_instance_id`, `last_sent_instance_remote_jid`, `last_sent_folder_id`, `sent_at`, `removed_from_funnel`.
- [x] Criar endpoint `POST /api/campaigns/<id>/stage-campaign` para Follow-up 1/2/Break-up com payload de agendamento, variações e instâncias.
- [x] Reutilizar fluxo da criação inicial em `campaigns_new` para a etapa `initial` (sem botão de inicial no Kanban).
- [x] Validar limite diário por instância (`30`) e calcular capacidade total por etapa (`30 * N instâncias`).
- [x] Implementar trava de transição: só liberar próxima etapa quando todos os sends da etapa anterior estiverem `done`.
- [x] No `move_campaign_lead`, ao mover para `converted/lost`, marcar `removed_from_funnel=true` e retirar elegibilidade futura.
- [x] Garantir idempotência de criação por `(campaign_id, stage, instance_id, janela_agendamento)`.

### `services/uazapi.py`

- [x] Garantir helper para criação de campanha avançada por instância (1 folder por instância).
- [x] Garantir helper para `list_folders` sem dependência de filtro por status.
- [x] Expor parser de retorno para capturar `folder_id`, `log_sucess`, `log_failed`, `log_total`, `status`.

### `utils/sync_uazapi.py`

- [x] Implementar sync periódico a cada 15 minutos para folders ativos.
- [x] Para cada `campaign_stage_sends.uazapi_folder_id`, consultar `listfolders` e atualizar status/contadores.
- [x] Atualizar lead tags por etapa com colunas explícitas (`last_sent_*`) e `sent_at` no momento da confirmação por sync.
- [x] Persistir `last_sent_instance_remote_jid` usando o identificador da instância efetiva do envio.
- [x] Marcar `status=done` em send quando folder finalizar; manter `partial`/`failed` quando aplicável.

### `worker_cadence.py`

- [x] Incluir job agendado de sync (15 min) para chamar rotina de atualização de folders ativos.
- [x] Incluir job pré-disparo (janela 2-5 min) para campanhas agendadas: recalcular elegíveis e só então materializar payload/folder.
- [x] Garantir que leads `converted/lost/removed_from_funnel` sejam excluídos na janela pré-disparo.

### `templates/campaigns_new.html`

- [x] Ajustar UI da criação inicial para conter todos os parâmetros do novo padrão: data, hora, 5 variações, intervalo, gerar/agendar e seletores avançados Uazapi.
- [x] Exibir claramente instâncias selecionadas e capacidade diária total prevista.

### `templates/campaigns_kanban.html`

- [x] Exibir botão **Gerar Campanha** apenas em `Follow-up 1`, `Follow-up 2`, `Break-up`.
- [x] Criar modal único reutilizável para geração/agendamento por etapa com os mesmos seletores avançados.
- [x] Exibir card com progresso agregado e detalhado por instância (success/failed/total + última atualização).
- [x] Bloquear ação de geração quando etapa anterior ainda não estiver `done` em todas as instâncias.

### `templates/campaigns_list.html`

- [x] Exibir resumo de execução por etapa/instância (status e progresso) para rastreabilidade operacional.
- [x] Exibir indicador de última sincronização (`last_sync_at`) da campanha.

### Nova tabela de tracking (`campaign_stage_sends`)

- [x] Criar DDL para tabela com campos: `campaign_id`, `stage`, `instance_id`, `uazapi_folder_id`, `scheduled_for`, `status`, `planned_count`, `success_count`, `failed_count`, `last_sync_at`, timestamps.
- [x] Criar índices por `(campaign_id, stage)`, `uazapi_folder_id`, `status`, `scheduled_for`.
- [x] Definir unicidade para evitar duplicidade por instância/etapa/janela.

### Testes (`tests/`)

- [ ] Teste de limite diário por instância e capacidade total multi-instância.
- [ ] Teste de bloqueio de transição sem `done` completo da etapa anterior.
- [ ] Teste de remoção de elegibilidade ao mover lead para `converted/lost`.
- [ ] Teste de persistência de `last_sent_instance_remote_jid` e `sent_at` via sync.
- [ ] Teste de job pré-disparo recalculando elegíveis e evitando envio de leads removidos após agendamento.

## Decisões fechadas

1. **Transição de etapa:** exigir `status=done` em todas as instâncias da etapa anterior.
2. **Polling padrão:** 15 minutos.
3. **Agendamento robusto:** adotar confirmação de elegibilidade em janela curta pré-disparo (2-5 min) e criar folder apenas nesse momento; evita inconsistência de lote antigo.
4. **Modelo de dados:** colunas explícitas no lead, com status por fase (`last_sent_stage` + campos explícitos de instância/folder/remote_jid/timestamp).

## Handoff para próximo chat

### O que já foi feito

1. Backend de etapa por instância (`stage-campaign`) com trava de etapa anterior `done`, limite `30 * N`, e idempotência por janela de agendamento.
2. Modelo de dados com `campaign_stage_sends` + colunas de tracking em `campaign_leads` e persistência de `remote_jid`.
3. Sync priorizando `campaign_stage_sends`, atualizando `success/failed/status/last_sync_at` e tags `last_sent_*`/`sent_at`.
4. Worker com pré-disparo determinístico (janela 2-5 min): recalcula elegíveis e exclui `converted/lost/removed_from_funnel` antes de criar folder.
5. UI:
   - `campaigns_new`: clareza de multi-instância e capacidade total (`30 x N`).
   - `campaigns_kanban`: botão gerar apenas em FU1/FU2/Break-up, progresso por etapa/instância, bloqueio visual quando etapa anterior não está liberada.
   - `campaigns_list`: resumo por etapa/instância e `last_sync_at`.

### Pendências objetivas

1. Executar e registrar testes de regressão da seção `tests/` (ambiente atual sem `pytest` instalado).
