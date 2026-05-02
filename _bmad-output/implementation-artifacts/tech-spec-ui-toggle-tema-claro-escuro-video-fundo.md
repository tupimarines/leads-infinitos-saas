---
title: 'UI — toggle tema claro/escuro, paleta revisada e vídeo de fundo global'
slug: ui-toggle-tema-claro-escuro-video-fundo
created: '2026-05-01T12:00:00Z'
status: ready-for-dev
stepsCompleted: [1, 2, 3, 4]
tech_stack:
  - Flask 3.x
  - Jinja2
  - CSS custom properties
  - Tailwind CSS (CDN em páginas selecionadas)
  - HTML5 video
files_to_modify:
  - templates/base.html
  - static/video/gemini-veo-video.mp4 (copiar do root do repo)
  - templates/dashboard.html
  - templates/campaigns_kanban.html
  - templates/campaigns_list.html
  - templates/campaigns_new.html
  - templates/campaigns_edit.html
  - templates/account.html
  - templates/admin/dashboard.html
  - templates/admin/campaigns.html
  - templates/admin/campaigns_new.html
  - templates/admin/campaigns_edit.html
  - templates/admin/users.html
  - templates/admin/dispatch_audit.html
  - templates/whatsapp_config.html
code_patterns:
  - Variáveis CSS em :root e overrides por data-theme
  - Preferir darkMode class do Tailwind onde o CDN é usado
  - Camada fixa de vídeo + overlay para legibilidade
test_patterns:
  - Sem suite E2E automatizada identificada; validação manual nas rotas principais
---

# Tech-Spec: UI — toggle tema claro/escuro, paleta revisada e vídeo de fundo global

**Criado:** 2026-05-01

## Overview

### Problem Statement

O produto hoje é majoritariamente **tema escuro** centralizado em `base.html` (variáveis `--bg`, `--card`, etc.), enquanto várias páginas complementam com **Tailwind via CDN** e cores fixas (`bg`, `card`, classes `text-gray-*`). Não há persistência de preferência de tema nem modo claro aplicado de forma consistente em **todas** as superfícies (layout, modais, botões). O fundo atual usa gradiente + `bg-pattern.png` em `body.themed-bg`. A direção de marca desejada é alinhar escuro/claro às referências visuais (roxo profundo, azul/ciano neon, magenta/rosado em gradientes) e substituir o fundo estático por um **vídeo responsivo** (`gemini-veo-video.mp4`).

### Solution

1. Introduzir **`data-theme="dark"` | `data-theme="light"`** no elemento raiz (`html`), com script que aplica o valor inicial (preferência salva em `localStorage`, com fallback opcional a `prefers-color-scheme`) e um **botão toggle** na barra de navegação (`base.html`).
2. **Rever tokens** no escuro (base mais índigo/violeta, acentos ciano/magenta sutis nas bordas e gradientes) e definir **tokens do claro**: fundo creme/branco gelo, texto escuro, **bordas roxas** discretas, gradientes roxo↔rosado leves em botões primários — inspirados nos anexos e nos PNGs em `assets/` do workspace.
3. Substituir o fundo do `body` por uma **camada fixa de vídeo** (copiar `gemini-veo-video.mp4` para `static/video/`), com `object-fit: cover`, overlay semântico por tema para contraste, e comportamento acessível sob **`prefers-reduced-motion`** (pausar vídeo ou mostrar poster estático).
4. Para páginas Tailwind: adotar **`darkMode: 'class'`** sincronizado com `data-theme` (quando tema escuro, manter classe `dark` no `html` conforme contrato do Tailwind) e **migrar cores hardcoded** para utilitários que funcionem nos dois modos (ex.: pares `text-slate-700 dark:text-gray-200` ou cores `extend` ligadas a variáveis CSS).

### Scope

**In scope:**

