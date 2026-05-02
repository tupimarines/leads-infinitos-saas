---
title: 'Pôster do vídeo de fundo, troca de asset, Kanban (modo claro) e botões CSV'
slug: 'poster-video-kanban-csv-ui'
created: '2026-05-02'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
elicitation_applied: '2026-05-02 — métodos 1 (Focus Group), 3 (Critique and Refine), 4 (Comparative Matrix)'
party_mode_insights: '2026-05-02 — aceite: focus-visible CSV, AC escaneabilidade, QA opcional sub-campanhas L200–258'
tech_stack:
  - Python 3.x / Flask (Jinja2)
  - HTML/CSS inline em templates/base.html; tema via documentElement dataset.theme + class dark
  - Tailwind CSS v3 (CDN) só em campaigns_kanban.html; theme.extend mapeia ink/faint para var(--text)/var(--muted)
  - JavaScript vanilla (createLeadCard, drag-and-drop, fetch /api/.../kanban-data)
files_to_modify:
  - templates/campaigns_kanban.html
code_patterns:
  - Fundo global via base.html — entregue (poster print-static.jpeg, vídeo gemini-veo-novo.mp4); prefers-reduced-motion pausa #bg-video (base.html ~749–766)
  - Tema claro/escuro em :root e [data-theme="light"] em base.html; páginas que estendem base herdam tokens
  - Kanban lead cards createLeadCard() template literal ~1085–1119 com text-gray-* que sobrescrevem .lead-card { color: var(--text) }
  - Cabeçalhos de coluna HTML estático ~267–401 text-gray-200 / text-gray-500 em títulos e botões editar; botões Gerar/Forçar com verde/âmbar translúcidos (validar claro)
test_patterns:
  - pytest em tests/ — rede mínima; não cobre HTML do Kanban
  - Validação final: revisão humana visual (tema claro/escuro)
---

# Tech-Spec: Pôster do vídeo de fundo, troca de asset, Kanban (modo claro) e botões CSV

**Created:** 2026-05-02

**Âmbito deste documento:** O título cobre o épico completo (fundo + Kanban); **código ainda por implementar** restringe-se a `templates/campaigns_kanban.html`. Poster e vídeo em `base.html` já estão em `main`.

## Overview

### Problem Statement

**Já resolvido (código em `main`):** O utilizador via um poster inadequado (`favicon`) e vídeo antigo; o fundo não refletia a marca durante o carregamento. Isto foi corrigido com **`static/img/print-static.jpeg`** como `poster` e **`static/video/gemini-veo-novo.mp4`** como fonte do `<video id="bg-video">` em `templates/base.html` (shell existente com `object-fit: cover`, `100svh`/`100dvh`).

**Em aberto:** No **modo claro**, os **lead cards** do Kanban continuam a usar classes Tailwind **`text-gray-200`**, **`text-gray-500`**, **`text-gray-400`**, **`text-gray-600`** dentro do template string de `createLeadCard()` — pensadas para fundo escuro, geram **baixo contraste** sobre cartão claro. Os **cabeçalhos de coluna** usam `text-gray-200` / `text-gray-500` em HTML estático. Os botões **CSV 1–4** e **CSV Break-up** são essencialmente *outline*; o segundo com **`text-yellow-200/90`** desaparece em fundo claro.

### Solution

**Entregue:** Poster dedicado + vídeo normalizado em `static/`, referenciados em `base.html`.

**Pendente:** (1) Tipografia dos cards e metadados alinhada a **`text-ink` / `text-faint`** com variantes **`dark:`** onde necessário (abordagem B da matriz), sem regressão no tema escuro. (2) Botões CSV com **fills** distintos, texto legível em claro e escuro, **`focus-visible`** para teclado. (3) Cabeçalhos de coluna com tokens de tema. (4) Botões **Gerar** / **Forçar** nos cabeçalhos (quando `use_uazapi_sender`): legibilidade em modo claro. (5) Ajuste recomendado no mesmo PR: badge “Enviado”, links e tons secundários em `createLeadCard`. (6) **Follow-up opcional:** bloco sub-campanhas (procurar `Sub-campanhas` no template) com `text-gray-*` fixo — QA humano; corrigir só se falhar em claro.

### Scope

**In Scope:**

- `templates/campaigns_kanban.html`: localizar alterações por **âncora de código** (não só por número de linha — o ficheiro muda): `onclick="downloadRemanentCsv('`, `function downloadRemanentCsv`, `function createLeadCard`, `.kanban-column-header`, bloco sub-campanhas se aplicável.
- Confirmação de que `templates/base.html` não precisa de alterações para este épico (vídeo/poster já feitos).

**Out of Scope:**

- Endpoints `export-remanent-csv` ou handlers Python.
- Redesenho de colunas, DnD ou modais.
- Remoção de `static/video/gemini-veo-video.mp4` (PR separado).
- Playwright/E2E novo (apenas manual neste épico).

## Elicitação avançada (1 + 3 + 4)

### Focus Group (síntese)

