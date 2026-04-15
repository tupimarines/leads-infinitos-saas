# Campanha avançada Uazapi - 4 números, intervalo mínimo
# Uso: .\scripts\curl_advanced_campaign.ps1

$TOKEN = "a7f9d434-c214-44b1-8c53-4c26ead90f97"
$URL = "https://neurix.uazapi.com/sender/advanced"

$payload = @{
  delayMin = 1
  delayMax = 2
  info = "teste-curl-4numeros"
  scheduled_for = 1
  messages = @(
    @{ number = "554137984981"; type = "text"; text = "Teste Maria" }
    @{ number = "554137984019"; type = "text"; text = "Teste Ana" }
    @{ number = "554137984966"; type = "text"; text = "Teste João" }
    @{ number = "554137984741"; type = "text"; text = "Teste Pedro" }
  )
} | ConvertTo-Json -Depth 5

Write-Host "=== Payload ===" -ForegroundColor Cyan
$payload
Write-Host ""
Write-Host "=== Response ===" -ForegroundColor Cyan
Invoke-RestMethod -Uri $URL -Method Post -Headers @{
  "Accept" = "application/json"
  "Content-Type" = "application/json"
  "token" = $TOKEN
} -Body $payload | ConvertTo-Json -Depth 5
