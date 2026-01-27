# 🏗️ D4 Implementation Plan (25/01/2026)

Este documento detalha o plano de execução para o **Dia 4** (Sábado), focado em IA, polish e testes de carga.

## Contexto

Temos:
- ✅ Fundação (Postgres, Redis, Workers)
- ✅ Conectividade WhatsApp (Mega API)
- ✅ Webhook Hotmart
- ✅ UI de Campanhas com seleção de listas

**Pendências críticas para MVP:**
- 🤖 IA para geração de copy
- ✨ Dashboard com métricas reais
- 🧪 Validação do worker_sender em produção
- 🔧 Testes de carga

---

## 📅 Atividades do Dia

### 1. Assistente de IA (Geração de Copy)

**Objetivo:** Integrar IA para gerar variações de mensagens persuasivas automaticamente.

#### 🔧 Alterações Necessárias

##### Backend (`app.py`)

- [ ] **Criar endpoint `POST /api/ai/generate-copy`**
  - Recebe: `{ "business_context": "...", "product_service": "..." }`
  - Retorna: `{ "messages": ["variação 1", "variação 2", "variação 3"] }`
  - Usa OpenAI/Anthropic com prompt otimizado para:
    - Mensagens curtas (2-4 linhas)
    - Linguagem persuasiva e natural
    - Anti-bloqueio (variações significativas)
    - Adequadas para cold-outreach via WhatsApp

- [ ] **Configuração de API Keys**
  - Adicionar `OPENAI_API_KEY` ou `ANTHROPIC_API_KEY` no `.env`
  - Instalar dependências: `openai` ou `anthropic`

##### Frontend (`templates/campaigns_new.html`)

**IMPORTANTE:** A UI já possui o botão "Criar com IA" (linha 152-159). As modificações são:

- [ ] **Campo de Contexto do Negócio**
  - Adicionar **abaixo** da seleção de leads (linha ~141):
  - Textarea: "Fale mais sobre o seu negócio e o produto ou serviço que você vende"
  - Placeholder: "Ex: Sou dentista em São Paulo. Oferecemos limpeza, clareamento e aparelhos ortodônticos..."
  
- [ ] **Comportamento do botão "Criar com IA"**
  - Ao clicar: Enviar `business_context` para `/api/ai/generate-copy`
  - Receber 1 mensagem gerada
  - **Inserir no último template vazio ou criar novo**
  
- [ ] **Integração com "Adicionar variação (Spin)"** (linha 170-177)
  - Quando usuário clica em "Adicionar variação (Spin)":
    - Criar novo textarea vazio
    - Também adicionar mini-botão ✨ ao lado do novo textarea
    - Ao clicar no mini-botão: Gerar nova mensagem com IA e preencher apenas aquele campo

---

### 2. Dashboard com Métricas Reais ✅ **CONCLUÍDO**

**Objetivo:** Exibir estatísticas vinculadas a cada campanha.

#### � Resumo da Implementação

**✅ Implementado (25/01/2026):**

- **Database:** 
  - Adicionada coluna `closed_deals` na tabela `campaigns` para rastrear negócios fechados
  - Migração automática segura com `IF NOT EXISTS`

- **Backend - 3 Novos Endpoints:**
  - `GET /api/campaigns/{id}/stats` - Retorna métricas completas incluindo conversão
  - `POST /api/campaigns/{id}/deal` - Incrementa/decrementa negócios fechados
  - `GET /api/dashboard/overview` - Estatísticas gerais do usuário

- **Frontend - Lista de Campanhas (`/campaigns`):**
  - Redesign completo com cards glassmorphism
  - Métricas em tempo real: total, enviados, pendentes, falhas
  - Progressbar visual animada
  - Taxa de sucesso calculada: `(sent / (sent + failed + invalid)) * 100`
  - **Botões +/- para rastrear negócios fechados**
  - **Taxa de conversão: `(closed_deals / sent) * 100`**
  - Polling automático a cada 5s para campanhas `running`

- **Frontend - Dashboard Geral (`/dashboard`):**
  - 6 widgets de métricas: leads extraídos, mensagens enviadas, taxa de sucesso, campanhas ativas, total de negócios fechados, taxa de conversão geral
  - Auto-refresh a cada 30s
  - Ações rápidas para navegação

- **Navegação:**
  - Link "Dashboard" adicionado ao menu principal
  - Rotas `/campaigns` e `/dashboard` criadas

