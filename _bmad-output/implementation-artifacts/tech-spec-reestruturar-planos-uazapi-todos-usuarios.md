---
title: 'Reestruturar planos e Uazapi para todos os usuários'
slug: 'reestruturar-planos-uazapi-todos-usuarios'
created: '2026-03-12'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'Flask-Login', 'PostgreSQL (psycopg2)', 'Redis + RQ', 'Jinja2', 'Requests', 'Pytest/Unittest']
files_to_modify: ['app.py', 'worker_sender.py', 'worker_cadence.py', 'utils/limits.py', 'services/uazapi.py', 'templates/account.html', 'templates/admin/users.html', 'templates/whatsapp_config.html', 'tests/test_sender_mock.py']
code_patterns: ['Monolito Flask com regras centrais em app.py', 'Licença ativa derivada de licenses com regra por license_type', 'Instâncias multi-tenant em instances com api_provider (megaapi/uazapi)', 'Worker sender usa limites e seleção por instância/provedor', 'Worker cadence reutiliza check_daily_limit global e hoje limita follow-up', 'Templates admin/account com comportamento divergente entre superadmin e usuário comum']
test_patterns: ['tests/ com mistura de unittest e pytest', 'test_sender_mock.py cobre sender e limite diário', 'test_sync_uazapi.py cobre normalização/sync Uazapi', 'scripts de webhook em tests/test_webhook*.py e tests/test_hubla_webhook.py']
---

# Tech-Spec: Reestruturar planos e Uazapi para todos os usuários

**Created:** 2026-03-12

## Overview

### Problem Statement

A base atual de planos e limites ainda contém regras legadas (semestral/anual), limites de disparo inconsistentes por plano, e cobertura Uazapi parcialmente concentrada em fluxos de superadmin. Isso dificulta padronização comercial, escalabilidade e a transição completa para Uazapi, além de criar lacunas de UX/validação para gestão de múltiplas instâncias por usuário.

### Solution

Padronizar regras de planos (Starter/Pro/Scale/Infinite), aplicar limite diário unificado de 30 disparos iniciais por instância (com exceção configurável para Infinite em "Minha Conta"), permitir follow-ups/break-up sem consumo de cota diária, expandir Uazapi para todos os usuários com criação sempre via Uazapi, e manter MegaAPI apenas como compatibilidade temporária desacoplada até descontinuação.

### Scope

**In Scope:**
1. Remover regras legadas ligadas a planos semestral/anual do fluxo ativo de negócio
2. Definir planos com limites de instâncias: Starter 1, Pro 2, Scale 4, Infinite 20
3. Definir extrações mensais: Starter 1000, Pro 2000, Scale 4000, Infinite 10000
4. Aplicar limite diário padrão de 30 disparos iniciais por instância para todos os planos
5. Para Infinite, adicionar em "Minha Conta" seletor de envios diários por instância (10, 20, 30, 40, 50)
6. Contabilizar no limite diário apenas disparo inicial; follow 1/follow 2/break-up livres
7. Manter follow-up padrão em 4 rodadas (inicial, follow 1, follow 2, break-up)
8. Adicionar botão "Adicionar instância" no modal de usuário no admin, sempre criando via Uazapi
9. Bloquear criação de instâncias acima do limite do plano no backend e informar no frontend: "Limite de instâncias atingido. Contate o suporte para contratar instâncias adicionais"
10. Expandir funcionalidades Uazapi para todos os usuários, preservando operação MegaAPI temporária em paralelo
11. Garantir independência de infraestrutura Uazapi em relação à MegaAPI (serviços, regras e integração desacoplados)

**Out of Scope:**
1. Descontinuação definitiva da MegaAPI nesta entrega
2. Mudanças de pricing/comercial além dos limites e capacidades definidos
3. Customização avançada de múltiplas estratégias de follow-up por plano nesta fase

## Context for Development

### Codebase Patterns

