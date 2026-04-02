---
title: 'Seletor de Limite Diário de Mensagens na Criação de Campanha'
slug: 'seletor-limite-diario-campanha'
created: '2026-04-02'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Python/Flask', 'PostgreSQL/psycopg2', 'Jinja2', 'Tailwind CSS', 'Vanilla JS']
files_to_modify:
  - 'app.py'
  - 'templates/campaigns_new.html'
  - 'templates/admin/campaigns_new.html'
code_patterns: ['render_template com variáveis Jinja', 'payload JS → fetch POST JSON', 'clamp de inteiros Python']
test_patterns: ['pytest', 'tests/test_admin_campaign_crud.py como referência']
---

# Tech-Spec: Seletor de Limite Diário de Mensagens na Criação de Campanha

**Created:** 2026-04-02

## Overview

### Problem Statement

Hoje o `daily_limit` de uma campanha é setado automaticamente com base no plano do usuário (`get_user_daily_limit`), sem nenhum controle do usuário no momento da criação. Isso impede estratégias mais conservadoras (ex: enviar apenas 10 mensagens/dia para testar uma lista) ou ajuste manual abaixo do teto do plano.

### Solution

Adicionar um contador +/− (mín 5, máx = teto do plano, step 5) logo abaixo do bloco "🚀 Envio Inteligente" nos formulários de criação de campanha — tanto para o usuário comum quanto para o superadmin criando campanhas para outros usuários. O backend passa o `plan_daily_limit` via Jinja ao renderizar o template, e `_create_campaign_core` aceita o `daily_limit` do payload, validando que não ultrapassa o limite do plano.

### Scope

**In Scope:**
- Contador +/− em `templates/campaigns_new.html` (fluxo usuário)
- Contador +/− em `templates/admin/campaigns_new.html` (fluxo superadmin)
- Rota `new_campaign` passa `plan_daily_limit` ao template calculado via `get_user_daily_limit`
- Rota `admin_new_campaign` passa `plan_daily_limit=30` (teto geral) ao template
- `_create_campaign_core` aceita `daily_limit` do payload JSON e clamba contra o plano real do `user_id` alvo

**Out of Scope:**
- Alterar a lógica do worker (já respeita `campaign.daily_limit`)
- Plano infinite com seletor por instância (comportamento existente mantido; teto 30 para infinite nesta feature)
- Edição de campanha já existente

---

## Context for Development

### Codebase Patterns

- Flask monolito em `app.py`; placeholders PostgreSQL com `%s`; `RealDictCursor` em queries com dicionário
- Templates Jinja2 com tema escuro; Tailwind CSS via CDN; `glass-panel` como classe de container padrão
- Limites centralizados em `utils/limits.py` → `PLAN_POLICY` e `get_user_daily_limit(user_id)`
- `_create_campaign_core(user_id, data, admin_id=None)` é o helper compartilhado: chamado por `/api/campaigns` (usuário) e `/api/admin/campaigns` (superadmin)
- Campos do payload extraídos via `data.get(...)` no início de `_create_campaign_core`
- UI de contador +/− já existe no app (vide print do usuário com "Negócios: − 0 +") — reutilizar mesmo estilo visual

### Files to Reference

| File | Linha | Propósito |
| ---- | ----- | --------- |
| `utils/limits.py` | 11-52 | `PLAN_POLICY` e `get_user_daily_limit` — limites por plano |
| `templates/campaigns_new.html` | 330-341 | Bloco "🚀 Envio Inteligente" — inserir contador logo após linha 341 |
| `templates/campaigns_new.html` | 1140-1152 | Objeto `data` no submit JS — adicionar campo `daily_limit` |
| `templates/admin/campaigns_new.html` | 183-190 | Bloco "[2] Seleção de Instância(s)" — inserir contador após linha 190 |
| `templates/admin/campaigns_new.html` | 778-791 | Objeto `data` no submit JS — adicionar campo `daily_limit` |
| `app.py` | 4236-4248 | Rota `new_campaign` — calcular e passar `plan_daily_limit` ao template |
| `app.py` | 3350-3354 | Rota `admin_new_campaign` — passar `plan_daily_limit=30` ao template |
| `app.py` | 4736 | Assinatura de `_create_campaign_core` |
| `app.py` | 4937 | `daily_limit = get_user_daily_limit(user_id)` — linha a substituir pelo clamp |

