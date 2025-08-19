# Testando Webhook da Hotmart

Este documento explica como testar a integração com webhooks da Hotmart no sistema Leads Infinitos.

## 📋 Pré-requisitos

1. **Servidor rodando**: O app Flask deve estar rodando
2. **Banco de dados**: O banco SQLite deve estar inicializado
3. **Dependências**: Instalar `requests` se não estiver instalado

## 🚀 Como Testar

### 1. Teste Local (Recomendado para desenvolvimento)

```bash
# 1. Inicie o servidor
python app.py

# 2. Em outro terminal, execute o teste
python test_webhook.py
```

### 2. Teste com URL Pública (Para produção/ngrok)

```bash
# Execute o script interativo
python test_webhook_public.py
```

### 3. Teste Manual com cURL

```bash
curl -X POST http://localhost:8000/webhook/hotmart \
  -H "Content-Type: application/json" \
  -H "X-Hotmart-Signature: test-signature" \
  -d '{
    "event": "SALE_COMPLETED",
    "data": {
      "purchase_id": "TEST-123456",
      "product_id": "5974664",
      "buyer_email": "teste@exemplo.com",
      "purchase_date": "2024-01-15T10:30:00Z",
      "price": "287.00",
      "currency": "BRL",
      "status": "approved"
    }
  }'
```

## 📊 O que o Teste Faz

### 1. Envia Dados Simulados
- Simula uma venda completada da Hotmart
- Usa o produto ID correto (5974664)
- Inclui dados do comprador e da transação

### 2. Verifica o Processamento
- Confirma se o webhook foi recebido (status 200)
- Verifica se os dados foram salvos no banco
- Testa diferentes cenários (anual, semestral, cancelado)

### 3. Valida as Licenças
- Confirma se as licenças foram criadas corretamente
- Verifica o tipo de licença baseado no preço
- Testa a validação de licenças ativas

## 🔍 Verificando os Resultados

### 1. Logs do Servidor
Observe os logs do Flask para ver:
```
INFO:webhook:Webhook recebido - SALE_COMPLETED
INFO:webhook:Licença criada para purchase_id: TEST-123456
```

### 2. Banco de Dados
Verifique as tabelas:
```sql
-- Webhooks recebidos
SELECT * FROM hotmart_webhooks ORDER BY created_at DESC LIMIT 5;

-- Licenças criadas
SELECT * FROM licenses ORDER BY created_at DESC LIMIT 5;
```

### 3. Interface Web
- Acesse `http://localhost:8000/licenses` para ver as licenças
- Teste o registro com o email usado no webhook
- Confirme se a licença está sendo validada

## 🧪 Cenários de Teste

### Venda Anual (R$ 287,00)
- Cria licença anual (365 dias)
- Email: `cliente.anual@exemplo.com`

### Venda Semestral (R$ 147,00)
- Cria licença semestral (180 dias)
- Email: `cliente.semestral@exemplo.com`

### Venda Cancelada
- Não cria licença
- Email: `cliente.cancelado@exemplo.com`

## 🔧 Configuração da Hotmart

### Webhook URL
- **Local**: `http://localhost:8000/webhook/hotmart`
- **Produção**: `https://seu-dominio.com/webhook/hotmart`
- **Ngrok**: `https://abc123.ngrok.io/webhook/hotmart`

### Eventos Configurados
- `SALE_COMPLETED` - Venda aprovada
- `SALE_CANCELLED` - Venda cancelada

### Produto ID
- **ID**: 5974664
- **Nome**: Potencialize sua Prospecção com Extração de Leads do Google Maps

## 🚨 Troubleshooting

### Erro de Conexão
```
❌ Erro de conexão. Certifique-se de que o servidor está rodando
```
**Solução**: Inicie o servidor com `python app.py`

### Erro 404
```
❌ 404 Not Found
```
**Solução**: Verifique se a URL do webhook está correta

### Erro 500
```
❌ 500 Internal Server Error
```
**Solução**: Verifique os logs do servidor para detalhes

### Licença Não Criada
**Verifique**:
1. Se o produto ID está correto (5974664)
2. Se o status da venda é "approved"
3. Se todos os campos obrigatórios estão presentes

## 📝 Próximos Passos

Após o teste bem-sucedido:

1. **Configure o webhook na Hotmart** com a URL correta
2. **Teste com dados reais** de uma venda
3. **Monitore os logs** para garantir que está funcionando
4. **Configure alertas** para falhas no webhook

## 🔐 Segurança

### Validação de Assinatura
O sistema está preparado para validar a assinatura da Hotmart:
- Header: `X-Hotmart-Signature`
- Implementação pendente no código

### Rate Limiting
Considere implementar rate limiting para evitar spam:
```python
# Exemplo básico
from flask_limiter import Limiter
limiter = Limiter(app, key_func=get_remote_address)

@app.route("/webhook/hotmart", methods=["POST"])
@limiter.limit("10 per minute")
def hotmart_webhook():
    # ...
```

## 📞 Suporte

Se encontrar problemas:
1. Verifique os logs do servidor
2. Confirme a configuração da Hotmart
3. Teste com o script fornecido
4. Entre em contato com o suporte
