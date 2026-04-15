# Sprint 4: Frontend — Templates de Criação e Edição + Rotas Flask + Testes

**Spec principal:** `_bmad-output/implementation-artifacts/tech-spec-crud-campanhas-superadmin.md`
**Sprint:** 4 de 4
**Pré-requisito:** Sprint 3 completo (sync + edição + listagem atualizada)
**Escopo:** Tasks 13-16 (2 templates novos + rotas Flask + testes)
**Risco:** Médio (templates grandes, mas sem impacto em dados)
**Estimativa:** Grande — ~800-1200 linhas (templates são verbosos)

---

## Contexto Mínimo

Este sprint cria as páginas de criação e edição de campanhas pelo admin, conectando com todos os endpoints dos sprints anteriores. Também adiciona rotas Flask para servir essas páginas e um teste de integração.

### Arquivos para LER antes de implementar

| Arquivo | Motivo |
|---------|--------|
| `templates/campaigns_new.html` | INTEIRO — template de referência para UI (copiar estrutura visual) |
| `templates/campaigns_edit.html` | Referência para edição |
| `templates/admin/campaigns.html` | Template atualizado no Sprint 3 (verificar botões) |
| `templates/base.html` | Base template ({% extends %}, {% block %}) |
| `app.py` L3562-3574 | Rota `new_campaign()` — padrão de rota Flask para forms |
| `app.py` L6492-6499 | Rota `edit_campaign()` — padrão de rota Flask para edição |

### Endpoints disponíveis (criados nos Sprints 1-3)

| Endpoint | Sprint | Uso no Frontend |
|----------|--------|-----------------|
| `GET /api/admin/users/list` | 1 | Dropdown de usuários |
| `GET /api/admin/users/<id>/instances` | 1 | Dropdown de instâncias (cascata) |
| `GET /api/admin/users/<id>/scraping-jobs` | 1 | Dropdown de jobs (cascata) |
| `POST /api/admin/campaigns` | 2 | Submit do form de criação |
| `POST /api/admin/campaigns/validate-csv` | 2 | Upload CSV + validação |
| `POST /api/admin/campaigns/<id>/update` | 3 | Submit do form de edição |
| `GET /api/admin/campaigns/<id>/leads` | 3 | Tabela de leads na edição |

---

## Tasks

### Task 13: Criar `templates/admin/campaigns_new.html`

- **File:** `templates/admin/campaigns_new.html` (NOVO)
- **Base:** Copiar estrutura de `campaigns_new.html` (glass-panel, neon-input, cadence-step, tema escuro). **NÃO copiar** funcionalidades de IA (botão "Criar com IA", campo "business_context").
- **Estrutura do formulário:**

  ```
  ┌─ Header: "Nova Campanha (Admin)" + Botão "← Voltar" ─┐
  │                                                        │
  │  [1] Select Usuário ────────────── (AJAX cascata)      │
  │  [2] Select Instância(s) ───────── (carrega ao mudar)  │
  │  [3] Select Job OU Upload CSV ──── (carrega ao mudar)  │
  │      └─ [Popup] "Validar números no WhatsApp?"         │
  │                                                        │
  │  [4] Nome da Campanha ──────────── (text input)        │
  │  [5] Mensagens (variações) ─────── (textareas + add)   │
  │  [6] Horários: start/end + toggles sáb/dom             │
  │  [7] Delay min/max (opcional) ──── (placeholders)      │
  │  [8] Agendar início (opcional) ─── (datetime-local)    │
  │  [9] Cadência toggle + Follow-ups accordion            │
  │                                                        │
  │  [Resumo confirmação] ──────────── (antes do submit)   │
  │  [Botão CRIAR CAMPANHA]                                │
  └────────────────────────────────────────────────────────┘
  ```

