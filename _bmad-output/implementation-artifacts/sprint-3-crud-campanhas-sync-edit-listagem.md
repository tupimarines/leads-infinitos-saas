# Sprint 3: Sync + Edição + Atualização da Listagem

**Spec principal:** `_bmad-output/implementation-artifacts/tech-spec-crud-campanhas-superadmin.md`
**Sprint:** 3 de 4
**Pré-requisito:** Sprint 2 completo (helper core + endpoint criação + CSV validate)
**Escopo:** Tasks 8-12 (sync contadores + edição admin + leads + atualização campaigns.html)
**Risco:** Médio
**Estimativa:** Médio — ~300-400 linhas de código

---

## Contexto Mínimo

Este sprint implementa o sync de contadores da Uazapi (para que os cards mostrem números reais), a edição de campanhas pelo admin, a listagem de leads, e atualiza o template da listagem admin com botões e AJAX.

### Arquivos para LER antes de implementar

| Arquivo | Linhas | Motivo |
|---------|--------|--------|
| `utils/sync_uazapi.py` L1-50 + L390-420 + L580-700 | Lógica de sync — `_sync_folder_via_listfolders`, `sync_campaign_leads_from_uazapi` |
| `app.py` L2900-2961 | Rota `admin_campaigns()` — listagem atual |
| `app.py` L6608-6644 | Rota `update_campaign()` — edição do usuário (referência) |
| `app.py` L4337-4382 | Padrão de criação de steps (`ON CONFLICT DO UPDATE`) |
| `app.py` L6502-6556 | Rota `get_campaign_leads()` — padrão de listagem de leads |
| `templates/admin/campaigns.html` | INTEIRO — template a modificar |
| `services/uazapi.py` L352-414 | `list_folders()` |

### ADR-5: Campos editáveis por status (regra de negócio para edição)

| Status | Campos editáveis | Campos bloqueados |
|--------|-------------------|-------------------|
| `pending` | Todos | Nenhum |
| `paused` | Nome, horários, delay, toggles fim de semana | Mensagens, instâncias, leads |
| `running` | Nome apenas | Tudo (exigir pausar primeiro) |
| `completed` | Nenhum (read-only) | Todos |

---

## Tasks

### Task 8: Endpoint `GET /api/admin/campaigns/sync`

- **File:** `app.py`
- **Action:** Nova rota `@admin_required`.
- **Lógica:**
  1. Buscar campanhas `running` com Uazapi:
     ```sql
     SELECT DISTINCT c.id
     FROM campaigns c
     WHERE c.status = 'running' AND c.use_uazapi_sender = TRUE
     ```
  2. Para cada campanha, buscar sends ativos:
     ```sql
     SELECT css.id, css.uazapi_folder_id, css.last_sync_at, css.instance_id,
            i.apikey
     FROM campaign_stage_sends css
     JOIN instances i ON i.id = css.instance_id
     WHERE css.campaign_id = %s
       AND css.status IN ('running', 'partial', 'scheduled', 'queued')
       AND css.uazapi_folder_id IS NOT NULL
     ```
  3. Skip se `last_sync_at` < 5 minutos atrás.
  4. Chamar `list_folders(token)` e encontrar o folder pelo `uazapi_folder_id`.
  5. Extrair `log_sucess` (ou `log_success`) e `log_failed` do folder.
  6. Atualizar `campaign_stage_sends`:
     ```sql
     UPDATE campaign_stage_sends
     SET success_count = %s, failed_count = %s, last_sync_at = NOW()
     WHERE id = %s
     ```
  7. Recontar leads por campanha:
     ```sql
     SELECT campaign_id,
            COUNT(*) as total_leads,
            COUNT(*) FILTER (WHERE status = 'sent') as sent_count,
            COUNT(*) FILTER (WHERE status = 'pending') as pending_count
     FROM campaign_leads
     WHERE campaign_id = ANY(%s)
     GROUP BY campaign_id
     ```
  8. Usar `try/except` por campanha — não travar se uma instância falhar.
- **Response:** `{campaigns: [{id, sent_count, pending_count, total_leads, last_sync}]}`
- **Performance:** Agrupar por instância (mesmo token) para reduzir chamadas API. Uma chamada `list_folders(token)` retorna TODAS as pastas da instância.

### Task 9: (Noop) A rota `admin_campaigns()` não precisa de alteração SQL

A query existente em L2900-2961 já retorna `sent_count` e `pending_count` de `campaign_leads`. O AJAX da Task 12 atualizará os valores com dados do sync.

### Task 10: Endpoint `POST /api/admin/campaigns/<id>/update`

- **File:** `app.py`
- **Action:** Nova rota `@admin_required`:
  ```python
  @app.route('/api/admin/campaigns/<int:campaign_id>/update', methods=['POST'])
  @login_required
  @admin_required
  def admin_update_campaign(campaign_id):
  ```
- **Lógica:**
  1. Carregar campanha: `SELECT * FROM campaigns WHERE id = %s` (sem filtro user_id).
  2. Se não existe: 404.
  3. Aplicar regras ADR-5 com base no `status`:
     - `running`: só permitir `name`. Se tentar editar outro campo → 400 com mensagem "Pause a campanha para editar [campo]".
     - `completed`: 400 "Campanha concluída não pode ser editada".
     - `paused`: permitir `name`, `send_hour_start`, `send_hour_end`, `send_saturday`, `send_sunday`, `delay_min_minutes`, `delay_max_minutes`.
     - `pending`: permitir todos os campos.
  4. Atualizar campos permitidos:
     ```sql
     UPDATE campaigns SET name = %s, send_hour_start = %s, ... WHERE id = %s
     ```
  5. Se `steps` presente no payload e status permite (pending/paused com follow-ups editáveis):
     - UPSERT em `campaign_steps` (padrão L4370-4382):
       ```sql
       INSERT INTO campaign_steps (campaign_id, step_number, step_label, message_template, delay_days)
       VALUES (%s, %s, %s, %s, %s)
       ON CONFLICT (campaign_id, step_number) DO UPDATE SET
           step_label = EXCLUDED.step_label,
           message_template = EXCLUDED.message_template,
           delay_days = EXCLUDED.delay_days
       ```
  6. Se `enable_cadence` mudou: atualizar `campaigns.enable_cadence` e `cadence_config`.
