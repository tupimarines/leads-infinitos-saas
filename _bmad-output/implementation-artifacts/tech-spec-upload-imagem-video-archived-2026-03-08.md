---
title: 'Upload de Imagem e Vídeo nas Campanhas (Uazapi - Superadmin)'
slug: 'upload-imagem-video-campanhas'
created: '2026-03-07'
status: 'Implementation Complete'
stepsCompleted: [1, 2, 3, 4, 5]
tech_stack: ['Flask', 'PostgreSQL', 'Uazapi', 'worker_sender', 'worker_cadence']
files_to_modify: ['app.py', 'worker_sender.py', 'worker_cadence.py', 'services/uazapi.py', 'templates/campaigns_new.html']
code_patterns: ['storage/{user_id}/campaign_media', 'campaign_steps.media_path/media_type', 'is_super_admin()', 'Uazapi POST /send/media']
test_patterns: []
---

# Tech-Spec: Upload de Imagem e Vídeo nas Campanhas (Uazapi - Superadmin)

**Created:** 2026-03-07

## Overview

### Problem Statement

O roadmap do projeto (project-context.md #5) prevê "Upload de imagem e vídeo na mensagem personalizada da campanha". Atualmente:

- **Com cadência**: Os steps 1–4 já têm UI de upload e o backend salva `media_path` em `campaign_steps`. Porém: (a) o **worker_sender** envia apenas texto na primeira mensagem (step 1) — ignora a mídia; (b) o **worker_cadence** envia mídia apenas via MegaAPI — quando a instância é Uazapi, a mídia é pulada (comentário: "Uazapi media em fase posterior").
- **Sem cadência**: Não há upload de mídia na seção principal da mensagem.

**Restrições do escopo:**
- Feature **exclusiva para Uazapi** (não MegaAPI).
- Aplicada **somente para superadmin** inicialmente (validação antes de expandir).
- Deve ser possível anexar mídia em **todos os follow-ups** (steps 1, 2, 3, 4).

### Solution

1. **UazapiService.send_media**: Implementar POST /send/media com `number`, `type` (image|video), `file` (URL ou base64), `text` (caption).
2. **worker_sender**: Quando campanha é do superadmin e instância Uazapi: buscar mídia do step 1 (se cadência) ou da campanha (se sem cadência); se houver `media_path`, enviar via `uazapi_service.send_media` com caption.
3. **worker_cadence**: Quando step tem `media_path` e `api_provider=='uazapi'`: chamar `uazapi_service.send_media` em vez de pular. Aplicar em todos os steps (1–4).
4. **Gate superadmin**: Só processar/enviar mídia quando `campaign.user_id` corresponde ao superadmin (`is_super_admin`).

### Scope

**In Scope:**
- Envio de mídia via **Uazapi** (POST /send/media) — exclusivo Uazapi
- **Superadmin apenas** inicialmente — gate por `is_super_admin()`
- Anexar em **todos os follow-ups** (steps 1, 2, 3, 4) — UI já existe
- Formatos: imagem (JPG preferencialmente) e vídeo (MP4)
- Opcional: upload na mensagem principal (sem cadência) para superadmin

**Out of Scope:**
- MegaAPI (esta feature não altera MegaAPI)
- Campanhas `use_uazapi_sender=true` (create_advanced_campaign)
- Áudio, documento, sticker (Uazapi suporta; deixar para extensão futura)
- Usuários não-superadmin (expansão posterior)

## Context for Development

### Codebase Patterns

- **Storage**: `storage/{user_id}/campaign_media/` — já usado para steps; `_is_path_owned_by_current_user(path)` para validar.
- **campaign_steps**: `media_path`, `media_type` ('image' ou 'video'); `message_template` JSON.
- **is_super_admin(user)**: email `augustogumi@gmail.com` — app.py linha ~620.
- **worker_cadence**: atualmente pula mídia quando `api_provider=='uazapi'`.
- **worker_sender**: não consulta `campaign_steps`; envia só texto.

### Uazapi POST /send/media (Spec Completa)

| Campo | Tipo | Obrigatório | Descrição |
|-------|------|-------------|-----------|
| number | string | sim | Número formato internacional (ex: "5511999999999") |
| type | string | sim | image, video, document, audio, myaudio, ptt, ptv, sticker |
| file | string | sim | URL ou base64 do arquivo |
| text | string | não | Caption/legenda (aceita placeholders) |
| docName | string | não | Nome do arquivo (para documents) |
| thumbnail | string | não | URL ou base64 de thumbnail (vídeos/documentos) |
| mimetype | string | não | MIME type (detectado automaticamente) |
| delay | integer | não | Atraso em ms antes do envio |
| async | boolean | não | Envio assíncrono via fila |

**Exemplos (curl — base: https://neurix.uazapi.com/send/media, header: token):**

**Imagem:**
```json
{"number": "5511999999999", "type": "image", "file": "https://exemplo.com/foto.jpg"}
```

**Imagem com legenda:**
```json
{"number": "5511999999999", "type": "image", "file": "https://exemplo.com/foto.jpg", "text": "Veja esta foto!"}
```

**Vídeo:**
```json
{"number": "5511999999999", "type": "video", "file": "https://exemplo.com/video.mp4", "text": "Confira este vídeo!"}
```

**Documento (PDF, DOC):**
```json
{"number": "5511999999999", "type": "document", "file": "https://exemplo.com/contrato.pdf", "docName": "Contrato.pdf", "text": "Segue o documento solicitado"}
```

**Nota:** `file` aceita URL ou base64. Para arquivos locais (storage), converter para base64 antes de enviar.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| app.py | Criação de campanha; rota POST /api/campaigns; salvar steps e media; init_db (campaigns.media_path, media_type) |
| worker_sender.py | Envio da primeira mensagem; send_message; buscar step 1 ou media da campanha |
| worker_cadence.py | Envio follow-ups; send_media_message; uazapi send_media |
| services/uazapi.py | UazapiService; adicionar send_media |
| templates/campaigns_new.html | UI upload na seção principal; handleMediaUpload; payload data |
| uazapi-openapi-spec (1).yaml | POST /send/media (linhas ~3885–3995) |

### Technical Decisions

- **Gate superadmin**: Só enviar mídia quando `user_id` da campanha corresponde ao superadmin. Usar `is_super_admin()` com `user_id` (buscar user por id ou passar contexto).
- **Uazapi file**: Converter `media_path` (arquivo local) para base64 antes de enviar; ou enviar como `data:image/jpeg;base64,{data}`.
- **Ordem de envio**: Quando há mídia, enviar mídia com caption (texto); não enviar texto separado.
- **UI steps 1–4**: Já existe; não alterar. Opcional: exibir upload na seção principal apenas quando `is_super_admin` (se implementar campanha sem cadência).

## Implementation Plan

### Tasks

| # | Task | File(s) | Action | Status |
|---|------|---------|--------|--------|
| 1 | **UazapiService.send_media** | services/uazapi.py | Método `send_media(token, number, media_type, file, caption="")`. POST /send/media. `file`: path local → ler e converter para base64; ou string base64/URL. Payload: `number`, `type` (image\|video), `file`, `text` (caption). Header: `token`. | [x] |
| 2 | **worker_sender**: Gate superadmin + mídia step 1 | worker_sender.py | Se `user_id` não é superadmin: ignorar mídia (comportamento atual). Se superadmin + Uazapi + enable_cadence: buscar step 1 de campaign_steps; se media_path: enviar uazapi_service.send_media com caption=message_text; senão send_message. | [x] |
| 3 | **worker_sender**: Mídia mensagem principal (sem cadência) | worker_sender.py | Se superadmin + Uazapi + enable_cadence=false: buscar campaigns.media_path (requer colunas). Se media_path: send_media. (Opcional: adicionar colunas e UI se escopo incluir.) | [ ] Opcional |
| 4 | **worker_cadence**: Uazapi send_media em todos os steps | worker_cadence.py | Remover condição `api_provider != 'uazapi'`. Quando step_config.get('media_path') e api_provider=='uazapi': chamar uazapi_service.send_media (file=path→base64, caption=message). Enviar apenas mídia com caption (não send_text em seguida). Gate: só se user_id é superadmin. | [x] |
| 5 | **Gate superadmin nos workers** | worker_sender.py, worker_cadence.py | Obter user_id da campanha; `SELECT email FROM users WHERE id = %s`; comparar com `SUPER_ADMIN_EMAIL` (worker_sender já tem essa constante). worker_cadence: adicionar mesma lógica. Só processar mídia quando email == SUPER_ADMIN_EMAIL. | [x] |
| 6 | **Migração DB** (opcional) | app.py | Se incluir mensagem principal sem cadência: `ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS media_path TEXT, media_type TEXT`. | [ ] Opcional |
| 7 | **UI + Backend mensagem principal** (opcional) | templates/campaigns_new.html, app.py | Se escopo incluir: upload na seção principal visível apenas para `is_super_admin`; salvar em campaigns.media_path. | [ ] Opcional |

## Acceptance Criteria

### AC1: Superadmin — step 1 com mídia (Uazapi)
- **Given** campanha do superadmin com cadência, instância Uazapi, step 1 com imagem anexada
- **When** worker_sender processa o lead
- **Then** lead recebe imagem com caption no WhatsApp via Uazapi

### AC2: Superadmin — follow-ups 2, 3, 4 com mídia (Uazapi)
- **Given** campanha do superadmin com cadência, instância Uazapi, step 2 (ou 3, 4) com vídeo anexado
- **When** worker_cadence processa lead nesse step
- **Then** lead recebe vídeo com caption no WhatsApp via Uazapi

### AC3: Não-superadmin — mídia ignorada
- **Given** campanha de usuário não-superadmin com step 1 com mídia
- **When** worker_sender processa
- **Then** lead recebe apenas texto (mídia não é enviada; comportamento atual preservado)

### AC4: Regressão — campanha sem mídia
- **Given** campanha criada sem mídia (superadmin ou não)
- **When** worker processa
- **Then** lead recebe apenas texto

### AC5: Uazapi POST /send/media
- **Given** arquivo salvo em storage (JPG ou MP4)
- **When** UazapiService.send_media é chamado
- **Then** request usa `type: image` ou `type: video`, `file` em base64 ou URL, `text` como caption

### AC6: Validação de path
- **Given** media_path fora de storage do usuário
- **When** worker tenta enviar
- **Then** envio falha ou path não é usado (segurança)

## Additional Context

### Dependencies

- Uazapi POST /send/media (spec completa no Overview)
- **Superadmin check nos workers**: worker_sender já usa `SUPER_ADMIN_EMAIL = 'augustogumi@gmail.com'` e consulta `SELECT email FROM users WHERE id = %s` para comparar. worker_cadence pode replicar o mesmo padrão (query user por campaign.user_id).

### Testing Strategy

- Teste manual: superadmin, campanha com cadência, step 1 com imagem → verificar envio Uazapi
- Teste manual: superadmin, follow-up step 2 com vídeo → verificar envio
- Teste manual: usuário não-superadmin com mídia → deve receber só texto
- Teste de regressão: campanha sem mídia continua enviando texto

### Notes

- Expansão futura: remover gate superadmin para disponibilizar a todos os usuários com Uazapi.
- Campanhas `use_uazapi_sender=true` (create_advanced_campaign) permanecem apenas texto.
- Uazapi suporta document, audio, sticker; focar em image e video nesta fase.