- **JavaScript necessário:**

  **Cascata de dropdowns:**
  ```javascript
  document.getElementById('user_id').addEventListener('change', async function() {
      const userId = this.value;
      // Limpar dependentes
      document.getElementById('instance_ids').innerHTML = '<option>Carregando...</option>';
      document.getElementById('job_id').innerHTML = '<option>Carregando...</option>';
      
      // Carregar instâncias
      const instRes = await fetch(`/api/admin/users/${userId}/instances`);
      const instances = await instRes.json();
      // Preencher select de instâncias...
      
      // Carregar jobs
      const jobsRes = await fetch(`/api/admin/users/${userId}/scraping-jobs`);
      const jobs = await jobsRes.json();
      // Preencher select de jobs...
  });
  ```

  **Upload CSV com popup de validação:**
  ```javascript
  csvInput.addEventListener('change', async function() {
      const file = this.files[0];
      if (!file) return;
      
      const doValidate = confirm('Gostaria de validar se todos os números da lista são WhatsApp válido?\n\nIsso pode levar alguns minutos.');
      
      const formData = new FormData();
      formData.append('file', file);
      formData.append('user_id', document.getElementById('user_id').value);
      formData.append('validate_whatsapp', doValidate ? 'true' : 'false');
      
      // Mostrar progress
      progressMsg.textContent = doValidate 
          ? 'Validando números no WhatsApp... Isso pode levar alguns minutos.'
          : 'Processando lista...';
      progressMsg.classList.remove('hidden');
      
      const res = await fetch('/api/admin/campaigns/validate-csv', {method: 'POST', body: formData});
      const data = await res.json();
      
      if (data.error) { alert(data.error); return; }
      
      progressMsg.textContent = `✅ ${data.valid} números válidos (${data.invalid} removidos). Job ID: ${data.job_id}`;
      // Setar job_id no select/hidden input
      document.getElementById('job_id').value = data.job_id;
  });
  ```

  **Confirmação antes do submit:**
  ```javascript
  form.addEventListener('submit', async function(e) {
      e.preventDefault();
      const userName = userSelect.options[userSelect.selectedIndex].text;
      const name = document.getElementById('name').value;
      if (!confirm(`Criar campanha "${name}" para ${userName}?`)) return;
      
      // Coletar dados e POST /api/admin/campaigns
      // ...
  });
  ```

  **Cadência/Follow-ups:** Reusar a mesma lógica de accordion de `campaigns_new.html` (classes `cadence-step`, `cadence-step-header`, `cadence-step-body`). Cada step: textarea de mensagem, input delay_days, input label.

### Task 14: Criar `templates/admin/campaigns_edit.html`

- **File:** `templates/admin/campaigns_edit.html` (NOVO)
- **Base:** Similar a `campaigns_new.html` admin, mas pré-preenchido.
- **Diferenças da criação:**

  1. **Header:** "Editar Campanha: {nome}" com badge de status (Ativa/Pausada/etc).
  2. **Campos pré-preenchidos** com dados passados pela rota Flask (`campaign`, `steps`, `instances`).
  3. **Campos desabilitados** conforme ADR-5:
     ```javascript
     const status = '{{ campaign.status }}';
     if (status === 'running') {
         // Desabilitar tudo exceto nome
         document.querySelectorAll('.editable-field:not(#name)').forEach(el => {
             el.disabled = true;
             el.classList.add('opacity-50');
         });
         document.getElementById('status-warning').textContent = '⚠️ Pause a campanha para editar campos.';
     } else if (status === 'completed') {
         document.querySelectorAll('.editable-field').forEach(el => {
             el.disabled = true;
         });
         document.getElementById('status-warning').textContent = '✅ Campanha concluída (somente leitura).';
     }
     ```
  4. **Seção de Leads** (abaixo do formulário):
     ```html
     <div class="glass-panel p-6 rounded-2xl mt-8">
         <h2 class="text-xl font-bold text-white mb-4">Leads da Campanha</h2>
         <div class="flex gap-2 mb-4">
             <select id="leadStatusFilter" class="neon-input px-3 py-2 rounded-lg text-sm">
                 <option value="">Todos</option>
                 <option value="sent">Enviados</option>
                 <option value="pending">Pendentes</option>
                 <option value="failed">Falhos</option>
             </select>
         </div>
         <div id="leadsTable">Carregando...</div>
         <div id="leadsPagination" class="flex gap-2 mt-4"></div>
     </div>
     ```
     JavaScript: `fetch(`/api/admin/campaigns/${campaignId}/leads?page=${page}&status=${filter}`)` e renderizar tabela.
  5. **Submit:** `POST /api/admin/campaigns/{id}/update`.

### Task 15: Rotas Flask para servir as páginas

