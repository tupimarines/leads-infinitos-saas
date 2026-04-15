---
title: 'Migrar disparador superadmin para Uazapi'
slug: 'migrar-disparador-superadmin-uazapi'
created: '2026-03-04'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Redis', 'RQ', 'requests']
files_to_modify: ['app.py', 'worker_sender.py', 'services/whatsapp.py', 'templates/whatsapp_config.html', 'templates/account.html', 'docker-compose.yml', 'docker-compose.dev.yml']
code_patterns: ['WhatsappService em app.py', 'MEGA_API_URL/TOKEN env vars', 'is_super_admin() para augustogumi@gmail.com', 'instances.apikey = instance_key (MegaAPI)']
test_patterns: ['pytest em tests/', 'test_*.py']
---

# Tech-Spec: Migrar disparador superadmin para Uazapi

**Created:** 2026-03-04

## Overview

### Problem Statement

O projeto usa MegaAPI para WhatsApp (criação de instância, conexão, status, envio de mensagens). A migração para Uazapi é planejada (project-context linhas 58-63) por ser mais leve e amigável. Esta spec implementa a migração **apenas para o superadmin** (augustogumi@gmail.com) como validação antes de aplicar globalmente.

### Solution

Substituir chamadas MegaAPI por Uazapi para o superadmin: criar instância (POST /instance/init), conectar (POST /instance/connect), status (GET /instance/status), deletar (DELETE /instance), enviar mensagem (POST /send/text). Extrair `WhatsappService` para `services/whatsapp.py` com suporte dual (MegaAPI vs Uazapi) baseado em `api_provider` da instância. Remover botão Reiniciar. Vincular status em /account ao status real da Uazapi.

### Scope

**In Scope:**
1. Envio de mensagem via Uazapi conforme status da campanha (ativa, pausada, programada, dentro/fora dias e horários)
2. Criar instância via POST /instance/init (botão Criar Instância)
3. Conectar instância via POST /instance/connect (botão QR Code)
4. Verificar status via GET /instance/status (botão Status)
5. Remover botão Reiniciar
6. Deletar instância via DELETE /instance (botão Deletar)
7. Status de conexão em /account vinculado ao Uazapi
8. Aplicar mudanças a todas as instâncias existentes do superadmin (requer recriação)
9. Extrair WhatsappService para services/whatsapp.py

**Out of Scope:**
- Usuários não-superadmin (continuam com MegaAPI)
- worker_cadence.py (follow-up) — mantém MegaAPI por enquanto
- Migração automática de instâncias MegaAPI existentes (superadmin deve deletar e criar novas)

## Context for Development

### Codebase Patterns

- **Monolito Flask**: `app.py` ~4600 linhas; modularização incremental ao tocar em features
- **WhatsappService**: classe em app.py usa MEGA_API_URL, MEGA_API_TOKEN; métodos: create_instance, get_qr_code, get_status, restart_instance, logout_instance, delete_instance
- **instances table**: id, user_id, name, server_url, apikey, status, updated_at. MegaAPI: apikey = instance_key
- **Uazapi**: admintoken para init; token (instance) para connect, status, delete, send. URL: https://neurix.uazapi.com
- **is_super_admin()**: email augustogumi@gmail.com
- **Worker**: worker_sender.py usa instance_name, MEGA_API_*; send_message, check_phone_on_whatsapp, get_instance_status_api, restart_instance_api

### Files to Reference