| Persona | Reação | Prioridade |
| ------- | ------ | ---------- |
| Operador (modo claro) | Nome/data ilegíveis; CSV Break-up invisível | P0 |
| Gestor (demo) | Título de coluna cinza em branco | P1 |
| Power user escuro | Sem regressão visual | P0 |

### Matriz → decisão

Recomendação **B** (classes no JS/HTML com `text-ink` / `text-faint` / `dark:`) + **A** mínimo se algum span dinâmico escapar.

## Party Mode (insights aceites)

- **UX:** Hierarquia clara — nome forte (`text-ink`), secundário `text-faint` com contraste mínimo no claro; CSV como ações visualmente distintas; Break-up com fundo âmbar/laranja e **texto escuro** sobre fill claro, não amarelo pastel.
- **Dev:** Modal de lead já usa tokens — fora do P0 salvo evidência de falha.
- **Acessibilidade:** Ambos os botões CSV com **`focus-visible:ring-2`** (ou classes Tailwind equivalentes `focus-visible:ring-*`).
- **Produto:** Critério de review humano — em modo claro, os **dois rótulos CSV** são identificáveis num olhar (alinhar com **AC3**; sem cronómetro obrigatório).
- **QA opcional:** Sub-campanhas L200–258 — verificar legibilidade em claro; corrigir em follow-up se necessário.

## Context for Development

### Codebase Patterns

- **Layout:** Rotas principais `{% extends "base.html" %}`; fundo de vídeo global já configurado.
- **`templates/base.html`:** `prefers-reduced-motion` controla `#bg-video`; não alterar neste épico.
- **`templates/campaigns_kanban.html`:** `tailwind.config.darkMode = 'class'` coerente com `html.dark`; utilitários `ink`/`faint` mapeados a variáveis CSS do tema.
- **Conflito:** `.lead-card { color: var(--text) }` é sobreposto por `text-gray-*` nos elementos filhos gerados em JS.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `templates/base.html` | Tokens e vídeo (referência; sem mudanças previstas) |
| `templates/campaigns_kanban.html` | Único ficheiro a editar |
| `_bmad-output/project-context.md` | Mudanças incrementais |
| `_bmad-output/implementation-artifacts/tech-spec-ui-toggle-tema-claro-escuro-video-fundo.md` | Tokens de tema |

### Technical Decisions

- Implementação principal: **abordagem B** (trocar classes no template string e no HTML estático) com **`dark:`** para preservar aparência no escuro.
- CSV: fill **roxo/violeta suave** (ou alinhado a `--primary`) para “1–4”; fill **âmbar/laranja** com texto **escuro** para “Break-up”.
- Não introduzir bibliotecas; só Tailwind CDN + CSS existente no bloco `<style>` se precisar de uma regra pontual.
- **Gate de qualidade:** a decisão final de contraste e legibilidade é **revisão humana visual** (não obrigatório WCAG automatizado neste épico); ferramentas de contraste no DevTools são opcionais para o implementador.

## Implementation Plan

### Tasks

- [x] **Task 1:** Botões de exportação CSV com fill, contraste e foco teclado  
  - File: `templates/campaigns_kanban.html`  
  - Action: Localizar os dois `<button type="button"` com `onclick="downloadRemanentCsv('funnel')"` e `onclick="downloadRemanentCsv('breakup')"` (vizinhos no markup). Substituir **apenas** classes Tailwind/CSS: `bg-*` distintos por botão, `text-*` legível em claro e escuro (`dark:` onde preciso), `border` e `hover:` coerentes, mais **`focus-visible:ring-2`** (e `ring-offset` ou `ring-inset` conforme layout) visível em **claro e escuro**. Manter `onclick`, `title` e texto visível dos rótulos. A função `function downloadRemanentCsv(scope)` (mesmo ficheiro) **não** muda na assinatura nem no corpo.  
  - Notes: Break-up: evitar `text-yellow-200` sobre fundo claro; texto escuro sobre fill âmbar + variante `dark:text-*` no escuro.

- [x] **Task 2:** Cabeçalhos das colunas — títulos, editar, **Gerar** e **Forçar**  
  - File: `templates/campaigns_kanban.html`  
  - Action: Em cada `.kanban-column-header`, alinhar títulos (`text-gray-200` → `text-ink` com `dark:` se necessário) e botões “Editar Mensagem” (`text-gray-500 hover:text-white` → `text-faint` / `hover:text-ink` com `dark:` no hover). Incluir os botões **`Gerar`** / **`Forçar`** (`openStageCampaignModal`, `forceUnlockStage`) onde existirem: em modo **claro**, validar legibilidade (texto/verde/âmbar translúcidos atuais); ajustar classes para contraste sem mudar `onclick` nem `data-*`.  
  - Notes: `column-badge` pode manter-se; smoke visual claro/escuro.

- [x] **Task 3:** `createLeadCard` — tipografia e estados visuais no card  
  - File: `templates/campaigns_kanban.html`  
  - Action: Dentro de `function createLeadCard(lead, targetStep)`, atualizar o **template literal** do `div.innerHTML` (bloco com `text-gray-200`, `text-gray-500`, etc.): nome `text-ink` (+ `dark:` se preciso); telefone e metas `text-faint` + `dark:`; “Último:”/data legíveis no claro; badge “Enviado” com par claro/escuro; links ícone com `text-faint` / hover. **Não** alterar `sentThisStage`, `aria-label`, drag handlers.  
  - Notes: `.lead-card { color: var(--text) }` no `<style>` não basta — os filhos com utilitários Tailwind continuam a mandar.