- `app.py` concentra schema, migrações e regras de negócio de plano/licença; hoje ainda mantém `license_type` com `starter/pro/scale/semestral/anual/infinite`, mas a regra alvo elimina `semestral/anual` sem camada de compatibilidade
- `License.daily_limit` está mapeado para 10/20/30/50 e `monthly_extraction_limit` retorna 10000 para infinite e 2000 para os demais; precisa migrar para matriz nova 1000/2000/4000/10000 e limite diário por instância
- Fluxo de criação de instância em `/api/whatsapp/init` ainda diferencia superadmin (Uazapi) e usuários comuns (MegaAPI), além de bloquear usuário comum para apenas 1 instância por regra fixa
- `worker_sender.py` calcula limite diário com lógica própria (10/20/30 para não-superadmin) e aplica limite por instância apenas para superadmin; seleção de provider também está acoplada a superadmin
- `worker_cadence.py` chama `check_daily_limit` antes de follow-up, o que conflita com nova regra de follow-up e break-up livres
- `utils/limits.py` centraliza helpers de limite, porém ainda usa regra antiga de limite por plano e contagem baseada em `campaign_leads.status = 'sent'` sem separar estágio inicial vs follow-up
- Controle de progresso/consumo em campanhas Uazapi já usa sincronização com API externa (`/sender/listfolders` e `/sender/listmessages`) em trechos do fluxo Kanban/cadência; padrão alvo: `listfolders` a cada 10 minutos durante campanha ativa (por `folder_id`) para atualizar `log_sucess`, com fallback em `listmessages` para conferir envios individuais/valores
- `templates/admin/users.html` possui modal de detalhes com plano/senha/exclusão e mostra somente uma instância; não há ação dedicada de "Adicionar instância" no modal
- `templates/account.html` mostra "Limite Diário: {{ license.daily_limit }} envios" e não possui seletor de envios por instância para plano infinite
- `templates/whatsapp_config.html` renderiza visão multi-instância apenas para superadmin; usuários comuns seguem fluxo single-instance
- `services/uazapi.py` já possui métodos para instância, envio e campanhas, podendo sustentar expansão para todos os usuários sem depender da MegaAPI

### Files to Reference

| File | Purpose |
| ---- | ------- |

| `app.py` | `init_db` (CHECK de `license_type`), classe `License`, `/account`, `/whatsapp`, `/api/whatsapp/init`, rotas admin de usuário/licença |
| `worker_sender.py` | Cálculo e enforcement de limite diário, seleção de instância/provider, contagem por instância |
| `worker_cadence.py` | Gate de limite diário para follow-up (ponto a remover para deixar follow-up livre) |
| `utils/limits.py` | Helpers de limite diário e capacidade por instância/campanha |
| `services/uazapi.py` | Camada Uazapi reutilizável para criação de instância/envio/status/delete |
| `utils/sync_uazapi.py` | Sincronização de status/quantidade com `listfolders` e `listmessages` para uso e transição de etapas |
| `templates/admin/users.html` | Modal de detalhes do usuário, dropdown de plano e área para botão "Adicionar instância" |
| `templates/account.html` | Exibição de plano e área para incluir seletor de envio diário por instância no Infinite |
| `templates/whatsapp_config.html` | UI de gerenciamento de instâncias e mensagens de erro/sucesso no frontend |
| `tests/test_sender_mock.py` | Base para ajustar testes de limites diários |
| `tests/test_sync_uazapi.py` | Base de testes utilitários Uazapi já existente |

### Technical Decisions

1. Normalizar planos ativos para `starter`, `pro`, `scale`, `infinite` e remover `semestral/anual` sem compatibilização de regra ativa
2. Matriz de limites oficial:
   - Instâncias: Starter 1, Pro 2, Scale 4, Infinite 20
   - Extrações/mês: Starter 1000, Pro 2000, Scale 4000, Infinite 10000
3. Limite diário de disparo inicial por instância:
   - Padrão: 30 para todos os planos
   - Exceção Infinite: por instância, usuário escolhe 10/20/30/40/50 em "Minha Conta" (incrementos de 10)
4. Follow-up (follow1/follow2/break-up) não consome limite diário, logo `worker_cadence.py` não deve bloquear por `check_daily_limit`
5. Criação de instância via admin modal será sempre Uazapi e sujeita ao cap do plano do usuário alvo
6. Bloqueio de cap de instâncias deve ser aplicado em backend e refletido no frontend com a mensagem:
   `"Limite de instâncias atingido. Contate o suporte para contratar instâncias adicionais"`