| File | Purpose |
| ---- | ------- |
| app.py | WhatsappService, rotas /api/whatsapp/*, /account, admin_check_whatsapp_status |
| worker_sender.py | send_message, check_phone_on_whatsapp, get_instance_status_api, lógica de campanha |
| templates/whatsapp_config.html | Botões Criar, QR Code, Status, Reiniciar, Deletar |
| templates/account.html | Status de conexão WhatsApp |
| uazapi-openapi-spec (1).yaml | Especificação Uazapi: /instance/init, /instance/connect, /instance/status, DELETE /instance, /send/text |
| _bmad-output/project-context.md | Regras, stack, dependências linhas 58-63 |

### Technical Decisions

1. **Coluna api_provider**: adicionar à tabela instances (DEFAULT 'megaapi'). Instâncias Uazapi = 'uazapi'. Permite coexistência.
2. **apikey para Uazapi**: armazena o token retornado por POST /instance/init (não o nome). Necessário para connect, status, delete, send.
3. **Variáveis de ambiente**: UAZAPI_URL=https://neurix.uazapi.com, UAZAPI_ADMIN_TOKEN. MegaAPI permanece para não-superadmin.
4. **Worker**: quando campaign.user_id é superadmin e instance.api_provider='uazapi', usar Uazapi para send e check_phone. Uazapi não tem isOnWhatsApp — manter check ou simplificar para superadmin.
5. **account.html**: para superadmin, buscar status real via Uazapi ao carregar a página (ou endpoint que retorna status).

## Implementation Plan

### Tasks

- [ ] Task 1: Adicionar coluna api_provider à tabela instances
  - File: `app.py` (init_db ou migrate)
  - Action: `ALTER TABLE instances ADD COLUMN IF NOT EXISTS api_provider TEXT DEFAULT 'megaapi';`
  - Notes: Migração manual ou em init_db

- [ ] Task 2: Criar services/whatsapp.py com UazapiService
  - File: `services/whatsapp.py` (novo)
  - Action: Extrair WhatsappService de app.py; criar UazapiService com métodos: create_instance (POST /instance/init, admintoken), connect (POST /instance/connect, token), get_status (GET /instance/status, token), delete_instance (DELETE /instance, token), send_text (POST /send/text, token). Factory ou método que retorna instância correta baseado em api_provider.
  - Notes: URL base https://neurix.uazapi.com; header admintoken para init; header token para demais. Payload init: {name}. Payload connect: {} ou {phone}. Payload send: {number, text}. number = JID sem @s.whatsapp.net

- [ ] Task 3: Atualizar init_whatsapp para superadmin usar Uazapi
  - File: `app.py`
  - Action: Se is_super_admin(), chamar UazapiService.create_instance; extrair token da resposta (instance.token ou response.token); salvar em instances com api_provider='uazapi', apikey=token
  - Notes: Uazapi init retorna instance com token. Armazenar token em apikey.

- [ ] Task 4: Atualizar get_whatsapp_qr para superadmin usar Uazapi
  - File: `app.py`
  - Action: Se instance.api_provider='uazapi', chamar UazapiService.connect(apikey); resposta tem instance.qrcode (base64). Retornar {"base64": qrcode}
  - Notes: Connect inicia conexão e retorna QR. Se já conectado, instance.status='connected'

- [ ] Task 5: Atualizar get_whatsapp_status e admin_check_whatsapp_status para Uazapi
  - File: `app.py`
  - Action: Se api_provider='uazapi', chamar UazapiService.get_status(apikey); mapear instance.status (disconnected, connecting, connected) para status do DB
  - Notes: Uazapi retorna instance.status e status.connected

- [ ] Task 6: Atualizar delete_whatsapp_instance para Uazapi
  - File: `app.py`
  - Action: Se api_provider='uazapi', chamar UazapiService.delete_instance(apikey); DELETE /instance com header token
  - Notes: Remover do DB após sucesso na API

- [ ] Task 7: Remover botão Reiniciar do template whatsapp_config.html
  - File: `templates/whatsapp_config.html`
  - Action: Remover `<button class="btn" onclick="restartSingleInstance(...)">Reiniciar</button>` e função restartSingleInstance
  - Notes: Uazapi não tem restart; remover para todos (não só superadmin) conforme spec

- [ ] Task 8: Remover rotas e lógica de restart em app.py
  - File: `app.py`
  - Action: Remover ou desabilitar rota /api/whatsapp/restart, admin_restart_instances; remover restart_instance de WhatsappService/UazapiService
  - Notes: Verificar referências a restart em campaign create

- [ ] Task 9: Atualizar worker_sender para Uazapi no envio
  - File: `worker_sender.py`
  - Action: Ao buscar instances (SELECT i.name, i.id, i.apikey, i.api_provider), incluir apikey e api_provider. Se is_sa e instance.api_provider='uazapi', chamar send_message_uazapi(apikey, number, text). number = extrair de phone_jid (remover @s.whatsapp.net). Respeitar status campanha (ativa/pausada/programada)
  - Notes: Worker já tem is_sa (email == SUPER_ADMIN_EMAIL). Criar send_message_uazapi em worker ou importar de services/whatsapp

- [ ] Task 10: Atualizar worker_sender para check_phone e status quando Uazapi
  - File: `worker_sender.py`
  - Action: Se instance Uazapi, get_instance_status_api deve chamar Uazapi; check_phone_on_whatsapp: Uazapi pode não ter isOnWhatsApp — verificar spec. Se não tiver, pular check ou usar endpoint alternativo
  - Notes: Spec Uazapi tem /contact/findByNumber ou similar? Se não, considerar pular verificação para Uazapi (envio direto)

- [ ] Task 11: Remover restart_instance_api e verify_and_recover_instance para instâncias Uazapi
  - File: `worker_sender.py`
  - Action: Se instance Uazapi, não chamar restart; verify_and_recover_instance retornar False sem tentar restart
  - Notes: Uazapi não tem restart

- [ ] Task 12: Vincular status em /account ao Uazapi
  - File: `app.py`, `templates/account.html`
  - Action: Na rota /account, se superadmin e tem instance com api_provider='uazapi', chamar UazapiService.get_status e passar status real ao template. Template exibir Conectado/Desconectado/Conectando conforme status real
  - Notes: Para multi-instance superadmin, account mostra primeira ou todas? Project-context: account usa instance única. Verificar se superadmin tem multi-instance em account

- [ ] Task 13: Adicionar UAZAPI_URL e UAZAPI_ADMIN_TOKEN ao docker-compose
  - File: `docker-compose.yml`, `docker-compose.dev.yml`
  - Action: Adicionar env vars UAZAPI_URL, UAZAPI_ADMIN_TOKEN nos serviços app, worker_sender
  - Notes: Não commitar token; usar placeholder ou .env

- [ ] Task 14: Instâncias existentes do superadmin
  - File: `templates/whatsapp_config.html` (opcional)
  - Action: Instâncias criadas com MegaAPI não têm token Uazapi. Superadmin deve deletar (remove do DB) e criar novas via Uazapi. Opcional: ao carregar whatsapp_config para superadmin com instâncias api_provider='megaapi', mostrar aviso "Instâncias antigas: delete e crie novas para usar Uazapi"
  - Notes: Não há migração automática de conexão. admin_create_user mantém MegaAPI (novo user não é superadmin)

### Acceptance Criteria

- [ ] AC 1: Given campanha ativa do superadmin com instância Uazapi conectada, when worker processa lead pendente, then mensagem enviada com resposta 200 OK da Uazapi
- [ ] AC 2: Given campanha pausada do superadmin, when worker processa, then mensagens não são enviadas
- [ ] AC 3: Given campanha despausada do superadmin, when worker processa, then mensagem enviada com 200 OK
- [ ] AC 4: Given campanha programada (fora de dias/horários), when worker processa, then mensagem não é enviada
- [ ] AC 5: Given superadmin na tela WhatsApp, when clica "Criar Instância" com nome válido, then instância criada via POST /instance/init e exibida na lista
- [ ] AC 6: Given instância Uazapi criada, when clica "QR Code", then QR exibido e ao escanear conexão confirmada
- [ ] AC 7: Given instância Uazapi, when clica "Status", then API retorna conectado, desconectado ou conectando
- [ ] AC 8: Given tela WhatsApp do superadmin, when visualizada, then botão "Reiniciar" não aparece
- [ ] AC 9: Given instância Uazapi, when clica "Deletar", then DELETE /instance executado e retorna sucesso ou fracasso
- [ ] AC 10: Given superadmin em /account, when página carregada, then status de conexão reflete status real da instância Uazapi
- [ ] AC 11: Given instância na lista do superadmin, when status ao lado da instância, then reflete status real da Uazapi

## Additional Context

### Dependencies

- Uazapi API em https://neurix.uazapi.com
- admintoken: XGA3pwXN4R6FAWc0Xr6sYzxR9vX5dkjv9nbAtgqI0JYhS0hYT0 (não commitar; usar .env)
- project-context linhas 58-63: MegaAPI atual, Uazapi planejado, modularização incremental

### Testing Strategy

- Testes manuais: fluxo completo criar → QR → conectar → enviar → pausar → despausar → deletar
- Verificar campanha programada não envia
- Verificar /account mostra status correto
- Teste de regressão: usuário não-superadmin continua com MegaAPI

### Notes

- worker_cadence.py (follow-up) mantém MegaAPI; migração futura
- Uazapi /send/text usa number no formato 5511999999999 (sem @s.whatsapp.net)
- check_phone_on_whatsapp: verificar se Uazapi tem endpoint equivalente; se não, considerar envio direto para superadmin
- Instâncias existentes: superadmin deve deletar e criar novas
