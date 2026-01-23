# 🏗️ D3 Implementation Plan (23/01/2026)

Este documento detalha o plano de execução para o **Dia 3** (Sexta-feira), focado em integração final (Hotmart) e experiência de uso (Campanhas).

## Contexto
Temos o backend de disparos pronto (D2). Agora precisamos fechar o ciclo de venda (Webhooks Hotmart) e dar uma interface para o usuário usar o sistema (UI de Campanhas).

---

## 📅 Atividades do Dia

### 1. Integração de Pagamentos (Hotmart)
**Prioridade Zero:** Validar o fluxo de webhooks para garantir que vendas gerem licenças automaticamente.

**Considerações de Setup:**
- **URL de Produção:** `https://leads-infinitos.wbtech.dev/api/webhooks/hotmart`
- **URL Local (Dev):** Necessário usar `ngrok` ou similar para receber requests reais, ou usar scripts de simulação (Mock).
- **Dados Necessários (Hotmart):**
    - **Hottok:** Token de verificação (configurar em `.env`).
    - **Eventos:** `PURCHASE_APPROVED` (venda aprovada) e `PURCHASE_COMPLETE`.
    - **Versão:** 2.0.0.

**Passos de Implementação:**
- [ ] **Data Model & Env:**
    - [ ] Adicionar `HOTMART_HOTTOK` no `.env`.
- [ ] **Webhook Route (`app.py`):**
    - [ ] Criar endpoint `POST /api/webhooks/hotmart`.
    - [ ] Validar `X-Hotmart-Hottok`.
    - [ ] Implementar lógica de **Criação Automática de Usuário** (se email não existir, criar com senha temporária).
    - [ ] Criar/Atualizar Licença (`licenses` table) garantindo idempotência.
- [ ] **Testes:**
    - [ ] Criar script de Mock (simular payload JSON da Hotmart localmente).
    - [ ] Validar criação de registros no banco.

### 2. UI de Campanhas (Frontend)
Criar a interface onde o usuário define *para quem* e *o que* vai enviar.

**Passos:**
- [ ] **Seleção de Leads:**
    - [ ] Criar endpoint `GET /api/scraping-jobs` para listar extrações anteriores.
    - [ ] Adicionar `<select>` na tela de campanha para escolher uma lista extraída.
    - [ ] Adicionar opção de **Upload CSV** (Fallback) com validação de colunas (Nome, Telefone).
- [ ] **Visualização:** Mostrar prévia da quantidade de contatos na lista selecionada.

### 3. Configuração de Disparo
Configurar os parâmetros finais antes de iniciar a campanha.

**Passos:**
- [ ] **Inputs de Agendamento:**
    - [ ] Campos de Data/Hora de início (opcional, default = agora).
- [ ] **Botão "Iniciar Campanha":**
    - [ ] Postar dados para `POST /api/campaigns`.
    - [ ] Backend: Criar registro em `campaigns` e popular `campaign_leads`.
    - [ ] Feedback visual (Toast/Redirect) para o usuário.

---

## 🚀 Como Executar
Seguiremos a ordem: **Hotmart** -> **UI de Campanhas** -> **Lógica de Criação**.