- **Response:** `{success: true}` ou `{error: "..."}`, status 400/404.

### Task 11: Endpoint `GET /api/admin/campaigns/<id>/leads`

- **File:** `app.py`
- **Action:** Nova rota `@admin_required`. Copiar padrão de `get_campaign_leads()` (L6502-6556) com estas diferenças:
  - Sem filtro `Campaign.get_by_id(campaign_id, current_user.id)` — admin acessa qualquer campanha.
  - Verificar apenas que campanha existe: `SELECT id FROM campaigns WHERE id = %s`.
  - Mesma paginação, mesmos filtros (name, phone, status).
- **Response:** `{leads: [...], total, page, pages}`

### Task 12: Atualizar `templates/admin/campaigns.html`

- **File:** `templates/admin/campaigns.html`
- **Action — 3 alterações:**

  **12a. Botão "Nova Campanha" no header:**
  - Após o botão "← Voltar" (L13), adicionar:
    ```html
    <a href="{{ url_for('admin_new_campaign') }}"
       class="btn bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg font-medium transition-colors">
        + Nova Campanha
    </a>
    ```

  **12b. Layout de 3 botões nos cards:**
  - Substituir o div de actions (L104-113) por layout com 3 botões compactos:
    ```html
    <div class="grid grid-cols-3 gap-1">
        <button type="button" onclick="event.stopPropagation(); openCampaignDetail({{ campaign.id }})"
            class="px-2 py-1.5 rounded text-xs font-medium bg-blue-600/20 text-blue-300 border border-blue-500/30 hover:bg-blue-600/40 transition-colors truncate">
            📋 Detalhes
        </button>
        <a href="/admin/campaigns/{{ campaign.id }}/edit" onclick="event.stopPropagation()"
            class="px-2 py-1.5 rounded text-xs font-medium bg-amber-600/20 text-amber-300 border border-amber-500/30 hover:bg-amber-600/40 transition-colors text-center truncate">
            ✏️ Editar
        </a>
        <button type="button" onclick="event.stopPropagation(); deleteCampaign({{ campaign.id }}, {{ campaign.name|tojson }})"
            class="px-2 py-1.5 rounded text-xs font-medium bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/40 transition-colors truncate">
            🗑️ Excluir
        </button>
    </div>
    ```

  **12c. Script AJAX de sync:**
  - Adicionar ao `<script>` existente (após a função `deleteCampaign`):
    ```javascript
    // Sync contadores via Uazapi (assíncrono)
    (async function syncCampaignCounts() {
        try {
            const res = await fetch('/api/admin/campaigns/sync');
            if (!res.ok) return;
            const data = await res.json();
            for (const c of (data.campaigns || [])) {
                // Atualizar card pelo data-campaign-id
                const card = document.querySelector(`[data-campaign-id="${c.id}"]`);
                if (!card) continue;
                const sentEl = card.querySelector('.sent-count');
                const pendingEl = card.querySelector('.pending-count');
                if (sentEl) sentEl.textContent = c.sent_count;
                if (pendingEl) pendingEl.textContent = c.pending_count;
            }
        } catch (e) {
            console.warn('Sync falhou:', e);
        }
    })();
    ```
  - Adicionar `data-campaign-id="{{ campaign.id }}"` ao div do card.
  - Adicionar classes `sent-count` e `pending-count` aos respectivos elementos de contagem nos cards.

---

## Acceptance Criteria deste Sprint

- [x] `GET /api/admin/campaigns/sync` retorna contadores atualizados das campanhas `running`
- [x] Sync pula campanhas sincadas há < 5 min (cache TTL)
- [x] Sync não trava se uma instância falhar (try/except por campanha)
- [x] `POST /api/admin/campaigns/{id}/update` respeita regras ADR-5 por status
- [x] Editar campanha `running` retorna erro 400 para campos bloqueados
- [x] Editar campanha `pending` permite todos os campos
- [x] Follow-ups (campaign_steps) são atualizados via UPSERT
- [x] `GET /api/admin/campaigns/{id}/leads` retorna leads paginados sem filtro user_id
- [x] Template `campaigns.html` exibe 3 botões (Detalhes, Editar, Excluir) sem quebra de layout
- [x] Botão "Nova Campanha" aparece no header
- [x] AJAX sync atualiza contadores nos cards ao carregar página

## Review Notes
- Adversarial review completed
- Findings: 5 total, 2 fixed (ADR-01 instance_ids removido, ADR-02 scheduled_start valida), 3 skipped (design intencional/pré-existente)
- Resolution approach: auto-fix

## Verificação Pós-Sprint

1. Abrir `/admin/campaigns` → botão "Nova Campanha" visível no header
2. Cards mostram 3 botões alinhados
3. Contadores atualizam após ~2-5s (AJAX sync)
4. Clicar "Editar" em campanha `pending` → redireciona (vai dar 404 até Sprint 4, ok)
5. Testar endpoint de edição via curl com diferentes status