**Fatos confirmados pela investigação:**
- `get_user_daily_limit` já está importado em `app.py` linha 41 — sem novo import
- `admin_create_campaign` (linha 3180) chama `_create_campaign_core(target_user_id, data, admin_id=current_user.id)` — core único para ambos os fluxos
- `new_campaign` já passa `instances` e `is_super_admin` ao template; `plan_daily_limit` ainda não é passado
- `admin_new_campaign` é GET puro sem variáveis ao template; `plan_daily_limit` ainda não é passado
- Campo `daily_limit` já existe na tabela `campaigns` com `DEFAULT 0` — **sem migração de schema necessária**
- Payload atual do usuário (linha 1140-1152): não inclui `daily_limit`
- Payload atual do admin (linha 778-791): não inclui `daily_limit`

### Technical Decisions

**ADR-1: Posicionamento do contador**
- Template usuário: inserir bloco imediatamente após o fechamento do div "🚀 Envio Inteligente" (após linha 341) e antes do bloco "🔄 Cadência de Follow-up"
- Template admin: inserir bloco após o div "[2] Seleção de Instância(s)" (após linha 190) e antes do div "[3] Seleção de Job OU Upload CSV"

**ADR-2: Validação — clamp silencioso no backend**
- Frontend controla visualmente (botões desabilitados nos limites); nunca submete valor fora do range
- Backend aplica `max(5, min(int(submitted), plan_limit))` como segurança; sem retorno de erro 400

**ADR-3: `plan_daily_limit` no template admin**
- Template admin usa `30` (teto geral) visualmente — sem endpoint JS extra para buscar plano do usuário selecionado
- Backend clamba pelo plano real do `user_id` alvo na submissão

**ADR-4: Valor padrão e step**
- Valor padrão = `plan_daily_limit` (teto do plano do usuário)
- Step: 5 (opções: 5, 10, 15, 20, 25, 30 — clamped ao `plan_daily_limit`)
- Input hidden `id="daily_limit_value"` carrega o valor corrente; botões JS atualizam esse valor

**ADR-5: Plano infinite**
- Teto visual do contador = 30 para infinite (default do plano); ajuste por instância continua na tela de Configurações (fora do escopo)

**ADR-6: Segurança do `user_id`**
- `_create_campaign_core` usa o `user_id` recebido como parâmetro para checar plano — nunca `current_user.id` diretamente

---

## Implementation Plan

### Tasks

- [x] **T1 — `app.py`: Rota `new_campaign` passa `plan_daily_limit` ao template**
  - File: `app.py`, linhas 4236–4248
  - Action: Após `conn.close()` na linha 4244, adicionar `plan_daily_limit = get_user_daily_limit(current_user.id)`. Adicionar `plan_daily_limit=plan_daily_limit` no `render_template` na linha 4246.
  - Notes: `get_user_daily_limit` já importado via `from utils.limits import ... get_user_daily_limit` (linha 41). Não requer novo import.

- [x] **T2 — `app.py`: Rota `admin_new_campaign` passa `plan_daily_limit=30` ao template**
  - File: `app.py`, linhas 3350–3354
  - Action: Alterar `return render_template('admin/campaigns_new.html')` para `return render_template('admin/campaigns_new.html', plan_daily_limit=30)`.
  - Notes: Valor 30 é o teto geral de qualquer plano não-infinite. O backend valida pelo plano real do user-alvo na submissão (T7).

- [x] **T3 — `templates/campaigns_new.html`: Inserir bloco HTML do contador após "🚀 Envio Inteligente"**
  - File: `templates/campaigns_new.html`, após linha 341 (fechamento do div do bloco "🚀 Envio Inteligente")
  - Action: Inserir o seguinte bloco HTML entre o fim do bloco "🚀 Envio Inteligente" e o início do bloco "🔄 Cadência de Follow-up":