**📊 Funcionalidades Principais:**
- Rastreamento de negócios fechados por campanha
- Cálculo automático de taxa de conversão
- Métricas agregadas em dashboard geral
- Atualização em tempo real sem refresh manual

---

### 3. Validação do Worker Sender ✅ **CONCLUÍDO**

**Problema Identificado:**  
O `docker-compose.yml` (linha 61) executa `python worker.py`, que é um worker **RQ genérico** para tarefas assíncronas (scraping, email).

O `worker_sender.py` é um **loop dedicado** que processa campanhas continuamente.

#### ✅ Análise

**worker_sender.py precisa continuar rodando?**  
**SIM**, porque:
1. Agora a extração é terceirizada para Apify, logo `worker_scraper.py` pode ser removido ou desativado
2. Mas `worker_sender.py` é essencial para disparos contínuos de campanhas

#### ✅ Implementação Realizada (25/01/2026)

- [x] **Atualizado `docker-compose.yml`**
  - Adicionado novo serviço `sender` conforme especificação
  - Configuradas todas as variáveis de ambiente necessárias
  - Dependências com healthcheck configuradas
  
- [x] **Mantido `worker` para RQ (email assíncrono)**
  - O `worker.py` atual continua útil para emails não-bloqueantes
  - `worker_scraper.py` mantido (ainda usado via RQ)

- [x] **Suporte completo a dados do Apify**
  - Adicionada coluna `whatsapp_link` ao banco (`campaign_leads`)
  - Backend atualizado para armazenar `whatsapp_link`
  - Worker_sender prioriza `whatsapp_link` sobre `phone_number`
  - Função `extract_phone_from_whatsapp_link()` criada

- [x] **Sistema de Coluna Status**
  - Planilhas Apify agora exportam com `status = 1`
  - Upload CSV adiciona coluna status automaticamente
  - Apenas leads com `status = 1` são importados para campanhas
  
- [x] **Personalização de Mensagens**
  - Suporte a variáveis `{nome}` e `{name}` implementado
  - Substituição automática no worker_sender

- [x] **Bug Fixes**
  - Corrigido `Campaign.__init__()` para aceitar `closed_deals`

**📊 Arquivos Modificados:**
- `docker-compose.yml` - Serviço sender adicionado
- `app.py` - Schema, CampaignLead, status column handling
- `worker_sender.py` - WhatsApp link parsing + personalização
- `main.py` - Exportação com status column

**📝 Documentação Criada:**
- `walkthrough.md` - Detalhes técnicos completos
- `status_column_system.md` - Sistema de coluna status
- `task_step3_analysis.md` - Análise inicial

---

### 4. Checklist de Teste Manual End-to-End (E2E)

**Objetivo:** Validar o fluxo completo do sistema, desde a aquisição até a execução de campanhas complexas, garantindo eficácia real.

#### 📋 Fluxo Principal do Usuário (Single User)

- [x] **1. Compra Hotmart (Simulação)**
  - [x] Simular webhook de compra aprovada via Postman/Insomnia.
  - [x] Verificar se usuário foi criado no banco.
  - [x] Verificar se licença foi atribuída corretamente.
  
- [x] **2. Acesso Inicial & Reset de Senha**
  - [x] Acessar `leads infinitos` (login).
  - [x] Tentar login com senha padrão (se houver) ou usar "Esqueci minha senha".
  - [x] Verificar recebimento do token de reset.

- [x] **3. Fluxo de Email SMTP**
  - [x] Confirmar recebimento do email de reset (SMTP configurado).
  - [x] Clicar no link e definir nova senha.
  - [x] Logar com nova senha.

- [ ] **4. Acesso ao Dashboard**
  - [x] Verificar se dashboard carrega zerado (primeiro acesso).
  - [] Validar permissões de visualização.
  - [] Verificar atualização do dashboard e percentual tx de sucesso

- [ ] **5. Extração de Leads (Apify Integration)**
  - Criar novo Job de extração (Google Maps Scraper).
  - Aguardar conclusão.
  - Verificar se leads foram salvos no banco.

- [ ] **6. Upload de Arquivo Manual**
  - Preparar CSV/Excel com leads (incluir coluna status/whatsapp se necessário).
  - Fazer upload na tela de campanhas/leads.
  - Verificar importação correta dos dados.