- Toggle de tema + persistência (`localStorage`) e hidratação sem flash (script inline mínimo no `<head>` ou logo após `<html>`).
- Tokens de cor escuros/claros e overlays para vídeo em `base.html` (e extrair para `static/css/theme.css` apenas se o arquivo ultrapassar limite confortável de manutenção — preferência: manter um único lugar de verdade na primeira iteração).
- Camada global de vídeo em **todas** as páginas que estendem `base.html` (universo atual do projeto).
- Ajuste de **nav**, **footer**, **botões**, **inputs**, **cards**, **flash messages** via variáveis e seletores `[data-theme="light"]`.
- Modais: overlays e painéis em `whatsapp_config.html`, `admin/campaigns.html`, `admin/users.html`, `campaigns_kanban.html` — cores alinhadas ao tema (sem cinza “fixo de dark” que quebre no claro).
- Revisão das páginas que usam **Tailwind CDN** listadas em `files_to_modify`.

**Out of scope:**

- Refatoração grande de `app.py` ou lógica de negócio.
- Build pipeline Tailwind (PostCSS); permanece CDN conforme hoje.
- Otimização pesada de vídeo (codecs alternativos, múltiplas resoluções) — pode ser nota futura; neste passo, arquivo único servido estaticamente.
- Temas adicionais (ex.: “sistema” como terceiro botão) — opcional futuro; especificar apenas dark/light.

## Context for Development

### Codebase Patterns

- **Layout único:** praticamente todos os templates fazem `{% extends "base.html" %}` — o vídeo e o toggle devem morar em `base.html` para cobrir “todas as páginas”.
- **CSS principal:** grande bloco `<style>` inline em `base.html` com `:root` e classes utilitárias (`.btn`, `.card`, `.nav`, `.field`).
- **Segundo sistema visual:** vários templates carregam `cdn.tailwindcss.com` em `{% block extra_head %}` com `tailwind.config.theme.extend.colors` fixos (`bg`, `card`, `primary`, `accent`). Isso **não** reage ao tema até migrar para `darkMode: 'class'` + classes condicionais ou cores derivadas de variáveis CSS.
- **Modais heterogêneos:** mistura de Tailwind (`bg-black/70`, `text-gray-200`), inline styles (`rgba(0,0,0,0.7)` no QR modal), e IDs específicos — cada um precisa de passagem explícita para tokens ou utilitários `dark:`.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `templates/base.html` | Variáveis CSS globais, body, nav, footer, scripts mobile menu — ponto central para tema e vídeo |
| `templates/dashboard.html` | Exemplo Tailwind + cards glass |
| `templates/campaigns_kanban.html` | Tailwind denso + modal de lead |
| `templates/admin/campaigns.html` | Modais admin + Tailwind |
| `templates/whatsapp_config.html` | Modal QR com estilo inline |
| `_bmad-output/project-context.md` | Já menciona modo claro planejado e variáveis CSS |
| `gemini-veo-video.mp4` (raiz do repo) | Origem do asset a servir via `static/` |

### Technical Decisions

| Decisão | Motivo |
| ------- | ------ |
| `data-theme` no `html` + sincronizar classe `dark` para Tailwind | Um hook único para CSS legado em `base.html` e para utilitários `dark:` do CDN |
| Vídeo em camada `position: fixed; z-index` abaixo do conteúdo | Responsivo global sem alterar cada template |
| Overlay por tema (escuro mais opaco, claro levemente rosado/branco translúcido) | Legibilidade sobre vídeo neon; alinha ao modo claro “creme” |
| `prefers-reduced-motion: reduce` → `video.pause()` e opcionalmente poster | Acessibilidade e economia de bateria/dados |
| Modo claro: fundo `#f7f4ef`–`#faf8f5` (creme), borda `#c4b5fd`–`#8b5cf6` em 1px, texto `#1e1b2e` | Traduz a intenção “branco gelo + bordas roxinhas”; gradientes botão: roxo → magenta suave (~5–15% peso visual no gradiente) |
| Modo escuro revisado: base `#0a0618`–`#12082a`, acento ciano `#22d3ee` ou `#67e8f9` para rings/bordas ativas, magenta `#e879f9`/`#f472b6` em gradientes | Aproxima das referências sem estourar contraste WCAG em textos |

### Design feedback (produto)

