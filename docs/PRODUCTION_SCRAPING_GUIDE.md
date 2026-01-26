# Guia de Scraping em Produção: Evitando Bloqueios e Detecção

Este guia consolida as melhores práticas para manter o scraper de Google Maps operando de forma confiável em ambientes de produção, baseado nas técnicas mais recentes de evasão de bots e "stealth scraping".

## 1. Camada de Navegador (Playwright)

### ✅ Já Implementado (Nível Básico/Intermediário)
*   **User-Agent Falso**: Mascarar o User-Agent para parecer um navegador de usuário real (Windows/Chrome).
*   **Client Hints (`sec-ch-ua`)**: Forçar os cabeçalhos de baixa granularidade para bater com o User-Agent, evitando a discrepância óbvia que causa bloqueios imediatos.
*   **Remoção de Flags de Automação**: Uso de `--disable-blink-features=AutomationControlled` para esconder a flag `navigator.webdriver`.

### 🚀 Recomendações Avançadas (Para Implementar se Bloqueios Voltarem)

1.  **Plugin de Stealth Dedicado**
    *   **Ferramenta**: `playwright-stealth` (Python)
    *   **Função**: Aplica automaticamente dezenas de correções de fingerprint (WebGL, Console, plugins instalados, idiomas).
    *   **Como usar**:
        ```python
        from playwright_stealth import stealth_sync
        # ... dentro do loop do browser ...
        page = context.new_page()
        stealth_sync(page)
        ```

2.  **Rotação de Viewport e Dispositivo**
    *   Em vez de usar sempre 1920x1080, varie ligeiramente as resoluções ou emule dispositivos móveis reais (iPhone, Pixel) rotativamente.
    *   O Playwright possui `playwright.devices['iPhone 13']`, que já configura User-Agent, Viewport e DPI corretos automaticamente.

3.  **Mouse e Interação Humana**
    *   O Google Maps rastreia movimentos de mouse. Clicar instantaneamente em coordenadas exatas é suspeito.
    *   **Melhoria**: Adicionar pequenas curvas ou "overshoot" no movimento do mouse antes de clicar, e variar o tempo de digitação (keystroke dynamics).

## 2. Camada de Rede (Infraestrutura)

Se o IP do servidor for marcado ("flagged"), nenhuma técnica de código vai resolver.

1.  **Proxies Residenciais Rotativos (Crítico para Escala)**
    *   IPs de Data Center (AWS, DigitalOcean, Azure) são facilmente detectados pelo Google.
    *   Use serviços como **Bright Data**, **Smartproxy** ou **Oxylabs**.
    *   Configure o Playwright para usar um proxy rotativo, garantindo que cada Job ou cada Sessão saia por um IP diferente.

2.  **TLS Fingerprinting (JA3)**
    *   Sistemas anti-bot analisam o "aperto de mão" SSL/TLS. O Playwright (baseado em Chrome) geralmente passa bem aqui, mas bibliotecas puras de Python como `requests` ou `aiohttp` geralmente falham. Continue usando navegadores reais.

## 3. Estratégia Operacional

1.  **"Warm-up" de Sessão (Cookies)**
    *   Navegadores "frescos" (sem cookies) acessando diretamente endpoints profundos são suspeitos.
    *   Acesse a home do Google, faça uma pesquisa aleatória, e *depois* vá para o Maps. Isso gera um histórico de cookies mais legítimo.

2.  **Limitação de Taxa (Rate Limiting)**
    *   Não faça requisições tão rápido quanto o computador aguenta.
    *   Adicione um `random.sleep(2, 5)` entre ações críticas. É mais lento, mas "lento é suave, e suave é rápido" (e não bloqueia).

3.  **Monitoramento de "Soft Bans"**
    *   As vezes o Google não dá erro 403, apenas retorna resultados vazios ou dados genéricos.
    *   Implemente verificações de "Sanity Check" no HTML retornado para garantir que os dados extraídos fazem sentido antes de salvar.

## Resumo da Ação Corretiva Atual

No erro recente, o problema era **Discrepância de Cabeçalho**: O navegador dizia ser "Chrome Windows" no User-Agent, mas "HeadlessChrome" nos detalhes técnicos do protocolo HTTP (`sec-ch-ua`).

A correção aplicada no arquivo `main.py` sincronizou esses sinais.

**Se falhar novamente:** O próximo passo lógico é integrar o pacote `playwright-stealth` e considerar o uso de proxies residenciais se o volume de requisições aumentar.