- [ ] **7. Criação de Campanhas Mistas**
  - **Campanha A (Instantânea):** Usar lista extraída (passo 5). Início imediato.
  - **Campanha B (Agendada):** Usar lista de upload (passo 6). Início agendado para +10 min.
  - Validar configuração de mensagens e variáveis.

- [ ] **8. Execução de Disparos**
  - Verificar logs do `worker_sender`.
  - Confirmar se Campanha A iniciou imediatamente.
  - Confirmar se Campanha B aguardou o horário.
  - Comparar disparos realizados vs lista de leads (conferência visual/banco).

- [ ] **9. Dashboard & Métricas em Tempo Real**
  - Verificar atualização dos contadores (Enviados, Pendentes, Falhas).
  - Confirmar se "Negócios Fechados" podem ser alterados manualmente.
  - Validar cálculos de taxas.

- [ ] **10. Exportação de Dados**
  - Baixar lista extraída (CSV/Excel).
  - Verificar integridade dos dados exportados.

- [ ] **11. Gestão de Jobs (Limpeza)**
  - Excluir jobs de extração antigos.
  - Verificar se dados relacionados foram tratados corretamente (cascade ou mantidos conforme regra).

- [ ] **12. Gestão de Campanhas (Limpeza)**
  - Excluir as campanhas criadas.
  - Verificar limpeza no banco de dados.

- [ ] **13. Limites do Plano (Starter)**
  - Simular compra de plano Starter (limite diário restrito).
  - Tentar enviar + mensagens que o limite.
  - Verificar bloqueio/pausa automática da campanha.

---

#### 🧪 Teste de Concorrência e Fila (Multi-Campaigns)

**Este teste é crítico para validar o `worker_sender` e o isolamento de dados.**

- [ ] **14. Simulação de 3 Campanhas Simultâneas**
  - **Setup:** Criar 3 usuários diferentes (User A, User B, User C).
  - **Ação:** Iniciar 1 campanha para cada usuário SIMULTANEAMENTE (aprox. mesmo horário).
  - **Verificações Críticas:**
    - [ ] **Fila:** O sistema engasgou? (Verificar uso de CPU/Memória do container sender).
    - [ ] **Isolamento:** User A enviou leads do User B? (JAMAIS pode acontecer).
    - [ ] **Concorrência:** As 3 campanhas progrediram ou uma bloqueou as outras?
    - [ ] **Worker:** Monitorar logs para garantir que o loop está iterando entre as campanhas (Round-robin ou paralelo).

---

#### ⚙️ Testes Administrativos e Conta

- [ ] **15. Dashboard (Funcionalidades Avançadas)**
  - Testar filtros de data.
  - Testar widgets de performance.

- [ ] **16. Super Admin**
  - Acessar painel de admin (se houver rota dedicada ou via Django Admin).
  - Listar todos os usuários.
  - Alterar planos manualmente.
  - Ver estatísticas globais.

- [ ] **17. Minha Conta**
  - Alterar dados cadastrais.
  - Alterar senha novamente dentro da área logada.
  - Verificar logout.

---

## 🚀 Ordem de Execução Recomendada

1. **Manhã (4-5h):**
   - Implementar IA (Backend + Frontend)
   - Teste manual de geração de copy

2. **Tarde (2-3h):**
   - Dashboard de métricas
   - Correção do docker-compose (worker_sender)
   - Deploy em staging

3. **Noite (1-2h):**
   - Testes de carga
   - Ajustes emergenciais

---

## 📦 Dependências a Adicionar

No `requirements.txt`:
```txt
openai>=1.0.0  # ou anthropic>=0.8.0
```

No `.env`:
```bash
OPENAI_API_KEY=sk-...
# ou
ANTHROPIC_API_KEY=sk-ant-...
```

---

## ⚠️ Bloqueadores Conhecidos

1. **API Key de IA:** Precisa ser configurada antes de testar
2. **Testes Reais de WhatsApp:** Requer números válidos (não consumir números de clientes reais)
3. **Mega API Rate Limits:** Confirmar se há limite de requests/min

---

## ✅ Critérios de Sucesso

- [ ] Usuário consegue gerar mensagens com IA em 1 clique
- [ ] Dashboard mostra métricas em tempo real
- [ ] Worker_sender está rodando no Docker
- [ ] Sistema aguenta 5 campanhas simultâneas sem crash
- [ ] Limites diários funcionam corretamente

---

## 🎯 Próximos Passos (Dia 5)

- Bateria final de testes E2E
- Documentação de uso para clientes
- Preparação para lançamento MVP