A combinação **creme/branco gelo + bordas roxas + toque rosado nos gradientes** é coerente com as referências e com o gradiente já usado em `.btn-primary` em `base.html`. Vale intensificar no escuro o **contraste entre fundo e superfície** (`--card` vs `--bg`) para cartões e modais continuarem legíveis sobre o vídeo.

## Implementation Plan

### Tasks

- [x] **Task 1:** Copiar `gemini-veo-video.mp4` da raiz do repositório para `static/video/gemini-veo-video.mp4` (criar pasta se necessário). Garantir que o arquivo não seja ignorado pelo `.gitignore` se o projeto for versionar o asset (se for grande, documentar em Notes uso de Git LFS ou hospedagem externa).

- [x] **Task 2:** Em `templates/base.html`, adicionar estrutura:
  - `<div class="bg-video-shell" aria-hidden="true">` contendo `<video>` com `autoplay`, `muted`, `loop`, `playsinline`, `preload="metadata"`, `poster` opcional (primeiro frame exportado ou placeholder em `static/img/`).
  - `<div class="bg-video-overlay"></div>` com opacidade/cor definidas por `[data-theme]`.
  - Wrapper `.app-root` envolvendo `.nav`, `{% block body %}`, `.footer` com `position: relative; z-index` acima do vídeo.

- [ ] **Task 3:** Substituir/remover dependência visual exclusiva de `body.themed-bg` + `bg-pattern.png` como fundo principal; manter pattern apenas se ainda agregar sobre o vídeo (provavelmente desligado por padrão para não poluir). Ajustar `body` para fundo transparente ou cor de fallback sólida igual a `--bg` quando vídeo falhar.

- [ ] **Task 4:** Expandir `:root` com tokens completos para **dark** (valores revisados) e adicionar bloco `[data-theme="light"]` com overrides para todas as variáveis usadas por `body`, `.nav`, `.btn`, `.btn-primary`, `.card`, `.field`/`input`, `.flash`, `.footer`, `.badge`, `.brand-badge`, focus rings. Incluir variáveis `--video-overlay-opacity`, `--video-overlay-tint` se útil.

- [ ] **Task 5:** Script de tema (inline cedo no `<head>` ou imediatamente após abrir `<html>`):
  - Ler `localStorage.getItem('theme')` (`'dark'|'light'`).
  - Se vazio, opcional: `window.matchMedia('(prefers-color-scheme: dark)')`.
  - Setar `document.documentElement.dataset.theme` e `classList.toggle('dark', theme === 'dark')` para Tailwind.
  - Pequeno listener `prefers-reduced-motion` para pausar vídeo após `DOMContentLoaded`.

- [ ] **Task 6:** Toggle na `.nav-inner` (ao lado de `.nav-cta` ou dentro em mobile): botão acessível (`aria-pressed`, `aria-label` “Alternar tema claro e escuro”), alterna tema e persiste em `localStorage`.

- [ ] **Task 7:** Para cada arquivo que usa Tailwind CDN (`dashboard.html`, `campaigns_*`, `account.html`, `admin/*` listados no frontmatter):
  - Adicionar `tailwind.config.darkMode = 'class'`.
  - Alinhar `theme.extend.colors` a variáveis CSS (ex.: cores nomeadas `surface`, `elevated`, `muted`) **ou** trocar classes literais `text-gray-200` por pares claros/escuros explícitos.
  - Revisar `.stat-card` e estilos inline em `<style>` dos templates para usar `var(--*)` ou classes `dark:`.

- [ ] **Task 8:** Modais — exemplos obrigatórios neste PR:
  - `templates/whatsapp_config.html`: remover dependência de overlay preto fixo; usar classe tokenizada (ex.: `modal-backdrop` com `background: rgba` via variável tema).
  - `templates/admin/campaigns.html`: substituir `bg-black/80` por utilitários compatíveis com ambos os modos (`bg-black/60 dark:bg-black/80` + painel claro no tema light).
  - `templates/campaigns_kanban.html`: painel modal e `body.modal-open` sem assumir fundo escuro único.
  - `templates/admin/users.html`: `#detailsModal` e conteúdos internos.

