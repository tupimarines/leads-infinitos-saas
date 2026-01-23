# 🏗️ D2 Implementation Plan (22/01/2026)

Este documento detalha o plano de execução para o **Dia 2** (Quinta-feira), focado em conectar o sistema ao mundo externo (Email, WhatsApp) e aplicar as regras de negócio.

## Contexto
Temos a fundação (Postgres, Redis, Workers) pronta. Agora precisamos que o SaaS:
1.  **Fale com o usuário** (Recuperação de senha via SMTP).
2.  **Fale com o cliente do usuário** (WhatsApp via Evolution API).
3.  **Respeite os limites** (Regras de planos).

---

## 📅 Atividades do Dia

### 1. SMTP & Recuperação de Senha (Gmail)
Configurar disparos de email transacionais essenciais para um SaaS.

**Credenciais:**
- Conta: `localgmn41@gmail.com`
- App Password: `fllt xlhs wogc vipr`

**Passos:**
- [x] **Configuração:** Adicionar `Flask-Mail` com settings do Gmail no `app.py`.
- [x] **Rota:** Criar/Validar rota `/forgot-password` e `/reset-password/<token>`.
- [x] **Teste:** Enviar email real de recuperação.

> [!NOTE]
> **Audit Fixes (Realizado):**
> - **Async Email:** Implementado worker dedicado (`worker_email.py`) e uso de Redis Queue (RQ) para envio não-bloqueante.
> - **Segurança:** Credenciais movidas para `.env` e vulnerabilidade de enumeração de usuários corrigida (mensagens genéricas de sucesso).
> - **Infra:** `docker-compose.yml` atualizado com serviços `web` e `worker` separados.

### 2. Regras de Negócio: Planos & Limites
Implementar os limites definidos na oferta.

**Tabela de Planos:**
| Plano | Limite Diário |
| :--- | :---: |
| **Starter** | 10 mensagens/dia |
| **Pro** | 20 mensagens/dia |
| **Scale** | 30 mensagens/dia |

**Passos:**
- [x] **Modelagem:** Adicionar campo `daily_limit` ou método na classe `License` para retornar o limite baseado no `license_type` ou valor hardcoded por enquanto.
    - *Nota:* O Roadmap sugere "Semestral=500" vs "Anual=2000", mas a imagem do usuário mostra "Starter/Pro/Scale". Vamos seguir a **IMAGEM** (10/20/30) para o MVP pois é o que está visualmente definido, ou questionar se o user prefere os limites maiores. *Assumiremos 10/20/30 para teste rápido, mas parametrizável.*
- [x] **Worker:** Atualizar `check_daily_limit` em `worker_sender.py` para ler esse limite dinamicamente.

### 3. Tela de Configuração WhatsApp
Permitir que o usuário conecte sua instância da Mega API.

**API Não-Oficial (Mega API):**
- **URL Base:** `https://ruker.megaapi.com.br`
- **Token:** `Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIyMC8xMS8yMDI1IiwibmFtZSI6IlJ1a2VyIiwiYWRtaW4iOnRydWUsImlhdCI6MTc2Mzc0MTQ2OH0.98QXHyajSnH-jUb0nvFNo9GRNMrOX1WDbNejCjJCO08`
- **Fluxo Confirmado (Implementado):**
    - `POST /rest/instance/init?instance_key={NAME}`: Cria nova instância.
        - **Payload Obrigatório:** `{"messageData": {"webhookUrl": "", "webhookEnabled": true}}`
    - `GET /rest/instance/qrcode/{NAME}`: Retorna HTML (`<img src="data:image...">`). App extrai a string Base64.
    - `GET /rest/instance/{NAME}`: Retorna lista `[{instance: {status: 'connected'}}]`.

**Passos:**
- [x] **Backend (`app.py`):**
    - Atualizar variáveis de ambiente ou constantes com a URL e Token da Mega API.
    - Rota `GET /api/whatsapp/status`: Proxy para `GET /rest/instance/{key}` (Parse de Lista).
    - Rota `GET /api/whatsapp/qr`: Proxy para `GET /rest/instance/qrcode/{key}` (Parse HTML/Base64).
    - Rota `POST /api/whatsapp/init`: Integração completa com regra de **1 Instância por Usuário**.
- [x] **Frontend (`templates/whatsapp_config.html`):**
    - Card com Status (Conectado/Desconectado).
    - Botão "Criar/Reconfigurar Instância" (Bloqueia se já existir, exigindo validação).
    - Botão "Gerar QR Code".
    - Exibição do QR Code e refresh automático do status.

### 4. Engine de Disparo (V1)
Refinar o `worker_sender.py` para comportar lógica humana (Antiban básico).

**Referências (`disparador-leads.json`):**
- Lógica de Variação de Mensagem (Spintax/Random Array).
- Delays Aleatórios.
- Verificação de "Já enviado".

**Passos:**
- [x] **Variação de Mensagem:** Implementar lógica que escolhe aleatoriamente uma mensagem de uma lista fornecida (JSON array no `campaigns.message_template`).
- [x] **Wait:** Garantir `time.sleep(random.randint(min, max))` entre envios.
- [x] **Error Handling:** Tratar desconexão da instância durante o envio (pausar campanha?).

---

## 🚀 Como Executar
Seguiremos a ordem sequencial acima. Cada passo deve ser validado individualmente.