7. Uazapi deve ser habilitada para todos os usuários, replicando o padrão de source of truth já usado no superadmin
8. A regra de plano/licença deve ser centralizada em helpers compartilhados para evitar divergência entre `app.py`, `worker_sender.py` e `utils/limits.py`
9. Definir um `PlanPolicy` central (ou equivalente) como fonte única de: limite de instâncias, limite mensal de extrações, limite diário por instância e opções configuráveis do Infinite
10. Ativar expansão Uazapi para todos por feature flag de rollout seguro (ex.: `UAZAPI_FOR_ALL_USERS_ENABLED`) com estratégia de rollback
11. Consolidar sincronização via Uazapi `listfolders` + `listmessages` como fonte de verdade para:
    - contabilização de envios (uso real)
    - reconciliação de status local x API
    - direcionamento para próxima etapa do funil/cadência
12. Durante campanha ativa Uazapi, executar `listfolders` por `folder_id` a cada 10 minutos para atualização de `log_sucess`; em divergência/falha, usar fallback `listmessages` para validar envios individuais
13. Aplicar trava transacional no backend para cap de instâncias por plano (evitar race condition em criação simultânea)
14. Manter worker principal legado da MegaAPI como padrão; só aplicar fluxo Uazapi no worker quando houver processo/campanha Uazapi ativa

## Implementation Plan

### Tasks

- [x] Task 1: Criar política central de planos e limites
  - File: `utils/limits.py`
  - Action: Introduzir estrutura central (`PLAN_POLICY`/`PlanPolicy`) com limites por plano: instâncias, extrações mensais e cota diária padrão por instância.
  - Notes: Deve suportar exceção do Infinite com seletor 10/20/30/40/50; manter função de fallback para dados legados.

- [x] Task 2: Migrar regras de licença para remover legado ativo `semestral/anual`
  - File: `app.py`
  - Action: Atualizar `init_db` para CHECK de `license_type` apenas com `starter/pro/scale/infinite` e remover aceitação de tipos legados nas validações ativas.
  - Notes: Aplicar migração idempotente com sequência segura de alteração de constraint.

- [x] Task 3: Atualizar mapeamento de limite diário e extração mensal da classe `License`
  - File: `app.py`
  - Action: Ajustar `License.daily_limit` para refletir limite base por instância (30, com exceção Infinite configurável em conta) e `monthly_extraction_limit` para 1000/2000/4000/10000.
  - Notes: Este campo será usado para UI; enforcement real por instância fica nos workers/helpers.

- [x] Task 4: Criar persistência da configuração diária por instância do Infinite
  - File: `app.py`
  - Action: Adicionar persistência por instância (não por conta) para `daily_sends_per_instance` e rota de atualização segura.
  - Notes: Opções permitidas somente 10, 20, 30, 40, 50.

- [x] Task 5: Expor seletor Infinite em “Minha Conta”
  - File: `templates/account.html`
  - Action: Inserir menu para plano Infinite com opções 10/20/30/40/50 e feedback de salvamento.
  - Notes: Mostrar seletor apenas quando plano ativo for `infinite`; demais planos exibem cota fixa de 30 por instância.

- [x] Task 6: Garantir limites de instância por plano em endpoint de criação
  - File: `app.py`
  - Action: Antes de criar instância (rotas admin e usuário), validar quantidade atual de instâncias contra limite do plano.
  - Notes: Em bloqueio, retornar erro com mensagem padrão: "Limite de instâncias atingido. Contate o suporte para contratar instâncias adicionais". Implementar trava transacional para evitar corrida.

- [x] Task 7: Tornar criação de instância sempre Uazapi no fluxo admin
  - File: `app.py`
  - Action: Ajustar criação no modal admin para sempre usar `UazapiService.create_instance`, removendo caminho de criação direta MegaAPI nesse fluxo.
  - Notes: Persistir `api_provider='uazapi'` ao salvar instância.

