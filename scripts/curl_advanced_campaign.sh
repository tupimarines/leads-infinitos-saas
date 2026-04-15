#!/bin/bash
# Campanha avançada Uazapi - 4 números, intervalo mínimo
# Uso: ./scripts/curl_advanced_campaign.sh

TOKEN="a7f9d434-c214-44b1-8c53-4c26ead90f97"
URL="https://neurix.uazapi.com/sender/advanced"

# delayMin/delayMax em segundos - menor intervalo possível (1-2s)
# scheduled_for: 1 = enviar imediatamente
PAYLOAD='{
  "delayMin": 1,
  "delayMax": 2,
  "info": "teste-curl-4numeros",
  "scheduled_for": 1,
  "messages": [
    {"number": "554137984981", "type": "text", "text": "Teste Maria"},
    {"number": "554137984019", "type": "text", "text": "Teste Ana"},
    {"number": "554137984966", "type": "text", "text": "Teste João"},
    {"number": "554137984741", "type": "text", "text": "Teste Pedro"}
  ]
}'

echo "=== Payload ==="
echo "$PAYLOAD" | python -m json.tool 2>/dev/null || echo "$PAYLOAD"
echo ""
echo "=== Response ==="
curl -s -X POST "$URL" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -H "token: $TOKEN" \
  -d "$PAYLOAD" | python -m json.tool 2>/dev/null || cat