- [x] **Task 4:** Smoke test e regressão  
  - File: n/a (QA)  
  - Action: **Validação final autoritativa:** revisão humana visual em claro e escuro (desktop + viewport estreita), Tab nos dois CSV, zoom 125%. Opcional: Safari/iOS ou WebKit se disponível (anel `focus-visible`). Sub-campanhas: se ilegíveis em claro, follow-up ou patch trivial no mesmo PR.  
  - Notes: Correr `pytest` (idealmente suíte relevante ou CI) — nenhuma alteração Python esperada; testes **não** substituem o olho humano neste épico. **Smoke automático (2026-05-02):** `pytest tests/test_campaign_leads_ui_status.py` — 22 passed, 1 skipped. Suíte `tests/` completa não executada aqui (PostgreSQL em `localhost:5432` indisponível neste ambiente); CI ou máquina com DB cobre regressão Python adicional.

### Acceptance Criteria

- [ ] **AC1:** Given tema **claro** ativo, when abro o Kanban de uma campanha com leads, then o **nome** e o **telefone** em cada `.lead-card` são legíveis à primeira vista (sem cinza muito claro sobre o fundo do card) e os **títulos das colunas** são legíveis.
- [ ] **AC2:** Given tema **escuro** ativo, when o mesmo ecrã após as alterações, then **cabeçalhos de coluna**, **texto dos lead cards** e **rótulos dos dois CSV** permanecem **claramente legíveis** (sem texto lavado ou “apagado”); aprovação por **revisão humana visual** comparando com o comportamento anterior ou captura de referência anexada ao PR.
- [ ] **AC3:** Given modo **claro**, when olho para os dois botões de exportação no header do Kanban, then cada um tem **área preenchida (não só outline)** e **rótulos “CSV 1–4” / “CSV Break-up”** distinguem-se um do outro sem esforço; confirmação final por **revisão humana** (objetivo de escaneabilidade, sem cronómetro obrigatório).
- [ ] **AC4:** Given foco por **teclado** (Tab), when o foco está em “CSV 1–4” e “CSV Break-up”, then cada botão mostra **indicador `focus-visible`** claramente visível em claro e em escuro.
- [ ] **AC5:** Given utilizador autenticado, when clico “CSV 1–4” ou “CSV Break-up”, then o fluxo de `downloadRemanentCsv` (navegação/download) permanece **inalterado** face ao comportamento anterior.
- [ ] **AC6 (sanity opcional):** If ninguém editou `templates/base.html` neste PR, when carregar qualquer página que use o layout comum, then **não** se introduziram regressões no vídeo/poster; se `base.html` for tocado, validar `prefers-reduced-motion` no `#bg-video`.
- [ ] **AC7 (opcional):** Given campanha com **sub-campanhas** visíveis, when modo claro, then o bloco correspondente é **legível** por revisão humana ou fica aberto follow-up documentado.

## Revisão adversarial (alterações objetivas integradas)

- Âncoras de implementação por **strings** (`downloadRemanentCsv`, `createLeadCard`, cabeçalhos) em vez de depender só de números de linha.
- **Task 2** alinhada ao **Context** (botões Gerar/Forçar incluídos).
- **AC2/AC3** desdobrados em critérios **observáveis**, mantendo **humano** como decisão final (sem exigir WCAG automatizado).
- **AC6** reposto como sanity **condicional** (só relevante se `base.html` mudar).
- **Testing:** explicitar que o olho humano manda; pytest como rede de segurança mínima, não prova de UI.

## Additional Context

### Dependencies

- Nenhuma dependência npm/pip nova. Tailwind via CDN já carregado na página Kanban.

### Testing Strategy

- **Autoritativo:** **Revisão humana visual** em tema claro e escuro (aprovação final do PR).
- **Automático (rede de segurança):** `pytest` no CI ou localmente (`tests/` ou alvo mínimo `tests/test_campaign_leads_ui_status.py`); não prova UI — só garante que Python não foi tocado inadvertidamente.
- **Manual sugerido:** Tab / Shift+Tab nos CSV; zoom 125%; viewport estreita; opcional WebKit/Safari para `focus-visible`; opcional inspetor de contraste no DevTools (não substitui o humano).

### Notes

- Números de linha citados noutros artefactos são **indicativos**; usar sempre **grep** pelas âncoras deste spec.
- **Risco:** Combinações `dark:` erradas podem lavar texto no escuro — validar sempre ambos os temas na revisão humana.
- **Limitação:** Sem teste E2E; regressões só apanham em QA manual ou futuro Playwright.
- **Futuro:** Unificar sub-campanhas para tokens; remover `gemini-veo-video.mp4` morto; crop de `print-static.jpeg` se duplicação vertical incomodar em certos rácios.
