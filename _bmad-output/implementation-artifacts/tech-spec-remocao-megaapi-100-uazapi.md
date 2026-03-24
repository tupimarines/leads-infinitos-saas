---
title: 'Remoção dos módulos MegaAPI e migração 100% Uazapi'
slug: 'remocao-megaapi-100-uazapi'
created: '2026-03-18'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python 3.x', 'Flask', 'PostgreSQL', 'Uazapi API', 'worker_sender', 'worker_cadence', 'Jinja2']
files_to_modify: ['app.py', 'worker_sender.py', 'worker_cadence.py', 'templates/campaigns_new.html', 'docker-compose.yml', 'docker-compose.dev.yml', '.env.example', 'tests/test_sender_mock.py']
code_patterns: ['use_uazapi_sender', 'api_provider', 'WhatsappService', 'UazapiService', '_sync_uazapi_usage', 'send_text_message', 'send_media_message', 'MEGA_API_URL', 'MEGA_API_TOKEN']
test_patterns: ['pytest tests/', 'MOCK_SENDER', 'os.environ.get']
---

# Tech-Spec: Remoção dos módulos MegaAPI e migração 100% Uazapi

**Created:** 2026-03-18

## Overview

### Problem Statement

O projeto mantém código legado da MegaAPI para envio de mensagens WhatsApp, enquanto a criação de instâncias e campanhas Uazapi já está em produção. A coexistência de dois provedores (MegaAPI e Uazapi) aumenta complexidade, duplica lógica e mantém variáveis de ambiente desnecessárias. O objetivo é remover completamente os módulos MegaAPI e operar somente via Uazapi.

### Solution

1. Forçar `use_uazapi_sender=true` em todas as novas campanhas (checkbox oculto/removido).
2. **worker_sender:** Remover apenas os **branches MegaAPI** em get_instance_status_api, check_phone_on_whatsapp, send_message; remover restart_instance_api inteiro; remover verify_and_recover_instance branch MegaAPI. **Manter** branches Uazapi, _sync_uazapi_usage, format_jid, jid_to_number, _is_media_path_safe.
3. **worker_cadence:** Remover send_text_message e send_media_message; em process_campaign_sends, remover branch MegaAPI; se api_provider != 'uazapi', logar e pular.
4. **app.py:** Remover WhatsappService; remover branches MegaAPI em get_whatsapp_qr, get_whatsapp_status, delete_whatsapp_instance. Se api_provider != 'uazapi', retornar 400.
5. Remover MEGA_API_URL e MEGA_API_TOKEN (worker_sender, worker_cadence, docker-compose, .env.example).
6. **test_sender_mock.py:** Atualizar mocks para Uazapi (api_provider='uazapi' ou mockar uazapi_service).

**Pré-requisito assumido:** Não há instâncias MegaAPI ativas nem campanhas ativas dependentes do worker MegaAPI. Caso existam, o deploy deve ser precedido de migração manual ou aviso aos usuários.

**Referência de dependências:** `_bmad-output/analise-dependencias-uazapi-remocao-megaapi.md`

### Scope

**In Scope:**
- Formulário de campanha: use_uazapi_sender sempre true (checkbox oculto ou removido)
- worker_sender: remover envio MegaAPI; manter _sync_uazapi_usage; simplificar loop
- worker_cadence: remover send_text_message e send_media_message; usar só Uazapi
- app.py: remover WhatsappService; remover branches MegaAPI em get_whatsapp_qr, get_whatsapp_status, delete_whatsapp_instance
- app.py: validação na criação de campanha — exigir instância Uazapi (já existe)
- docker-compose: remover MEGA_API_URL e MEGA_API_TOKEN (opcional)

**Out of Scope:**
- Migração automática de campanhas/instâncias MegaAPI existentes no banco
- Script de migração de dados (assume ambiente limpo ou migração manual prévia)
- migrate_bootstrap_cadence.py (avaliar separadamente; pode ser deprecated)