- **File:** `app.py`
- **Action — 2 rotas:**

  **Rota de criação:**
  ```python
  @app.route('/admin/campaigns/new')
  @login_required
  @admin_required
  def admin_new_campaign():
      return render_template('admin/campaigns_new.html')
  ```

  **Rota de edição:**
  ```python
  @app.route('/admin/campaigns/<int:campaign_id>/edit')
  @login_required
  @admin_required
  def admin_edit_campaign(campaign_id):
      conn = get_db_connection()
      with conn.cursor(cursor_factory=RealDictCursor) as cur:
          cur.execute("""
              SELECT c.*, u.email as user_email
              FROM campaigns c
              JOIN users u ON u.id = c.user_id
              WHERE c.id = %s
          """, (campaign_id,))
          campaign = cur.fetchone()
          if not campaign:
              flash("Campanha não encontrada.", "error")
              return redirect(url_for('admin_campaigns'))
          
          cur.execute("""
              SELECT step_number, step_label, message_template, delay_days, media_type
              FROM campaign_steps WHERE campaign_id = %s ORDER BY step_number
          """, (campaign_id,))
          steps = cur.fetchall()
          
          cur.execute("""
              SELECT i.id, i.name, i.status
              FROM campaign_instances ci
              JOIN instances i ON i.id = ci.instance_id
              WHERE ci.campaign_id = %s
          """, (campaign_id,))
          instances = cur.fetchall()
      conn.close()
      
      return render_template('admin/campaigns_edit.html',
                             campaign=campaign,
                             steps=steps,
                             instances=instances)
  ```

  **IMPORTANTE:** A rota `/admin/campaigns/new` DEVE ser registrada ANTES de `/admin/campaigns/<int:campaign_id>/edit` no Flask, senão "new" pode ser interpretado como um campaign_id.

### Task 16: Teste de integração

- **File:** `tests/test_admin_campaign_crud.py` (NOVO)
- **Action:** Teste pytest com mock da Uazapi:
  ```python
  import json
  import pytest
  from unittest.mock import patch, MagicMock
  
  def test_admin_create_campaign():
      """Superadmin cria campanha para outro usuário."""
      with patch('services.uazapi.UazapiService.create_advanced_campaign') as mock_uazapi:
          mock_uazapi.return_value = {'folder_id': 'test_folder_123', 'status': 'queued', 'count': 5}
          # ... setup: criar user, instance, scraping_job com leads
          # POST /api/admin/campaigns com admin auth
          # Verificar: campaigns.created_by_admin_id = admin.id
          # Verificar: campaigns.user_id = target_user.id
          # Verificar: campaign_instances existe
          # Verificar: campaign_leads populados
  ```
- **Notes:** Seguir padrão de `tests/test_validate_job_csv.py` (mock + tempfile para CSV).

---

## Acceptance Criteria deste Sprint

- [ ] `/admin/campaigns/new` exibe formulário completo com todos os campos
- [ ] Selecionar usuário carrega instâncias e jobs via AJAX
- [ ] Upload CSV com popup "Validar números?" funciona nos dois caminhos (sim/não)
- [ ] Submit do formulário cria campanha real via `POST /api/admin/campaigns`
- [ ] Após criar, redireciona para `/admin/campaigns` com flash message de sucesso
- [ ] `/admin/campaigns/{id}/edit` exibe campanha pré-preenchida
- [ ] Campos desabilitados conforme status (running → só nome; completed → tudo bloqueado)
- [ ] Seção de leads na edição mostra leads paginados com filtro por status
- [ ] Follow-ups accordion funciona (abrir/fechar, adicionar step, delay_days)
- [ ] Confirmação antes de criar: "Criar campanha X para Y?"
- [ ] Rota `/admin/campaigns/new` registrada antes de `/<campaign_id>/edit` no Flask
- [ ] Teste `test_admin_create_campaign` passa com mock Uazapi

## Verificação Final (Pós-Sprint 4)

**Feature completa — checklist end-to-end:**

1. ✅ Abrir `/admin/campaigns` → botão "Nova Campanha" visível
2. ✅ Contadores sincronizam via AJAX (números reais da Uazapi)
3. ✅ Cards com 3 botões: Detalhes, Editar, Excluir
4. ✅ "Nova Campanha" → formulário com cascata de dropdowns
5. ✅ Criar campanha para usuário X → aparece no painel do usuário X
6. ✅ Upload CSV com validação de números → preview correto
7. ✅ Follow-ups configurados → worker_cadence processa normalmente
8. ✅ Editar campanha `pending` → todos os campos editáveis
9. ✅ Editar campanha `running` → campos bloqueados
10. ✅ Leads na edição → tabela paginada com filtros
11. ✅ Excluir campanha → funciona como antes (folder deletada na Uazapi)
12. ✅ `created_by_admin_id` preenchido no DB para campanhas criadas pelo admin