- [x] Task 8: Adicionar botão “Adicionar instância” no modal de detalhes do usuário
  - File: `templates/admin/users.html`
  - Action: Inserir ação explícita no modal, com chamada API para criar instância Uazapi para o usuário selecionado.
  - Notes: Exibir erro de cap no próprio modal, sem depender de reload total.

- [x] Task 9: Exibir múltiplas instâncias no detalhe do usuário admin
  - File: `app.py`
  - Action: Atualizar endpoint `/admin/users/<id>/details` para retornar lista de instâncias (não apenas a mais recente).
  - Notes: Incluir status/provider para facilitar verificação no frontend.

- [x] Task 10: Atualizar UI admin para status/ações por instância
  - File: `templates/admin/users.html`
  - Action: Renderizar lista de instâncias com status e ações de verificação para cada instância.
  - Notes: Manter compatibilidade visual com modal atual.

- [x] Task 11: Habilitar Uazapi para todos com flag de rollout
  - File: `app.py`
  - Action: Aplicar feature flag (`UAZAPI_FOR_ALL_USERS_ENABLED`) nos fluxos de criação/uso para usuários comuns.
  - Notes: Quando desligada, manter fluxo legado; quando ligada, habilitar Uazapi para fluxos elegíveis sem forçar mudança imediata no worker legado.

- [x] Task 12: Desacoplar seleção de provider da lógica “superadmin-only”
  - File: `worker_sender.py`
  - Action: Refatorar seleção de instância/provider para usar `api_provider` e configuração de rollout, não email superadmin.
  - Notes: Preservar MegaAPI como padrão do worker e ativar trilha Uazapi somente quando houver processo/campanha Uazapi ativa.

- [x] Task 13: Aplicar limite diário por instância para todos os planos
  - File: `worker_sender.py`
  - Action: Substituir lógica atual de limite global por checagem por instância com base em política central.
  - Notes: Para Infinite, usar valor configurado em conta; para demais, 30.

- [x] Task 14: Contabilizar apenas disparo inicial no consumo diário
  - File: `utils/limits.py`
  - Action: Ajustar queries/funções para contabilizar apenas envios de estágio inicial (não follow1/follow2/breakup).
  - Notes: Usar campos de estágio (`last_sent_stage`, `current_step`, ou equivalente) para distinção.

- [x] Task 15: Remover bloqueio de limite diário do worker de cadência
  - File: `worker_cadence.py`
  - Action: Retirar gate que impede follow-up por `check_daily_limit`.
  - Notes: Follow-ups e break-up devem permanecer livres de cota diária.

- [x] Task 16: Formalizar sincronização Uazapi como source of truth de uso
  - File: `utils/sync_uazapi.py`
  - Action: Consolidar leitura de `/sender/listfolders` (a cada 10 min em campanha ativa por `folder_id`) e `/sender/listmessages` para reconciliação de contagem/status.
  - Notes: Atualizar `log_sucess` via listfolders e usar listmessages como fallback para conferência individual.

- [x] Task 17: Integrar sincronização Uazapi ao direcionamento de próxima etapa
  - File: `worker_cadence.py`
  - Action: Usar resultado de sync para avançar estágio do lead com base no status real (Sent/Failed/Scheduled) da API.
  - Notes: Evitar avanço de etapa apenas por estado local não reconciliado.

- [x] Task 18: Integrar sincronização Uazapi ao controle de consumo diário
  - File: `worker_sender.py`
  - Action: Utilizar dados reconciliados de envio para evitar dupla contagem e corrigir divergências local x API.
  - Notes: Priorizar idempotência e consistência em reprocessamento.

- [x] Task 19: Ajustar telas de WhatsApp para não depender de perfil superadmin
  - File: `templates/whatsapp_config.html`
  - Action: Preparar experiência multi-instância para usuários elegíveis por plano, sem bifurcação rígida superadmin vs comum.
  - Notes: Respeitar limite por plano e mensagens de bloqueio.

- [x] Task 20: Atualizar validação de extração mensal no fluxo de scraping
  - File: `app.py`
  - Action: Aplicar nova matriz de extrações mensais por plano no trecho de validação de ciclo.
  - Notes: Garantir mensagens de erro coerentes com o novo limite do plano.

