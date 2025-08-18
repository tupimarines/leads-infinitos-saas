# Integração com Hotmart - Leads Infinitos

## Visão Geral

Este documento descreve a integração do sistema Leads Infinitos com a plataforma Hotmart para validação de licenças e controle de acesso.

## Funcionalidades Implementadas

### 1. Validação de Compra no Registro
- ✅ Usuário se registra com email
- ✅ Sistema verifica se email tem compra válida na Hotmart
- ✅ Cria licença automaticamente baseada no valor da compra
- ✅ Bloqueia registro se não houver compra

### 2. Tipos de Licença
- **Licença Semestral**: R$ 195,00 (180 dias)
- **Licença Anual**: R$ 287,00 (365 dias)

### 3. Controle de Acesso
- ✅ Verificação de licença ativa antes de usar o scraper
- ✅ Página de visualização de licenças
- ✅ Status de licença em tempo real na interface

### 4. Webhook da Hotmart
- ✅ Endpoint para receber notificações de vendas
- ✅ Processamento automático de novas compras
- ✅ Criação automática de licenças para usuários existentes

## Configuração

### 1. Credenciais da Hotmart
As seguintes credenciais já estão configuradas no sistema:

```
Client ID: cb6bcde6-24cd-464f-80f3-e4efce3f048c
Client Secret: 7ee4a93d-1aec-473b-a8e6-1d0a813382e2
Product ID: 5974664
```

### 2. Configurar Webhook

#### Passo 1: Executar script de configuração
```bash
python setup_hotmart_webhook.py
```

#### Passo 2: Criar webhook na Hotmart
1. Acesse: https://developers.hotmart.com/webhooks
2. Clique em "Criar Webhook"
3. Configure:
   - **Nome**: Leads Infinitos Webhook
   - **URL**: `https://seudominio.com/webhook/hotmart` (em produção)
   - **Versão**: 2.0.0 (Recomendado)
   - **Eventos**: SALE_COMPLETED
4. Salve o webhook
5. Copie o "Hottok de verificação" (webhook secret)

#### Passo 3: Atualizar webhook secret
```bash
python update_webhook_secret.py <SEU_WEBHOOK_SECRET>
```

## Estrutura do Banco de Dados

### Tabela `licenses`
```sql
CREATE TABLE licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    hotmart_purchase_id TEXT UNIQUE NOT NULL,
    hotmart_product_id TEXT NOT NULL,
    license_type TEXT NOT NULL CHECK (license_type IN ('semestral', 'anual')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'cancelled')),
    purchase_date TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
```

### Tabela `hotmart_webhooks`
```sql
CREATE TABLE hotmart_webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    hotmart_purchase_id TEXT,
    payload TEXT NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Tabela `hotmart_config`
```sql
CREATE TABLE hotmart_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    client_secret TEXT NOT NULL,
    webhook_secret TEXT,
    product_id TEXT NOT NULL,
    sandbox_mode BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Fluxo de Funcionamento

### 1. Registro de Usuário
```
Usuário → Registra com email → Sistema verifica Hotmart → 
Se compra válida → Cria usuário + licença → Login
Se não comprou → Bloqueia registro
```

### 2. Uso do Sistema
```
Usuário → Tenta usar scraper → Sistema verifica licença → 
Se licença ativa → Permite uso
Se licença expirada → Bloqueia acesso
```

### 3. Nova Venda (Webhook)
```
Hotmart → Venda aprovada → Webhook → Sistema processa → 
Se usuário existe → Cria licença
Se usuário não existe → Aguarda registro
```

## Endpoints da API

### 1. Verificação de Licença
```
GET /api/verify-license
Headers: Authorization (session)
Response: {"has_active_license": true/false}
```

### 2. Webhook da Hotmart
```
POST /webhook/hotmart
Headers: X-Hotmart-Signature
Body: JSON payload da Hotmart
Response: {"status": "success"}
```

### 3. Página de Licenças
```
GET /licenses
Headers: Authorization (session)
Response: HTML com lista de licenças
```

## Testes

### 1. Teste de Conexão
```bash
python setup_hotmart_webhook.py
```

### 2. Teste de Registro
1. Faça uma compra de teste na Hotmart
2. Tente se registrar com o email da compra
3. Verifique se a licença foi criada

### 3. Teste de Webhook
1. Configure o webhook
2. Faça uma nova compra
3. Verifique os logs em `/webhook/hotmart`

## Monitoramento

### Logs de Webhook
Os webhooks recebidos são salvos na tabela `hotmart_webhooks` para auditoria.

### Status de Licenças
- Verificação automática de expiração
- Interface mostra status em tempo real
- Bloqueio automático de acesso expirado

## Troubleshooting

### Erro: "Email não encontrado em nossas vendas"
- Verifique se o email está correto
- Confirme se a compra foi aprovada na Hotmart
- Verifique se o Product ID está correto

### Erro: "Falha na conexão com Hotmart"
- Verifique as credenciais (Client ID/Secret)
- Confirme se a API está acessível
- Verifique se não há bloqueio de firewall

### Webhook não está sendo recebido
- Verifique se a URL está correta
- Confirme se o webhook está ativo na Hotmart
- Verifique se o servidor está acessível publicamente

## Próximos Passos

### Melhorias Planejadas
- [ ] Validação de assinatura do webhook
- [ ] Renovação automática de licenças
- [ ] Notificações de expiração
- [ ] Dashboard de vendas
- [ ] Relatórios de uso

### Produção
- [ ] Configurar HTTPS
- [ ] Usar URL de produção para webhook
- [ ] Configurar monitoramento
- [ ] Backup automático do banco