- [ ] **Task 9:** Responsividade do vídeo: garantir cobertura mobile (`object-fit: cover`, `min-height: 100svh`, teste em largura estreita). Evitar que o vídeo empurre layout (permanecer `fixed`).

- [ ] **Task 10:** Verificar páginas que não usam Tailwind mas estendem `base.html` (`login.html`, `register.html`, `index.html`, etc.) para regressões visuais no modo claro.

### Acceptance Criteria

- [ ] **AC 1:** Given usuário na primeira visita sem preferência salva, when a página carrega, then o tema aplicado segue a regra definida (padrão recomendado: **dark** para preservir aparência atual da maioria dos usuários, ou **system** se explicitamente implementado — registrar a escolha no código e neste spec).

- [ ] **AC 2:** Given usuário clica no toggle de tema, when a ação completa, then `data-theme` alterna entre `dark` e `light`, a classe `dark` do Tailwind permanece sincronizada, e o valor é persistido em `localStorage` para o próximo carregamento.

- [ ] **AC 3:** Given recarregamento da página após escolher modo claro, when o HTML é parseado, then não ocorre flash prolongado do modo errado (FOUC mínimo: script de hidratação executa antes da primeira pintura relevante ou usa fallback de cor de fundo neutra).

- [ ] **AC 4:** Given modo claro ativo, when o usuário navega por Dashboard, Lista de campanhas, Kanban (modal aberto), Nova campanha, Admin campanhas (modais), WhatsApp config (modal QR) e Minha conta, then textos, bordas e superfícies permanecem legíveis e coerentes com bordas roxas e fundo creme.

- [ ] **AC 5:** Given modo escuro ativo revisado, when o mesmo fluxo da AC 4 é exercitado, then a UI mantém hierarquia visual (contraste WCAG AA em texto principal onde aplicável) e acentos alinhados à paleta roxo/azul/ciano/rosado.

- [ ] **AC 6:** Given viewport desktop e mobile, when a página é exibida, then o vídeo de fundo cobre o viewport sem barras pretas laterais visíveis desproporcionais e sem quebrar scroll ou sticky nav.

- [ ] **AC 7:** Given `prefers-reduced-motion: reduce`, when a página carrega, then o vídeo não reproduz em loop contínuo (pausado ou substituído por poster estático conforme implementação).

- [ ] **AC 8:** Given falha ao carregar o vídeo (rede ou formato), when o usuário usa o app, then o fundo degrada para gradiente/cor sólida definida por `--bg` sem conteúdo sobreposto ilegível.

## Additional Context

### Dependencies

- Nenhuma nova dependência Python obrigatória.
- Asset de vídeo em `static/video/` servido por Flask `url_for('static', filename='video/gemini-veo-video.mp4')`.

### Testing Strategy

- **Manual:** login, dashboard, CRUD campanhas, kanban + abrir modal de lead, admin campanhas + modais, whatsapp config + QR modal, alternar tema em cada uma; repetir em largura ~375px e ~1280px.
- **Acessibilidade:** contraste aproximado (DevTools ou checklist), foco visível em inputs em ambos os temas, `aria` no toggle.

### Notes

- **Peso do vídeo:** MP4 grande pode impactar clone e deploy; avaliar compressão ou hospedagem externa + URL absoluta se necessário.
- **Bootstrap:** `base.html` inclui `bootstrap.bundle.min.js`; validar se algum componente Bootstrap visual conflita com o modo claro (não há Bootstrap CSS global evidente no trecho lido — confirmar outras páginas).
- **Imagens de referência:** PNGs salvos em `assets/` no workspace podem ser usados como moodboard interno; não é obrigatório commitá-los no produto.

---

**Resumo para implementação:** 10 tarefas focadas em `base.html` + vídeo estático + migração tema nas páginas Tailwind e modais; 8 critérios de aceitação em Given/When/Then.

Quando for implementar em agente novo, usar prompt: `quick-dev _bmad-output/implementation-artifacts/tech-spec-ui-toggle-tema-claro-escuro-video-fundo.md`.
