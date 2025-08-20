## Checklist de teste do Webhook Hubla no Dokploy (produção)

Objetivo: quando o cliente finalizar a compra, o email já cadastrado deve conseguir fazer login no Leads Infinitos.

Importante sobre a versão atual do código:
- A criação automática de usuário acontece ao receber o evento v2 “Membro > Acesso concedido” (`customer.member_added`).
- A criação automática da licença continua acontecendo para eventos de compra que contenham `purchase` e `completed` ou `approved` (ex.: `purchase.completed`, `purchase.approved`).
- O evento de teste “Assinatura ativa (v2)” da Hubla é recebido e armazenado, mas não cria licença automaticamente.

Se você precisa validar a criação de licença nesta versão, use um evento de compra suportado ou faça um POST manual (exemplo abaixo) simulando `purchase.completed`.

Referências oficiais:
- Hubla – Webhooks: [help.hub.la/hc/pt-br/webhook-hubla](https://help.hub.la/hc/pt-br/webhook-hubla)
- Eventos v2 – Assinatura: [hubla.gitbook.io/docs/webhooks/eventos-v2/assinatura#assinatura-ativada](https://hubla.gitbook.io/docs/webhooks/eventos-v2/assinatura#assinatura-ativada)
- Eventos v2 – Membro (Acesso concedido): [hubla.gitbook.io/docs/webhooks/eventos-v2/membro#acesso-concedido](https://hubla.gitbook.io/docs/webhooks/eventos-v2/membro#acesso-concedido)

---

### Variáveis (ajuste antes de executar)

```bash
export APP_DOMAIN="https://leads-infinitos.wbtech.dev"
export WEBHOOK_URL="https://webhooks.wbtech.dev/webhook/hubla"
export EMAIL="testui@example.com"    # email que deverá conseguir logar após a compra
export PASS="Senha#123"              # senha para o login
export PRODUCT_ID="SEU_PRODUCT_ID"   # ID do produto na Hubla
```

---

### 1) Sanidade do serviço

```bash
curl -i "$APP_DOMAIN/healthz"
```
Esperado: HTTP/200 com corpo "ok".

---

### 2) Garantir que o banco está pronto (idempotente)

```bash
python3 -c "import init_db; init_db.init_db() or True"
```

---

### 3) Conferir/ajustar configuração da Hubla

Visualizar `hubla_config` atual:
```bash
python3 - <<'PY'
import os, sqlite3
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
cfg=conn.execute("SELECT * FROM hubla_config LIMIT 1").fetchone()
print(dict(cfg) if cfg else None)
PY
```

Se precisar definir/atualizar `webhook_token` e `product_id` (gera token forte automaticamente):
```bash
python3 - <<'PY'
import os, sqlite3, secrets, string
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
token=''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(48))
product_id=os.environ.get('PRODUCT_ID') or 'unset-product-id'
row=conn.execute("SELECT id FROM hubla_config LIMIT 1").fetchone()
if row:
    conn.execute("UPDATE hubla_config SET webhook_token=?, product_id=?, sandbox_mode=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (token, product_id, row['id']))
else:
    conn.execute("INSERT INTO hubla_config (webhook_token, product_id, sandbox_mode) VALUES (?, ?, 0)", (token, product_id))
conn.commit()
print("HUBLA_WEBHOOK_TOKEN:", token)
PY
```
Guarde o valor de `HUBLA_WEBHOOK_TOKEN`; ele será usado como `Authorization: Bearer <token>` na configuração da Hubla.

---

### 4) Usuário para login após a compra

Agora, ao disparar “Membro > Acesso concedido” (v2) na Hubla, o usuário é criado automaticamente se ainda não existir. Caso prefira, você ainda pode criar previamente com a senha desejada:

```bash
python3 - <<'PY'
import os, sqlite3
from werkzeug.security import generate_password_hash as H
email=os.environ.get('EMAIL'); pwd=os.environ.get('PASS')
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db)
conn.execute("INSERT OR IGNORE INTO users(email,password_hash) VALUES(?,?)", (email, H(pwd)))
conn.commit(); conn.close()
print('Usuario OK:', email)
PY
```

---

### 5) Configurar a regra de Webhook na Hubla

- URL do Webhook: `https://webhooks.wbtech.dev/webhook/hubla`
- Header Authorization: `Bearer <HUBLA_WEBHOOK_TOKEN>` (do passo 3)
- Produto: `PRODUCT_ID` configurado no passo 3
- Evento para teste na UI da Hubla:
  - “Membro > Acesso concedido (v2)” (`customer.member_added`): cria automaticamente o usuário (se não existir).
  - “Assinatura ativa (v2)”: será recebido e salvo, mas não cria licença.
  - Para criar a licença, prefira um evento de compra suportado (ex.: `purchase.completed`).

Veja as instruções oficiais: Hubla Webhooks ([help.hub.la](https://help.hub.la/hc/pt-br/webhook-hubla)).

---

### 6) Disparar o teste na Hubla

No modal “Testar configuração”, informe exatamente o `EMAIL` do passo 4 e clique em “Enviar eventos”.

---

### 7) Verificar recebimento do webhook (auditoria)

```bash
python3 - <<'PY'
import os, sqlite3
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
rows=conn.execute("SELECT id,event_type,hubla_purchase_id,created_at FROM hubla_webhooks ORDER BY id DESC LIMIT 10").fetchall()
print('Ultimos webhooks:')
for r in rows: print(dict(r))
PY
```

Para inspecionar o último payload salvo:
```bash
python3 - <<'PY'
import os, sqlite3
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
r=conn.execute("SELECT event_type,payload FROM hubla_webhooks ORDER BY id DESC LIMIT 1").fetchone()
print('event:', r['event_type'] if r else None)
print((r['payload'] or '')[:2000] if r else 'no payload')
PY
```

---

### 8) Verificar usuário e licença

```bash
python3 - <<'PY'
import os, sqlite3
email=os.environ.get('EMAIL')
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
u=conn.execute("SELECT id,email FROM users WHERE email=?", (email,)).fetchone()
print('user:', dict(u) if u else None)
if u:
    ls=conn.execute("SELECT id,license_type,status,purchase_date,expires_at,hotmart_purchase_id FROM licenses WHERE user_id=? ORDER BY id DESC LIMIT 5", (u['id'],)).fetchall()
    print('licenses:', [dict(x) for x in ls])
PY
```

Esperado:
- Com “Membro > Acesso concedido (v2)”: o usuário deve existir no banco.
- Com eventos de compra suportados: ao menos uma licença `active` criada.

Observação: usando “Assinatura ativa (v2)”, a licença não será criada pela versão atual.

---

### 9) (Opcional) Simular manualmente um evento suportado para criar licença

Se você precisa validar imediatamente a criação de licença, envie um webhook manual de `purchase.completed` com o mesmo `EMAIL` e o header Authorization correto.

```bash
TOKEN="$(python3 - <<'PY'
import os, sqlite3
db=os.path.join(os.getcwd(),'app.db')
conn=sqlite3.connect(db); conn.row_factory=sqlite3.Row
cfg=conn.execute("SELECT webhook_token FROM hubla_config LIMIT 1").fetchone()
print(cfg['webhook_token'] if cfg else '')
PY
)"

curl -i "$APP_DOMAIN/webhook/hubla" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d @- <<'JSON'
{
  "event": "purchase.completed",
  "data": {
    "purchase": {
      "id": "hubla-manual-12345",
      "created_at": "2025-01-01T10:00:00Z",
      "approved_at": "2025-01-01T10:00:00Z",
      "price": {"value": 297.00, "currency": "BRL"}
    },
    "buyer": {"email": "REPLACE_WITH_EMAIL"},
    "product": {"id": "REPLACE_WITH_PRODUCT_ID", "name": "Leads Infinitos"}
  }
}
JSON
```

Troque `REPLACE_WITH_EMAIL` pelo `EMAIL` do passo 4 e `REPLACE_WITH_PRODUCT_ID` pelo `PRODUCT_ID`.

Depois, repita o passo 8 para confirmar a licença.

---

### 10) Login final

Abra:
```
$APP_DOMAIN/login
```
Credenciais: `EMAIL` e `PASS` definidos nos passos iniciais.

---

### Nota sobre “Acesso concedido”

Agora suportamos o evento “Membro > Acesso concedido” (v2) para criação automática do usuário. Ainda não criamos licença a partir deste evento; a licença continua sendo criada a partir de eventos de compra suportados.