- [x] Task 21: Atualizar criação manual de licença no admin
  - File: `templates/admin/users.html`
  - Action: Revisar opções de plano e descrições para refletir nova política (sem menções legadas).
  - Notes: Exibir informações de instâncias e extrações alinhadas com a matriz oficial.

- [x] Task 22: Atualizar endpoint de criação de licença manual
  - File: `app.py`
  - Action: Validar `license_type` apenas nos quatro planos ativos.
  - Notes: Em tentativa inválida, retornar erro claro e não criar licença.

- [x] Task 23: Cobrir nova política com testes de sender/limits
  - File: `tests/test_sender_mock.py`
  - Action: Adicionar casos para limite diário por instância, exceção Infinite configurável e não consumo por follow-up.
  - Notes: Incluir cenários de bloqueio no cap de instâncias.

- [x] Task 24: Cobrir sincronização Uazapi de contagem/estágio
  - File: `tests/test_sync_uazapi.py`
  - Action: Adicionar cenários de reconciliação por `listfolders`/`listmessages` e impacto no avanço de etapa.
  - Notes: Garantir comportamento determinístico para estados conflitantes.

- [x] Task 25: Documentar rollout e rollback operacional
  - File: `docs/` (novo arquivo de operação de rollout)
  - Action: Criar guia de ativação da flag global, monitoramento e rollback seguro.
  - Notes: Incluir checklist pós-deploy e sinais de regressão.

### Acceptance Criteria

- [ ] AC 1: Given usuário com plano Starter ativo, when tentar criar segunda instância, then backend bloqueia e frontend exibe "Limite de instâncias atingido. Contate o suporte para contratar instâncias adicionais".
- [ ] AC 2: Given usuário com plano Pro ativo, when criar duas instâncias e tentar terceira, then criação é bloqueada com a mesma mensagem padrão.
- [ ] AC 3: Given usuário com plano Scale ativo, when atingir 4 instâncias, then nenhuma nova instância é criada até reduzir quantidade.
- [ ] AC 4: Given usuário com plano Infinite ativo, when atingir 20 instâncias, then tentativa adicional é bloqueada no backend.
- [ ] AC 5: Given usuário Infinite em "Minha Conta", when selecionar 40 envios/dia por instância e salvar, then valor persiste por instância e passa a reger o limite diário daquela instância.
- [ ] AC 6: Given plano não Infinite, when acessar "Minha Conta", then limite diário por instância aparece fixo em 30 sem seletor configurável.
- [ ] AC 7: Given qualquer plano ativo, when worker processa disparos iniciais, then limite diário é aplicado por instância com valor correto da política.
- [ ] AC 8: Given lead em follow1/follow2/breakup, when worker de cadência envia mensagem, then envio não consome cota diária.
- [ ] AC 9: Given campanha com leads em múltiplos estágios, when calcular consumo diário, then apenas envios do estágio inicial entram na contagem.
- [ ] AC 10: Given criação de instância via modal admin, when ação é executada, then instância é criada via Uazapi e salva com `api_provider='uazapi'`.
- [ ] AC 11: Given modal de detalhes de usuário admin, when aberto, then lista todas as instâncias do usuário com status e provider.
- [ ] AC 12: Given feature flag `UAZAPI_FOR_ALL_USERS_ENABLED` desligada, when usuário comum cria/usa instância, then fluxo legado continua funcional.
- [ ] AC 13: Given feature flag `UAZAPI_FOR_ALL_USERS_ENABLED` ligada, when usuário comum elegível cria/usa instância, then fluxo Uazapi é habilitado sem quebrar worker legado da MegaAPI.
- [ ] AC 14: Given divergência entre status local e Uazapi, when sincronização por `listfolders`/`listmessages` executa, then status local é reconciliado com API.
- [ ] AC 15: Given mensagens com status `Sent` retornadas em `listmessages`, when reconciliar uso diário, then contagem local reflete uso real sem dupla contagem.
- [ ] AC 16: Given mensagens `Scheduled` e `Failed` retornadas pela API, when worker decide próxima etapa, then avanço ocorre apenas para leads elegíveis conforme estado real.
- [ ] AC 17: Given usuário com plano Starter e extrações acumuladas de 1000 no ciclo, when solicitar nova extração, then requisição é bloqueada com mensagem de limite mensal.
- [ ] AC 18: Given usuário com plano Pro/Scale/Infinite, when validar limite mensal, then sistema aplica respectivamente 2000/4000/10000 sem usar regras legadas.
- [ ] AC 19: Given tentativa de criação/uso de `license_type` legado (`anual/semestral`), when processado, then sistema rejeita como inválido no fluxo ativo.
- [ ] AC 20: Given tentativa de criar licença manual com tipo fora de `starter/pro/scale/infinite`, when admin submete, then sistema rejeita com erro de validação.
- [ ] AC 21: Given rollout para Uazapi global em produção, when erro 5xx de integração >5% por 10 minutos ou latência p95 >8s por 10 minutos, then rollback por feature flag restaura fluxo legado sem indisponibilidade.
- [ ] AC 23: Given campanha Uazapi ativa com `folder_id`, when janela de 10 minutos é atingida, then sistema executa `listfolders` e atualiza `log_sucess` da campanha.
- [ ] AC 24: Given divergência em `listfolders` ou falha de resposta, when fallback é acionado, then `listmessages` é usado para validar envios individuais/valores e reconciliar estado.
- [ ] AC 22: Given suíte de testes atualizada, when executada após implementação, then cobre happy path, erros e regressões dos limites/instâncias/sincronização.