```html
<!-- ============================================ -->
<!-- Limite diário de mensagens -->
<!-- ============================================ -->
<div class="glass-panel p-5 rounded-xl"
    style="background: rgba(79, 124, 255, 0.04); border: 1px solid rgba(79, 124, 255, 0.15);">
    <label class="block text-sm font-semibold text-gray-300 mb-1">
        📨 Mensagens por dia
    </label>
    <p class="text-xs text-gray-500 mb-3">Quantas mensagens serão enviadas por dia nesta campanha. Máximo do seu plano: <strong class="text-blue-400">{{ plan_daily_limit }}</strong>.</p>
    <div class="flex items-center gap-4" id="dailyLimitCounter" data-min="5" data-max="{{ plan_daily_limit }}" data-step="5">
        <button type="button" id="dailyLimitDec"
            class="w-9 h-9 rounded-lg border border-blue-500/40 bg-blue-500/10 text-blue-400 font-bold text-lg flex items-center justify-center hover:bg-blue-500/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            onclick="adjustDailyLimit(-1)">−</button>
        <span id="dailyLimitDisplay" class="text-2xl font-bold text-white w-10 text-center">{{ plan_daily_limit }}</span>
        <button type="button" id="dailyLimitInc"
            class="w-9 h-9 rounded-lg border border-blue-500/40 bg-blue-500/10 text-blue-400 font-bold text-lg flex items-center justify-center hover:bg-blue-500/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            onclick="adjustDailyLimit(1)">+</button>
        <span class="text-xs text-gray-500">msgs/dia</span>
    </div>
    <input type="hidden" id="daily_limit_value" value="{{ plan_daily_limit }}">
</div>
```

  - Notes: `data-min`, `data-max`, `data-step` são lidos pelo JS (T3-JS abaixo). O `id="daily_limit_value"` é lido no submit (T4).

- [x] **T3-JS — `templates/campaigns_new.html`: Adicionar função JS `adjustDailyLimit`**
  - File: `templates/campaigns_new.html`, na seção `<script>` existente (após as funções de cadência ou antes do handler de submit)
  - Action: Inserir a função JS:

```javascript
function adjustDailyLimit(direction) {
    const counter = document.getElementById('dailyLimitCounter');
    const display = document.getElementById('dailyLimitDisplay');
    const hidden = document.getElementById('daily_limit_value');
    const min = parseInt(counter.dataset.min, 10);
    const max = parseInt(counter.dataset.max, 10);
    const step = parseInt(counter.dataset.step, 10);
    let current = parseInt(hidden.value, 10);
    current = Math.min(max, Math.max(min, current + direction * step));
    hidden.value = current;
    display.textContent = current;
    document.getElementById('dailyLimitDec').disabled = current <= min;
    document.getElementById('dailyLimitInc').disabled = current >= max;
}

// Inicializar estado dos botões ao carregar
document.addEventListener('DOMContentLoaded', () => {
    const counter = document.getElementById('dailyLimitCounter');
    if (counter) {
        const min = parseInt(counter.dataset.min, 10);
        const max = parseInt(counter.dataset.max, 10);
        const current = parseInt(document.getElementById('daily_limit_value').value, 10);
        document.getElementById('dailyLimitDec').disabled = current <= min;
        document.getElementById('dailyLimitInc').disabled = current >= max;
    }
});
```

  - Notes: Se já existe um `DOMContentLoaded` listener, adicionar o bloco de inicialização dentro do existente em vez de criar um novo.

- [x] **T4 — `templates/campaigns_new.html`: Adicionar `daily_limit` no payload do submit**
  - File: `templates/campaigns_new.html`, no objeto `data` do handler de submit (linha ~1140–1152)
  - Action: Adicionar a linha abaixo no objeto `data`, após `send_sunday`:

```javascript
daily_limit: parseInt(document.getElementById('daily_limit_value').value, 10),
```

  - Notes: O `fetch` já envia para `/api/campaigns` com `Content-Type: application/json`.

- [x] **T5 — `templates/admin/campaigns_new.html`: Inserir bloco HTML do contador**
  - File: `templates/admin/campaigns_new.html`, após linha 190 (fechamento do div "[2] Seleção de Instância(s)") e antes do comentário "[3] Seleção de Job OU Upload CSV"
  - Action: Inserir o mesmo bloco HTML do T3, adaptado para admin (sem texto "seu plano", pois o plano é do user-alvo):

```html
<!-- ============================================ -->
<!-- Limite diário de mensagens -->
<!-- ============================================ -->
<div class="glass-panel p-5 rounded-xl"
    style="background: rgba(79, 124, 255, 0.04); border: 1px solid rgba(79, 124, 255, 0.15);">
    <label class="block text-sm font-semibold text-gray-300 mb-1">
        📨 Mensagens por dia
    </label>
    <p class="text-xs text-gray-500 mb-3">Quantas mensagens serão enviadas por dia. Limite máximo: <strong class="text-blue-400">{{ plan_daily_limit }}</strong> (validado pelo plano do usuário).</p>
    <div class="flex items-center gap-4" id="dailyLimitCounter" data-min="5" data-max="{{ plan_daily_limit }}" data-step="5">
        <button type="button" id="dailyLimitDec"
            class="w-9 h-9 rounded-lg border border-blue-500/40 bg-blue-500/10 text-blue-400 font-bold text-lg flex items-center justify-center hover:bg-blue-500/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            onclick="adjustDailyLimit(-1)">−</button>
        <span id="dailyLimitDisplay" class="text-2xl font-bold text-white w-10 text-center">{{ plan_daily_limit }}</span>
        <button type="button" id="dailyLimitInc"
            class="w-9 h-9 rounded-lg border border-blue-500/40 bg-blue-500/10 text-blue-400 font-bold text-lg flex items-center justify-center hover:bg-blue-500/20 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            onclick="adjustDailyLimit(1)">+</button>
        <span class="text-xs text-gray-500">msgs/dia</span>
    </div>
    <input type="hidden" id="daily_limit_value" value="{{ plan_daily_limit }}">
</div>
```

- [x] **T5-JS — `templates/admin/campaigns_new.html`: Adicionar função JS `adjustDailyLimit`**
  - File: `templates/admin/campaigns_new.html`, na seção `<script>` (antes do handler de submit, após as funções `toggleCadence`/`toggleStep`)
  - Action: Inserir a mesma função `adjustDailyLimit` e inicialização do T3-JS.

- [x] **T6 — `templates/admin/campaigns_new.html`: Adicionar `daily_limit` no payload do submit**
  - File: `templates/admin/campaigns_new.html`, no objeto `data` do handler de submit (linha ~778–791)
  - Action: Adicionar após `send_sunday`:

```javascript
daily_limit: parseInt(document.getElementById('daily_limit_value').value, 10),
```

- [x] **T7 — `app.py`: `_create_campaign_core` — aceitar e clampar `daily_limit` do payload**
  - File: `app.py`, linha 4937
  - Action: Substituir a linha:
    ```python
    daily_limit = get_user_daily_limit(user_id)
    ```
    por:
    ```python
    plan_limit = get_user_daily_limit(user_id)
    _submitted = data.get('daily_limit')
    daily_limit = max(5, min(int(_submitted), plan_limit)) if _submitted is not None else plan_limit
    ```
  - Notes: `get_user_daily_limit` já importado. O `int()` pode lançar `ValueError` se o valor for inválido — mas o frontend sempre envia inteiro válido; o clamp já cobre bypassers. Se quiser ser defensivo, envolver em `try/except` e usar `plan_limit` como fallback.

---

### Acceptance Criteria

- [x] **AC1** — Dado que o usuário acessa `/campaigns/new`, quando a página carrega, então deve haver um bloco "📨 Mensagens por dia" com botões `−` e `+` e valor inicial igual ao `plan_daily_limit` do plano, posicionado abaixo do bloco "🚀 Envio Inteligente".

