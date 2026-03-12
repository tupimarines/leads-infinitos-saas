# Guia Operacional de Rollout e Rollback Uazapi (Todos os Usuários)

## Objetivo

Este guia padroniza a ativação gradual da flag `UAZAPI_FOR_ALL_USERS_ENABLED`, monitoramento operacional e rollback seguro sem indisponibilidade.

## Pré-requisitos

- Variáveis de ambiente válidas:
  - `UAZAPI_FOR_ALL_USERS_ENABLED`
  - `UAZAPI_URL`
  - `UAZAPI_ADMIN_TOKEN`
  - `MEGA_API_URL`
  - `MEGA_API_TOKEN`
- Banco e Redis saudáveis.
- Logs de `app.py`, `worker_sender.py` e `worker_cadence.py` acessíveis.
- Time de suporte ciente da janela de rollout.

## Estratégia de Rollout (Feature Flag)

1. **Pré-check (T-30min)**
   - Confirmar que criação de instância admin permanece funcional.
   - Confirmar que envio legado (MegaAPI) continua operando para campanhas sem Uazapi ativa.
   - Confirmar acesso aos endpoints Uazapi de status/sincronização.

2. **Ativação controlada**
   - Alterar `UAZAPI_FOR_ALL_USERS_ENABLED=true`.
   - Reiniciar serviços necessários (web + workers).
   - Validar smoke test básico:
     - criação de instância Uazapi para usuário não-superadmin;
     - envio inicial de campanha;
     - sync por `listfolders`/`listmessages`.

3. **Monitoração intensiva (primeiros 30 min)**
   - Erro de integração Uazapi (`HTTP 5xx`) por minuto.
   - Latência p95 das chamadas Uazapi.
   - Taxa de falha em criação de instância.
   - Taxa de falha de disparo (`failed`) acima da linha de base.

4. **Estabilização (30-120 min)**
   - Manter monitoração contínua.
   - Verificar se há divergência relevante entre status local e status reconciliado via sync.

## Critérios Objetivos de Rollback

Executar rollback imediato se qualquer condição abaixo ocorrer por 10 minutos consecutivos:

- Erro 5xx de integração Uazapi **maior que 5%**.
- Latência p95 de integração Uazapi **maior que 8s**.

Também considerar rollback se:

- aumento anormal de falha de envio sem recuperação;
- indisponibilidade do endpoint de criação de instância;
- backlog crescente de campanhas sem avanço de etapa após sync.

## Procedimento de Rollback (Sem Downtime)

1. Ajustar `UAZAPI_FOR_ALL_USERS_ENABLED=false`.
2. Reiniciar os processos necessários (web + workers).
3. Confirmar fallback:
   - fluxos elegíveis voltam ao comportamento legado;
   - campanhas legadas seguem com MegaAPI sem quebra.
4. Validar saúde:
   - login/admin;
   - criação/edição de campanha;
   - processamento de fila normalizado.

## Checklist Pós-Deploy

- [ ] Criação de instância para usuário comum funcionando.
- [ ] Bloqueio de limite de instâncias por plano funcionando.
- [ ] Limite diário por instância respeitado no sender.
- [ ] Follow-up não consumindo cota diária.
- [ ] Sync `listfolders` e fallback `listmessages` funcionando.
- [ ] Sem crescimento anormal de erros 5xx.
- [ ] Latência p95 abaixo de 8s.

## Sinais de Regressão

- Campanhas com status "running" sem progresso por período prolongado.
- Divergência recorrente entre contagem local e API.
- Erros de plano/licença inválida em operações admin rotineiras.
- Queda abrupta de taxa de entrega após ativação da flag.

## Comunicação Operacional

- Registrar no canal interno:
  - horário de ativação;
  - versão/deploy aplicado;
  - métricas dos primeiros 30 minutos;
  - decisão de manter ou rollback.
- Em rollback, registrar causa raiz preliminar e próximos passos.