## Context for Development

### Codebase Patterns

| Padrão | Localização | Notas |
|--------|-------------|-------|
| use_uazapi_sender | campaigns table, app.py, worker_sender | true = Uazapi; false = MegaAPI (legado) |
| api_provider | instances table | 'uazapi' ou 'megaapi'; COALESCE(api_provider, 'megaapi') |
| WhatsappService | app.py:4334–4468 | Classe MegaAPI: create_instance, get_qr_code, get_status, logout_instance, delete_instance. Usa MEGA_API_URL, MEGA_API_TOKEN. |
| UazapiService | services/uazapi.py | create_instance, connect, get_status, delete_instance, send_text, send_media, create_advanced_campaign, edit_campaign |
| _sync_uazapi_usage | worker_sender.py:700–740 | Sync campanhas Uazapi a cada 10 min; chama sync_campaign_leads_from_uazapi |
| get_instance_status_api | worker_sender.py:103–166 | Uazapi path (111–123); MegaAPI path (125–166) — remover MegaAPI |
| restart_instance_api | worker_sender.py:167–192 | Só MegaAPI — remover inteiro |
| check_phone_on_whatsapp | worker_sender.py:389–533 | Uazapi path (408–426); MegaAPI path (428–533) — remover MegaAPI |
| send_message | worker_sender.py:534–602 | Uazapi path (545–561); MegaAPI path (563–602) — remover MegaAPI |
| send_text_message | worker_cadence.py:253–268 | MegaAPI POST /rest/sendMessage/{instance}/text — remover |
| send_media_message | worker_cadence.py:269–295 | MegaAPI POST /rest/sendMessage/{instance}/imageMessage ou videoMessage — remover |
| process_campaign_sends | worker_cadence.py:1334–1535 | Usa api_provider; branch MegaAPI (1508–1518) — remover; usar só Uazapi |
| use_uazapi_sender (HTML) | campaigns_new.html:343 | Checkbox id="use_uazapi_sender"; JS usa em 1155, 1194, 1284, 1426, 1461, 1485, 1507 |
| get_whatsapp_qr | app.py:4564–4637 | api_provider; uazapi (4581–4591); megaapi (4593–4636) — remover branch megaapi |
| get_whatsapp_status | app.py:4640–4720 | Idem |
| delete_whatsapp_instance | app.py:4725–4770 | Idem |

### Files to Reference

| File | Purpose |
| ---- | ------- |
| app.py | WhatsappService (4334–4468 remover), rotas QR/status/delete (simplificar), init_whatsapp (já Uazapi) |
| worker_sender.py | MEGA_API_* (21–22 remover), get_instance_status_api MegaAPI (125–166), restart_instance_api (167–192), check_phone MegaAPI (428–533), send_message MegaAPI (563–602). Manter Uazapi paths e _sync_uazapi_usage. |
| worker_cadence.py | MEGA_API_* (33–34), send_text_message (253–268), send_media_message (269–295), process_campaign_sends branch MegaAPI (1508–1518). Manter Uazapi. |
| templates/campaigns_new.html | Checkbox use_uazapi_sender (343); JS useUazapiSender (1155, 1194, etc.). Adicionar checked ou forçar true. |
| services/uazapi.py | Manter; referência para envio |
| migrate_bootstrap_cadence.py | Script standalone com send_text_message/send_media_message próprios (170–210). Não importa worker_cadence. Avaliar deprecated. |
| docker-compose.yml | web, worker, sender, cadence: MEGA_API_URL, MEGA_API_TOKEN (linhas 18–19, 48–49, 76–77, 107–108) |
| .env.example | MEGA_API_URL, MEGA_API_TOKEN — remover ou comentar |

### Technical Decisions

