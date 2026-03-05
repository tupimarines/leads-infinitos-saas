---
title: 'Migrar disparador superadmin para Uazapi'
slug: 'migrar-disparador-superadmin-uazapi'
created: '2026-03-04'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Redis', 'RQ', 'requests']
files_to_modify: ['app.py', 'worker_sender.py', 'services/uazapi.py', 'templates/whatsapp_config.html', 'templates/account.html', 'docker-compose.yml', 'docker-compose.dev.yml', '.env.example']
code_patterns: ['WhatsappService em app.py (manter para MegaAPI)', 'MEGA_API_URL/TOKEN env vars', 'is_super_admin() para augustogumi@gmail.com', 'instances.apikey = instance_key (MegaAPI)']
test_patterns: ['pytest em tests/', 'test_*.py']
---

# Tech-Spec: Migrar disparador superadmin para Uazapi

**Created:** 2026-03-04

## Overview

### Problem Statement

O projeto usa MegaAPI para WhatsApp (criação de instância, conexão, status, envio de mensagens). A migração para Uazapi é planejada (project-context linhas 58-63) por ser mais leve e amigável. Esta spec implementa a migração **apenas para o superadmin** (augustogumi@gmail.com) como validação antes de aplicar globalmente.

### Solution

Substituir chamadas MegaAPI por Uazapi para o superadmin: criar instância (POST /instance/init), conectar (POST /instance/connect), status (GET /instance/status), deletar (DELETE /instance), enviar mensagem (POST /send/text), verificar número (POST /chat/check). Criar `UazapiService` em `services/uazapi.py` (sem extrair WhatsappService — manter em app.py para MegaAPI). **Remover completamente** botão e lógica de Reiniciar (Uazapi não tem restart). Superadmin usa **apenas** instâncias Uazapi; remover instâncias MegaAPI do superadmin. Vincular status em /account ao status real da Uazapi para todas as instâncias.

### Scope

**In Scope:**
1. Envio de mensagem via Uazapi conforme status da campanha (ativa, pausada, programada, dentro/fora dias e horários)
2. Criar instância via POST /instance/init (botão Criar Instância)
3. Conectar instância via POST /instance/connect (botão QR Code)
4. Verificar status via GET /instance/status (botão Status)
5. **Remover completamente** botão Reiniciar e toda lógica de restart (rotas, campaign create, WhatsappService)
6. Deletar instância via DELETE /instance (botão Deletar)
7. Status de conexão em /account vinculado ao Uazapi — **mostrar todas as instâncias** com status real
8. Remover instâncias MegaAPI do superadmin (apenas Uazapi)
9. Criar UazapiService em services/uazapi.py (não extrair WhatsappService agora)

**Out of Scope:**
- Usuários não-superadmin (continuam com MegaAPI)
- worker_cadence.py (follow-up) — mantém MegaAPI por enquanto
- Extração/remoção de WhatsappService (migração final futura)

## Context for Development

### Codebase Patterns

- **Monolito Flask**: `app.py` ~4600 linhas
- **WhatsappService**: classe em app.py — **manter** para MegaAPI (usuários não-superadmin)
- **UazapiService**: novo em services/uazapi.py — apenas para superadmin
- **instances table**: id, user_id, name, server_url, apikey, status, updated_at, api_provider
- **Uazapi**: admintoken para init; token (instance) para connect, status, delete, send, chat/check. URL: https://neurix.uazapi.com
- **is_super_admin()**: email augustogumi@gmail.com
- **Worker**: worker_sender.py — filtrar instâncias Uazapi para superadmin; usar /chat/check para verificar número
- **Campanha pausada**: status 'paused' já existe (toggle_pause); worker filtra `status IN ('pending','running')` — pausadas não são processadas
- **Campanha programada**: scheduled_start com data futura → status 'pending'; worker usa `scheduled_start IS NULL OR scheduled_start <= NOW()` — programadas futuras não são processadas

