#!/bin/bash
# Script para testar webhook com cURL

echo "🧪 Testando webhook com cURL..."
echo "URL: http://localhost:8000/webhook/hotmart"
echo ""

# Dados do webhook no formato real da Hotmart
curl -X POST http://localhost:8000/webhook/hotmart \
  -H "Content-Type: application/json" \
  -H "X-Hotmart-Signature: test-signature" \
  -d '{
    "id": "test-webhook-123",
    "creation_date": 1755543671489,
    "event": "PURCHASE_COMPLETE",
    "version": "2.0.0",
    "data": {
      "product": {
        "id": 5974664,
        "name": "Potencialize sua Prospecção com Extração de Leads do Google Maps"
      },
      "buyer": {
        "email": "teste@exemplo.com",
        "name": "Usuário Teste"
      },
      "purchase": {
        "approved_date": 1755543671489,
        "price": {
          "value": 287.00,
          "currency_value": "BRL"
        },
        "status": "COMPLETED",
        "transaction": "HP-TEST-123456"
      }
    },
    "hottok": "test-hottok"
  }'

echo ""
echo "✅ Teste concluído!"