- **Instâncias MegaAPI:** Assumir que não existem. Se existirem, retornar erro amigável nas rotas QR/status/delete ("Instância legada. Crie uma nova instância Uazapi.").
- **Campanhas use_uazapi_sender=false:** O worker_sender deixará de processá-las (query já exclui). Campanhas antigas ficam "órfãs" — não enviarão mais. Documentar no deploy.
- **worker_sender:** Manter o processo rodando apenas para _sync_uazapi_usage. O loop de envio MegaAPI será removido; a query `use_uazapi_sender IS NULL OR false` retornará vazio após a mudança no formulário.
- **UAZAPI_FOR_ALL_USERS_ENABLED:** Pode ser removido ou fixado true; o check em worker_sender deixa de ser relevante quando não houver campanhas MegaAPI.

## Implementation Plan

### Tasks

| # | Task | File | Action |
|---|------|------|--------|
| 1 | Forçar use_uazapi_sender=true no formulário | templates/campaigns_new.html | Ocultar checkbox; definir checked por default; garantir useUazapiSender=true no submit. |
| 2 | Backend: garantir use_uazapi_sender=true | app.py (create_campaign) | Se use_uazapi_sender não vier no body, default true. Validação: exigir instâncias Uazapi (já existe). |
| 3 | Remover MEGA_API_* e branches MegaAPI (worker_sender) | worker_sender.py | Remover MEGA_API_URL, MEGA_API_TOKEN (linhas 21–22). Remover **apenas** branch MegaAPI em: get_instance_status_api (125–166), check_phone_on_whatsapp (428–533), send_message (563–602). **Manter** branches Uazapi (111–123, 408–426, 545–561). |
| 4 | Remover restart_instance_api e branch MegaAPI de verify_and_recover_instance | worker_sender.py | Deletar restart_instance_api (167–192). Em verify_and_recover_instance: remover branch MegaAPI (239–256); se api_provider != 'uazapi', retornar False imediatamente. |
| 5 | Remover send_text_message e send_media_message | worker_cadence.py | Deletar funções (253–295). Remover MEGA_API_URL, MEGA_API_TOKEN (33–34). |
| 6 | process_campaign_sends: remover branch MegaAPI | worker_cadence.py | Remover blocos que chamam send_media_message e send_text_message (1633–1636, 1642–1643). Se api_provider != 'uazapi', logar "Instância MegaAPI ignorada" e continue (pular lead). Manter branch Uazapi (1620–1641). |
| 7 | Remover WhatsappService | app.py | Deletar classe WhatsappService (4334–4468). |
| 8 | Simplificar get_whatsapp_qr, get_whatsapp_status, delete_whatsapp_instance | app.py | Remover branch api_provider == 'megaapi' (que usa WhatsappService). Se api_provider != 'uazapi', retornar 400 com "Instância legada. Crie uma nova instância Uazapi." |
| 9 | Remover MEGA_API do docker-compose | docker-compose.yml, docker-compose.dev.yml | Remover MEGA_API_URL e MEGA_API_TOKEN dos serviços web, worker, sender, cadence. |
| 10 | Remover MEGA_API do .env.example | .env.example | Remover ou comentar MEGA_API_URL, MEGA_API_TOKEN. |
| 11 | Atualizar test_sender_mock.py | tests/test_sender_mock.py | test_check_phone_exists: passar api_provider='uazapi', apikey='fake-token'; @patch('worker_sender.uazapi_service') com mock.check_phone retornando [{'isInWhatsapp': True, 'jid': '5541999@s.whatsapp.net'}]; desempacotar (exists, _, _) = check_phone_on_whatsapp(...). test_send_message: passar api_provider='uazapi', apikey='fake-token'; mockar uazapi_service.send_text para retornar {}. Garantir que os testes passem após remoção do MegaAPI. |

### Acceptance Criteria