### Files to Reference

| File | Purpose |
| ---- | ------- |
| app.py | WhatsappService, rotas /api/whatsapp/*, /account, campaign create (restart a remover) |
| worker_sender.py | send_message, check_phone_on_whatsapp, lógica de campanha |
| templates/whatsapp_config.html | Botões Criar, QR Code, Status, Deletar (Reiniciar a remover) |
| templates/account.html | Status de conexão WhatsApp — expandir para múltiplas instâncias |
| uazapi-openapi-spec (1).yaml | /instance/init, /instance/connect, /instance/status, DELETE /instance, /send/text, /chat/check |
| _bmad-output/project-context.md | Regras, stack, dependências linhas 58-63 |

### Technical Decisions

1. **Coluna api_provider**: adicionar à tabela instances (DEFAULT 'megaapi'). Instâncias Uazapi = 'uazapi'.
2. **apikey para Uazapi**: armazena o token retornado por POST /instance/init.
3. **Variáveis de ambiente**: UAZAPI_URL, UAZAPI_ADMIN_TOKEN. **Nunca commitar token** — usar .env.
4. **Superadmin apenas Uazapi**: remover instâncias MegaAPI do superadmin (DELETE ou migração).
5. **check_phone**: usar POST /chat/check com payload `{numbers: [number]}`; resposta tem `isInWhatsapp`.
6. **DELETE 404**: se Uazapi retornar 404 (instância já deletada), tratar como sucesso e remover do DB.
7. **account.html**: superadmin vê todas as instâncias; cada uma com seu status real via /instance/status.
8. **Status instância**: /instance/status retorna connected, connecting ou disconnected; atualizar DB conforme resposta.
9. **Não extrair WhatsappService**: criar UazapiService em arquivo separado; manter WhatsappService em app.py para não comprometer outros usuários. Migração final futura remove WhatsappService.

## Implementation Plan

### Tasks

- [x] Task 1: Adicionar coluna api_provider à tabela instances
  - File: `app.py` (init_db)
  - Action: `ALTER TABLE instances ADD COLUMN IF NOT EXISTS api_provider TEXT DEFAULT 'megaapi';`

- [x] Task 2: Criar services/uazapi.py com UazapiService
  - File: `services/uazapi.py` (novo)
  - Action: Criar UazapiService (não extrair WhatsappService). Métodos: create_instance (POST /instance/init, admintoken), connect (POST /instance/connect, token), get_status (GET /instance/status, token), delete_instance (DELETE /instance, token), send_text (POST /send/text, token), check_phone (POST /chat/check, token). URL base de UAZAPI_URL. Header admintoken para init; header token para demais.
  - Notes: Payload init: `{name}`. Payload connect: `{}`. Payload send: `{number, text}` — number sem @s.whatsapp.net. Payload check_phone: `{numbers: [number]}`. Resposta check: array com `isInWhatsapp`.

- [x] Task 3: Atualizar init_whatsapp para superadmin usar Uazapi
  - File: `app.py`
  - Action: Se is_super_admin(), chamar UazapiService.create_instance; extrair token de `response.get('token') or response.get('instance', {}).get('token')`; salvar em instances com api_provider='uazapi', apikey=token, name=instância.
  - **Exemplo curl init:**
  ```bash
  curl -X POST https://neurix.uazapi.com/instance/init \
    -H "admintoken: $UAZAPI_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name": "minha-instancia"}'
  ```
  - Resposta: `{ "token": "uuid-...", "instance": {...}, "name": "minha-instancia" }`

- [x] Task 4: Atualizar get_whatsapp_qr para superadmin usar Uazapi
  - File: `app.py`
  - Action: Se instance.api_provider='uazapi', chamar UazapiService.connect(apikey); resposta tem instance.qrcode (base64). Retornar {"base64": qrcode}. Se já conectado, instance.status='connected'.

- [ ] Task 5: Atualizar get_whatsapp_status e admin_check_whatsapp_status para Uazapi
  - File: `app.py`
  - Action: Se api_provider='uazapi', chamar UazapiService.get_status(apikey); mapear instance.status (disconnected, connecting, connected) para status do DB. Atualizar instances.status conforme resposta da API.

- [x] Task 6: Atualizar delete_whatsapp_instance para Uazapi
  - File: `app.py`
  - Action: Se api_provider='uazapi', chamar UazapiService.delete_instance(apikey). Se API retornar 200: remover do DB. **Se API retornar 404** (instância já deletada na Uazapi): não fazer nada (não tratar como erro; remover do DB mesmo assim para consistência).

- [x] Task 7: Remover completamente botão Reiniciar e toda lógica de restart
  - File: `templates/whatsapp_config.html`
  - Action: Remover `<button onclick="restartSingleInstance(...)">Reiniciar</button>` e função restartSingleInstance.
  - File: `app.py`
  - Action: Remover rota /api/whatsapp/restart; remover bloco de restart em campaign create (linhas ~2913-2936 que chamam service.restart_instance); remover método restart_instance de WhatsappService (ou deixar no-op para compatibilidade com MegaAPI — mas como removemos o botão para todos, podemos remover a rota e o bloco do campaign create; WhatsappService.restart_instance pode permanecer para admin que ainda usa MegaAPI em outros fluxos, ou remover se não houver mais uso).
  - Notes: Uazapi não tem restart. Remover para todos os usuários.

- [x] Task 8: Remover instâncias MegaAPI do superadmin
  - File: `app.py` (ou script de migração)
  - Action: Para superadmin, manter apenas instâncias api_provider='uazapi'. Opções: (a) migração: `DELETE FROM instances WHERE user_id = (SELECT id FROM users WHERE email = 'augustogumi@gmail.com') AND (api_provider IS NULL OR api_provider != 'uazapi')`; (b) ao carregar whatsapp_config, filtrar para exibir apenas Uazapi. Decisão: executar migração para remover MegaAPI do superadmin.
  - Notes: Superadmin usa somente Uazapi.

- [x] Task 9: Atualizar worker_sender para Uazapi no envio
  - File: `worker_sender.py`
  - Action: Para superadmin, filtrar instances com api_provider='uazapi' apenas. SELECT incluir apikey, api_provider. Se is_sa, usar apenas instâncias Uazapi. Chamar send_message_uazapi(apikey, number, text). number = extrair de phone_jid (remover @s.whatsapp.net).

- [x] Task 10: Atualizar worker_sender para check_phone via /chat/check
  - File: `worker_sender.py`
  - Action: Se instance Uazapi, usar POST /chat/check. Payload: `{numbers: [number]}` (number sem @s.whatsapp.net). Resposta: array com `isInWhatsapp`. Mapear para exists/correct_jid.
  - Notes: Uazapi retorna `isInWhatsapp` (camelCase). Endpoint: POST /chat/check com header token.

- [x] Task 11: Atualizar worker_sender para status e recovery quando Uazapi
  - File: `worker_sender.py`
  - Action: Se instance Uazapi, get_instance_status_api chama Uazapi GET /instance/status. Retornar connected, connecting ou disconnected conforme API. Atualizar instances.status no DB. verify_and_recover_instance: para Uazapi, não chamar restart (não existe); retornar False sem tentar recovery; atualizar status no DB se desconectado.

- [ ] Task 12: Vincular status em /account ao Uazapi — mostrar todas as instâncias
  - File: `app.py`
  - Action: Na rota /account, se superadmin: buscar todas as instances (api_provider='uazapi'); para cada uma, chamar UazapiService.get_status e obter status real (connected, connecting, disconnected). Passar lista `instances_with_status` ao template.
  - File: `templates/account.html`
  - Action: Para superadmin, em vez de um único bloco "instance", criar loop sobre `instances_with_status`; cada instância com seu próprio card mostrando nome e status (Conectado/Conectando/Desconectado). Manter layout glass-panel; expandir seção WhatsApp para múltiplos cards.

- [ ] Task 13: Adicionar UAZAPI_URL e UAZAPI_ADMIN_TOKEN ao docker-compose e criar .env.example
  - File: `docker-compose.yml`, `docker-compose.dev.yml`
  - Action: Adicionar env vars UAZAPI_URL, UAZAPI_ADMIN_TOKEN nos serviços app, worker_sender.
  - File: `.env.example` (criar se não existir)
  - Action: Criar .env.example com placeholders para todas as vars incluindo UAZAPI_URL, UAZAPI_ADMIN_TOKEN (sem valores reais).

- [ ] Task 14: Garantir status 'paused' e programada no worker
  - File: `worker_sender.py`
  - Action: Verificar que worker já filtra `status IN ('pending','running')` — campanhas 'paused' não são processadas. Verificar que `scheduled_start IS NULL OR scheduled_start <= NOW()` — campanhas programadas para data futura (pending com scheduled_start > NOW) não são processadas. Documentar no spec; não alterar se já correto.
  - Notes: toggle_pause em app.py já define status 'paused'. Campanha com scheduled_start futura fica 'pending' até scheduled_start <= NOW.

### Acceptance Criteria

- [ ] AC 1: Given campanha ativa do superadmin com instância Uazapi conectada, when worker processa lead pendente, then mensagem enviada com resposta 200 OK da Uazapi
- [ ] AC 2: Given campanha pausada do superadmin, when worker processa, then mensagens não são enviadas
- [ ] AC 3: Given campanha despausada do superadmin, when worker processa, then mensagem enviada com 200 OK
- [ ] AC 4: Given campanha programada (scheduled_start futura), when worker processa, then mensagem não é enviada
- [ ] AC 5: Given superadmin na tela WhatsApp, when clica "Criar Instância" com nome válido, then instância criada via POST /instance/init e exibida na lista
- [ ] AC 6: Given instância Uazapi criada, when clica "QR Code", then QR exibido e ao escanear conexão confirmada
- [ ] AC 7: Given instância Uazapi, when clica "Status", then API retorna conectado, desconectado ou conectando
- [ ] AC 8: Given tela WhatsApp, when visualizada, then botão "Reiniciar" não aparece
- [ ] AC 9: Given instância Uazapi, when clica "Deletar", then DELETE /instance executado e retorna sucesso ou fracasso
- [ ] AC 10: Given superadmin em /account, when página carregada, then todas as instâncias exibidas com status real (Conectado/Conectando/Desconectado)
- [ ] AC 11: Given instância na lista do superadmin, when status ao lado da instância, then reflete status real da Uazapi
- [ ] AC 12: Given superadmin, when carrega whatsapp_config, then apenas instâncias Uazapi exibidas (MegaAPI removidas)

## Additional Context

### Dependencies

- Uazapi API em https://neurix.uazapi.com
- admintoken: usar variável de ambiente UAZAPI_ADMIN_TOKEN (nunca commitar valor)
- project-context linhas 58-63: MegaAPI atual, Uazapi planejado, modularização incremental

### Testing Strategy

- Testes manuais: fluxo completo criar → QR → conectar → enviar → pausar → despausar → deletar
- Verificar campanha programada não envia
- Verificar /account mostra todas as instâncias com status correto
- Teste de regressão: usuário não-superadmin continua com MegaAPI

### Notes

- worker_cadence.py (follow-up) mantém MegaAPI
- Uazapi /send/text usa number no formato 5511999999999 (sem @s.whatsapp.net)
- /chat/check: payload {numbers: ["5511999999999"]}, resposta array com isInWhatsapp
- Status instância: /instance/status retorna connected, connecting, disconnected
- Campanha pausada: status 'paused' já existe; worker exclui
- Campanha programada: scheduled_start futura → pending; worker exclui até scheduled_start <= NOW