## Additional Context

### Dependencies

- Uazapi acessível e estável para operações de instância para todos os usuários
- Persistência de dados de licença/plano consistente no PostgreSQL
- Compatibilidade temporária com MegaAPI sem regressão operacional
- Endpoints Uazapi `sender/listfolders` e `sender/listmessages` disponíveis para reconciliação de uso/status
- Variáveis de ambiente para rollout e integração (`UAZAPI_FOR_ALL_USERS_ENABLED`, `UAZAPI_URL`, `UAZAPI_ADMIN_TOKEN`)
- Scheduler/loop disponível para sincronização periódica de 10 minutos em campanhas Uazapi ativas
- Estrutura de dados de campanha/leads com campos de estágio suficientes para distinguir inicial vs follow-up

### Testing Strategy

- Unit tests:
  - Política central de planos (`PlanPolicy`) e regras de mapeamento legado
  - Cálculo de cota diária por instância (incluindo Infinite configurável)
  - Distinção de contagem: inicial vs follow-up
- Integration tests:
  - Endpoints de criação de instância (admin/usuário) com validação de cap por plano
  - Fluxos de conta para salvar/aplicar seletor Infinite
  - Sincronização Uazapi (`listfolders`/`listmessages`) e reconciliação de status com janela de 10 minutos
  - Cenário de corrida em criação simultânea de instâncias para validar trava por plano
- Manual tests:
  - Starter/Pro/Scale/Infinite: tentar exceder cap de instâncias e validar mensagem
  - Infinite: alternar 10→50 e confirmar alteração no comportamento do sender
  - Campanha com follow-up ativo para validar que cota diária não é consumida
  - Ligar/desligar feature flag de rollout e validar fallback MegaAPI
  - Conferir que avanço de etapa do funil segue estado real retornado pela Uazapi

### Notes

- O usuário confirmou exclusão de regras legadas semestral/anual do fluxo ativo
- O usuário confirmou que a migração é para todos os usuários, com MegaAPI temporariamente mantida
- Riscos altos (pre-mortem):
  - Divergência entre regra local e API externa de status/uso
  - Regressão no sender por alterar política de limites
  - Quebra de onboarding/licença ao migrar `license_type`
- Mitigações:
  - Fonte única de regras + testes de contrato
  - Rollout com feature flag + plano de rollback
  - Migração idempotente e monitoramento de reconciliação
- Limitações conhecidas:
  - MegaAPI permanece legado até janela futura de desligamento
  - `listmessages` pode retornar apenas a primeira conversa em alguns cenários; por isso é fallback de conferência, não métrica primária
  - Sincronização com API externa depende de disponibilidade e latência Uazapi