| # | AC | Given | When | Then |
|---|-----|-------|------|------|
| AC1 | Nova campanha sempre Uazapi | Formulário de criação | Usuário cria campanha | use_uazapi_sender=true no payload e no DB |
| AC2 | worker_sender não envia MegaAPI | worker_sender rodando | Campanhas use_uazapi_sender=false existentes | Nenhum envio MegaAPI (query retorna vazio) |
| AC3 | Sync Uazapi continua | worker_sender rodando | Campanhas Uazapi ativas | _sync_uazapi_usage executa a cada 10 min; sync_campaign_leads_from_uazapi chamado |
| AC4 | Branches Uazapi mantidos (worker_sender) | get_instance_status_api, check_phone_on_whatsapp, send_message | api_provider='uazapi' | Código Uazapi executado; uazapi_service usado |
| AC5 | Follow-up só via Uazapi | worker_cadence, campanha com cadência | Lead pronto para follow-up | Envio via uazapi_service.send_text ou send_media |
| AC6 | QR/Status/Delete sem MegaAPI | Instância com api_provider=uazapi | Usuário acessa QR/status/delete | UazapiService usado; sem chamada MegaAPI |
| AC7 | Instância MegaAPI retorna erro | Instância com api_provider=megaapi (se existir) | Usuário acessa QR/status/delete | 400 com mensagem "Instância legada" |
| AC8 | Sem MEGA_API em env | Deploy | Container inicia | MEGA_API_URL e MEGA_API_TOKEN não necessários |
| AC9 | Testes passam | pytest tests/test_sender_mock.py | Após alterações | test_check_phone_exists e test_send_message passam (mocks Uazapi) |

## Additional Context

### Dependencies

- UazapiService em services/uazapi.py (manter)
- sync_campaign_leads_from_uazapi em utils/sync_uazapi.py (manter)
- Instâncias devem ter api_provider='uazapi'

### Testing Strategy

- **test_sender_mock.py:** Atualizar test_check_phone_exists e test_send_message para usar api_provider='uazapi' e mockar uazapi_service (check_phone, send_text). Os testes atuais mockam requests.get/post (MegaAPI) e falharão após remoção.
- Teste manual: criar campanha → verificar use_uazapi_sender=true
- Teste manual: QR code, status, delete de instância Uazapi
- Teste manual: follow-up de campanha com cadência (Uazapi)
- Verificar que worker_sender não quebra (_sync_uazapi_usage roda)

### Notes

- **migrate_bootstrap_cadence.py**: Script standalone com send_text_message e send_media_message próprios (não importa worker_cadence). Se ainda usado, migrar para Uazapi ou marcar deprecated. Fora do escopo desta spec.
- **utils/validate_job_csv.py**: Já filtra instâncias Uazapi; sem mudança.
- **project-context.md**: Menciona MegaAPI (linhas 48, 86, 108); atualizar após implementação.
- **verify_and_recover_instance** (worker_sender): Chama restart_instance_api para MegaAPI; ao remover restart, o branch MegaAPI pode ser simplificado (sempre retornar False para api_provider != 'uazapi').

### ⚠️ Dependências Uazapi — NÃO remover (crítico)

**Ver análise completa:** `_bmad-output/analise-dependencias-uazapi-remocao-megaapi.md`

| Componente | Localização | Ação |
|------------|-------------|------|
| UazapiService | services/uazapi.py | Manter |
| sync_campaign_leads_from_uazapi | utils/sync_uazapi.py | Manter |
| get_uazapi_campaign_counts, fetch_all_phones_by_status | utils/sync_uazapi.py | Manter |
| uazapi_service (import) | worker_sender, worker_cadence | Manter |
| _sync_uazapi_usage | worker_sender.py:700–740 | Manter |
| format_jid, jid_to_number, _is_media_path_safe | worker_sender, worker_cadence | Manter |
| **Branches Uazapi** em get_instance_status_api (111–123) | worker_sender.py | Manter |
| **Branches Uazapi** em check_phone_on_whatsapp (408–426) | worker_sender.py | Manter |
| **Branches Uazapi** em send_message (545–561) | worker_sender.py | Manter |