- [x] **AC2** — Dado que o usuário clica em `−` estando no valor mínimo (5), quando o click ocorre, então o botão `−` deve estar desabilitado e o valor permanece em 5. Idem para `+` no valor máximo (`plan_daily_limit`).

- [x] **AC3** — Dado que o usuário tem plano `starter_trial`, quando a página `/campaigns/new` carrega, então o valor máximo do contador é 15 e o botão `+` fica desabilitado ao atingir 15.

- [x] **AC4** — Dado que o usuário seleciona 10 mensagens/dia e submete o formulário, quando o request chega em `POST /api/campaigns`, então o body JSON contém `"daily_limit": 10`.

- [x] **AC5** — Dado que `_create_campaign_core` recebe `daily_limit: 30` para um `user_id` com plano `starter_trial` (max 15), quando a campanha é criada, então `campaigns.daily_limit = 15` no banco (clamp silencioso).

- [x] **AC6** — Dado que `_create_campaign_core` recebe payload sem o campo `daily_limit`, quando a campanha é criada, então `campaigns.daily_limit = plan_limit` do usuário (comportamento legado preservado).

- [x] **AC7** — Dado que `_create_campaign_core` recebe `daily_limit: 0` ou `daily_limit: 2` (abaixo do mínimo), quando a campanha é criada, então `campaigns.daily_limit >= 5` (clamp para mínimo).

- [x] **AC8** — Dado que o superadmin acessa `/admin/campaigns/new`, quando a página carrega, então deve haver o mesmo bloco contador com `max=30`, e o valor é incluído no payload enviado a `POST /api/admin/campaigns`.

---

## Additional Context

### Dependencies

- Nenhuma dependência externa nova
- `get_user_daily_limit` já importado em `app.py` linha 41 via `from utils.limits import ..., get_user_daily_limit, ...`

### Testing Strategy

**Manual:**
1. Logar como usuário `starter_trial` → acessar `/campaigns/new` → verificar contador inicia em 15, botão `+` desabilitado
2. Logar como usuário `pro`/`starter`/`scale` → acessar `/campaigns/new` → verificar contador inicia em 30
3. Clicar `−` até 5 → verificar botão `−` desabilitado; clicar `+` → verifica step de 5
4. Criar campanha com 10 msgs/dia → verificar no banco: `SELECT daily_limit FROM campaigns ORDER BY id DESC LIMIT 1`
5. Superadmin acessa `/admin/campaigns/new` → verificar contador presente com max 30

**Backend (via curl ou script):**
```bash
# AC5: clamp para starter_trial
curl -X POST /api/campaigns -H "Content-Type: application/json" \
  -d '{"daily_limit": 30, "name": "test", ...}' --cookie session=...
# Verificar no banco que daily_limit = 15

# AC6: sem daily_limit no payload → usa plan_limit
curl -X POST /api/campaigns -H "Content-Type: application/json" \
  -d '{"name": "test", ...}' --cookie session=...
# Verificar no banco que daily_limit = plan_limit do usuário
```

### Notes

- Campo `daily_limit` já existe na tabela `campaigns` com `DEFAULT 0` — **sem migração de schema necessária**
- Worker `worker_sender.py` já lê `campaign.daily_limit` para controlar envios — **sem alteração no worker**
- `PLAN_POLICY` em `utils/limits.py`: `starter_trial=15`, `starter=30`, `pro=30`, `scale=30`, `infinite=30` (default; infinite pode ter custom por instância via `INFINITE_DAILY_SEND_OPTIONS`, mas isso está fora do escopo desta feature)
- O bloco do contador usa `onclick="adjustDailyLimit(±1)"` inline para simplicidade; se o projeto migrar para módulos JS, extrair para evento addEventListener
- **Risco:** Se `int(_submitted)` falhar (valor não numérico enviado por bypass), a campanha usa `plan_limit` como fallback — considerar envolver em `try/except ValueError` para robustez adicional
