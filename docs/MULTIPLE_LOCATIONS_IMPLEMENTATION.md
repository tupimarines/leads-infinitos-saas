# 🚀 Implementação de Múltiplas Localizações - Plano Detalhado

## 📋 Visão Geral
Implementar funcionalidade para buscar a mesma palavra-chave em múltiplos bairros/localizações, concatenando os resultados em um único arquivo CSV/Excel com deduplicação automática.

## 🎯 Objetivo Final
- Interface para adicionar até 15 localizações diferentes
- Busca sequencial (uma por vez) para estabilidade
- Resultado único concatenado com deduplicação
- Manter compatibilidade com sistema atual

---

## 📅 FASE 1: Preparação e Análise
**Duração estimada:** 30 minutos  
**Arquivos a modificar:** Nenhum (apenas análise)

### 1.1 Análise do Código Atual
- [ ] Revisar `app.py` linha 882-912 (rota `/scrape`)
- [ ] Revisar `main.py` linha 138-286 (função `run_scraper`)
- [ ] Revisar `templates/index.html` linha 26-44 (formulário atual)
- [ ] Entender fluxo: Form → Flask → main.py → BusinessList

### 1.2 Identificar Pontos de Modificação
- [ ] **Frontend:** Adicionar interface para múltiplas localizações
- [ ] **Backend:** Modificar rota `/scrape` para aceitar lista de localizações
- [ ] **Scraper:** Adaptar `run_scraper` para processar lista de queries
- [ ] **Deduplicação:** Usar sistema existente de `Business.__hash__()`

### 1.3 Backup e Preparação
- [ ] Fazer backup do `app.py` atual
- [ ] Fazer backup do `templates/index.html` atual
- [ ] Criar branch git para desenvolvimento: `git checkout -b feature/multiple-locations`

---

## 📅 FASE 2: Modificação da Interface (Frontend)
**Duração estimada:** 45 minutos  
**Arquivos a modificar:** `templates/index.html`

### 2.1 Adicionar JavaScript para Múltiplas Localizações
```javascript
// Adicionar no final do template, antes do </body>
<script>
let locationCount = 1;

function addLocationField() {
    if (locationCount >= 15) {
        alert('Máximo de 15 localizações permitidas');
        return;
    }
    
    const container = document.getElementById('locations-container');
    const newField = document.createElement('div');
    newField.className = 'location-field';
    newField.innerHTML = `
        <div class="field" style="display: flex; gap: 8px; align-items: end;">
            <input type="text" name="localizacoes[]" required 
                   placeholder="Ex.: Portão, Curitiba" style="flex: 1;">
            <button type="button" onclick="removeLocationField(this)" 
                    style="background: #ff5d5d; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer;">
                ×
            </button>
        </div>
    `;
    container.appendChild(newField);
    locationCount++;
}

function removeLocationField(button) {
    if (locationCount <= 1) return;
    button.parentElement.parentElement.remove();
    locationCount--;
}
</script>
```

### 2.2 Modificar Formulário HTML
```html
<!-- Substituir o campo de localização atual por: -->
<div class="field">
    <label>Localizações <span class="muted">(máximo 15)</span></label>
    <div id="locations-container">
        <div class="location-field">
            <div class="field" style="display: flex; gap: 8px; align-items: end;">
                <input type="text" name="localizacoes[]" required 
                       placeholder="Ex.: Portão, Curitiba" style="flex: 1;">
                <button type="button" onclick="removeLocationField(this)" 
                        style="background: #ff5d5d; color: white; border: none; padding: 8px 12px; border-radius: 4px; cursor: pointer;">
                    ×
                </button>
            </div>
        </div>
    </div>
    <button type="button" onclick="addLocationField()" 
            style="margin-top: 8px; background: #4f7cff; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer;">
        + Adicionar Localização
    </button>
</div>
```

### 2.3 Adicionar Validação JavaScript
```javascript
// Adicionar validação no submit do formulário
document.querySelector('form').addEventListener('submit', function(e) {
    const locations = document.querySelectorAll('input[name="localizacoes[]"]');
    if (locations.length === 0) {
        e.preventDefault();
        alert('Adicione pelo menos uma localização');
        return;
    }
    
    // Verificar se todos os campos estão preenchidos
    for (let input of locations) {
        if (!input.value.trim()) {
            e.preventDefault();
            alert('Preencha todas as localizações');
            return;
        }
    }
});
```

### 2.4 Testar Interface
- [x] Verificar se botão "Adicionar Localização" funciona
- [x] Verificar se botão "×" remove campos
- [x] Testar limite de 15 localizações
- [x] Verificar validação de campos vazios

---

## 📅 FASE 3: Modificação do Backend (Flask)
**Duração estimada:** 30 minutos  
**Arquivos a modificar:** `app.py`

### 3.1 Modificar Rota `/scrape`
```python
# Localizar linha 882-912 e substituir por:
@app.route("/scrape", methods=["POST"]) 
@login_required
def scrape():
    # Verificar se usuário tem licença ativa
    if not current_user.has_active_license():
        flash("Sua licença expirou ou não está ativa. Entre em contato com o suporte para renovar.")
        return redirect(url_for("index"))
    
    palavra_chave = request.form.get("palavra_chave", "").strip()
    localizacoes = request.form.getlist("localizacoes[]")  # Lista de localizações
    total_raw = request.form.get("total", "").strip() or "100"
    
    try:
        total = int(total_raw)
    except Exception:
        total = 100
    
    # Guardrails: clamp total and inputs
    total = max(1, min(total, 500))
    if len(palavra_chave) > 100:
        palavra_chave = palavra_chave[:100]
    
    # Validar entrada
    if not palavra_chave:
        flash("Por favor, preencha 'Palavra-chave'.")
        return redirect(url_for("index"))
    
    if not localizacoes or not any(loc.strip() for loc in localizacoes):
        flash("Por favor, adicione pelo menos uma localização.")
        return redirect(url_for("index"))
    
    # Limitar a 15 localizações
    localizacoes = [loc.strip() for loc in localizacoes if loc.strip()][:15]
    
    # Criar queries para cada localização
    queries = [f"{palavra_chave} in {loc}" for loc in localizacoes]
    
    user_base_dir = os.path.join(STORAGE_ROOT, str(current_user.id), "GMaps Data")
    results = run_scraper(queries, total=total, headless=True, save_base_dir=user_base_dir)

    return render_template("result.html", results=results, query=f"{palavra_chave} em {len(localizacoes)} localizações")
```

### 3.2 Testar Backend
- [ ] Verificar se `request.form.getlist("localizacoes[]")` funciona
- [ ] Testar validação de campos obrigatórios
- [ ] Verificar limite de 15 localizações
- [ ] Testar geração de queries

---

## 📅 FASE 4: Modificação do Scraper (main.py)
**Duração estimada:** 45 minutos  
**Arquivos a modificar:** `main.py`

### 4.1 Adicionar Função de Concatenação
```python
# Adicionar após linha 43 (após a classe BusinessList)
def concatenate_business_lists(business_lists: List[BusinessList]) -> BusinessList:
    """
    Concatena múltiplas BusinessList em uma única, com deduplicação automática
    """
    if not business_lists:
        return BusinessList()
    
    # Usar a primeira BusinessList como base
    result = business_lists[0]
    
    # Adicionar businesses das outras listas
    for business_list in business_lists[1:]:
        for business in business_list.business_list:
            result.add_business(business)  # Deduplicação automática
    
    return result
```

### 4.1.1 Modificar Método `dataframe()` para Adicionar Coluna Status
```python
# Modificar o método dataframe() na classe BusinessList (linha 67-74)
def dataframe(self, add_status_column: bool = False):
    """transform business_list to pandas dataframe

    Args:
        add_status_column: Se True, adiciona coluna 'status' com valor 1

    Returns: pandas dataframe
    """
    df = pd.json_normalize(
        (asdict(business) for business in self.business_list), sep="_"
    )
    
    # Adicionar coluna status se solicitado
    if add_status_column:
        df['status'] = 1
    
    return df
```

### 4.1.2 Adicionar Métodos para Salvar com Coluna Status
```python
# Adicionar novos métodos na classe BusinessList
def save_to_excel_with_status(self, filename):
    """
    Salva dataframe com coluna status adicionada

    Args:
        filename (str): filename
    """
    try:
        df = self.dataframe(add_status_column=True)
        out_path = f"{self.save_at}/{filename}.xlsx"
        # Write with openpyxl engine so we can post-process hyperlinks
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
            try:
                from openpyxl.utils import get_column_letter
                ws = writer.book.active
                if "whatsapp_link" in df.columns:
                    col_idx = df.columns.get_loc("whatsapp_link") + 1  # 1-based
                    col_letter = get_column_letter(col_idx)
                    for row_idx in range(2, len(df) + 1):  # skip header
                        cell = ws[f"{col_letter}{row_idx}"]
                        link = cell.value
                        if link:
                            cell.hyperlink = link
                            cell.style = "Hyperlink"
            except Exception:
                # If anything goes wrong, keep the plain values without hyperlinks
                pass
    except ImportError:
        print("openpyxl not installed; skipping Excel export and continuing with CSV...")
    except Exception as e:
        print(f"Failed to write Excel: {e}; continuing with CSV...")

def save_to_csv_with_status(self, filename):
    """saves pandas dataframe to csv file with status column

    Args:
        filename (str): filename
    """
    self.dataframe(add_status_column=True).to_csv(f"{self.save_at}/{filename}.csv", index=False)
```

### 4.2 Modificar Função `run_scraper`
```python
# Localizar linha 138 e modificar a assinatura:
def run_scraper(
    search_list: List[str],
    total: int,
    headless: bool = True,
    save_base_dir: str | None = None,
    concatenate_results: bool = False,  # Nova opção
) -> List[Dict[str, str]]:
    """Run scraping for one or more searches.

    Args:
        search_list: Lista de queries para buscar
        total: Número máximo de resultados por busca
        headless: Executar browser em modo headless
        save_base_dir: Diretório base para salvar arquivos
        concatenate_results: Se True, concatena todos os resultados em um arquivo único

    Returns a list of dicts with keys: search, csv_path, xlsx_path
    """
    results: List[Dict[str, str]] = []
    all_business_lists: List[BusinessList] = []
    
    with sync_playwright() as p:
        # Browser launch with fallbacks
        try:
            browser = p.chromium.launch(headless=headless)
        except Exception:
            try:
                browser = p.chromium.launch(channel="chrome", headless=headless)
            except Exception:
                browser = p.chromium.launch(channel="msedge", headless=headless)
        page = browser.new_page(locale="en-GB")

        page.goto("https://www.google.com/maps", timeout=20000)

        for search_for_index, search_for in enumerate(search_list):
            print(f"-----\n{search_for_index + 1}/{len(search_list)} - {search_for}".strip())

            page.locator('//input[@id="searchboxinput"]').fill(search_for)
            page.wait_for_timeout(3000)

            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            # scrolling
            page.hover('//a[contains(@href, "https://www.google.com/maps/place")]')

            previously_counted = 0
            while True:
                page.mouse.wheel(0, 10000)
                page.wait_for_timeout(3000)

                if (
                    page.locator(
                        '//a[contains(@href, "https://www.google.com/maps/place")]'
                    ).count()
                    >= total
                ):
                    listings = page.locator(
                        '//a[contains(@href, "https://www.google.com/maps/place")]'
                    ).all()[:total]
                    listings = [listing.locator("xpath=..") for listing in listings]
                    print(f"Total Scraped: {len(listings)}")
                    break
                else:
                    if (
                        page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).count()
                        == previously_counted
                    ):
                        listings = page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).all()
                        print(f"Arrived at all available\nTotal Scraped: {len(listings)}")
                        break
                    else:
                        previously_counted = page.locator(
                            '//a[contains(@href, "https://www.google.com/maps/place")]'
                        ).count()
                        print(
                            f"Currently Scraped: ",
                            page.locator(
                                '//a[contains(@href, "https://www.google.com/maps/place")]'
                            ).count(), end='\r'
                        )

            business_list = BusinessList(
                save_base_dir=save_base_dir or 'GMaps Data'
            )

            # scraping
            for listing in listings:
                try:
                    listing.click()
                    page.wait_for_timeout(2000)

                    name_attribute = 'h1.DUwDvf'
                    address_xpath = '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
                    website_xpath = '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
                    phone_number_xpath = '//button[contains(@data-item-id, "phone:tel:")]//div[contains(@class, "fontBodyMedium")]'
                    review_count_xpath = '//div[@jsaction="pane.reviewChart.moreReviews"]//span'
                    reviews_average_xpath = '//div[@jsaction="pane.reviewChart.moreReviews"]//div[@role="img"]'

                    business = Business()

                    if name_value := page.locator(name_attribute).inner_text():
                        business.name = name_value.strip()
                    else:
                        business.name = ""

                    if page.locator(address_xpath).count() > 0:
                        business.address = page.locator(address_xpath).all()[0].inner_text()
                    else:
                        business.address = ""

                    if page.locator(website_xpath).count() > 0:
                        business.domain = page.locator(website_xpath).all()[0].inner_text()
                        business.website = f"https://www.{page.locator(website_xpath).all()[0].inner_text()}"
                    else:
                        business.website = ""

                    if page.locator(phone_number_xpath).count() > 0:
                        raw_phone = page.locator(phone_number_xpath).all()[0].inner_text()
                        business.phone_number = raw_phone
                        business.whatsapp_link = format_whatsapp_link_br(raw_phone)
                    else:
                        business.phone_number = ""
                        business.whatsapp_link = ""

                    if page.locator(review_count_xpath).count() > 0:
                        business.reviews_count = int(page.locator(review_count_xpath).inner_text().split()[0].replace(',', '').strip())
                    else:
                        business.reviews_count = ""

                    if page.locator(reviews_average_xpath).count() > 0:
                        business.reviews_average = float(page.locator(reviews_average_xpath).get_attribute('aria-label').split()[0].replace(',', '.').strip())
                    else:
                        business.reviews_average = ""

                    business.category = search_for.split(' in ')[0].strip()
                    business.location = search_for.split(' in ')[-1].strip()
                    business.latitude, business.longitude = extract_coordinates_from_url(page.url)

                    business_list.add_business(business)
                except Exception as e:
                    print(f'Error occurred: {e}', end='\r')

            # Armazenar business_list para concatenação posterior
            all_business_lists.append(business_list)
            
            # Se não for para concatenar, salvar individualmente
            if not concatenate_results:
                safe_filename = f"{search_for}".replace(' ', '_')
                business_list.save_to_excel(safe_filename)
                business_list.save_to_csv(safe_filename)
                results.append({
                    "search": search_for,
                    "csv_path": os.path.join(business_list.save_at, f"{safe_filename}.csv"),
                    "xlsx_path": os.path.join(business_list.save_at, f"{safe_filename}.xlsx"),
                })

        browser.close()
    
    # Se for para concatenar, criar arquivo único
    if concatenate_results and all_business_lists:
        concatenated = concatenate_business_lists(all_business_lists)
        
        # Nome do arquivo baseado na primeira busca
        first_search = search_list[0] if search_list else "multiple_locations"
        base_keyword = first_search.split(' in ')[0].strip()
        safe_filename = f"{base_keyword}_múltiplos_bairros"
        
        # Salvar com coluna status adicionada
        concatenated.save_to_excel_with_status(safe_filename)
        concatenated.save_to_csv_with_status(safe_filename)
        
        results.append({
            "search": f"{base_keyword} em {len(search_list)} localizações",
            "csv_path": os.path.join(concatenated.save_at, f"{safe_filename}.csv"),
            "xlsx_path": os.path.join(concatenated.save_at, f"{safe_filename}.xlsx"),
        })
    
    return results
```

### 4.3 Atualizar Chamada no app.py
```python
# Na rota /scrape, modificar a chamada para:
results = run_scraper(queries, total=total, headless=True, save_base_dir=user_base_dir, concatenate_results=True)
```

### 4.4 Testar Scraper
- [x] Testar função `concatenate_business_lists`
- [x] Verificar deduplicação automática
- [x] Testar geração de arquivo único
- [x] Verificar nome do arquivo concatenado

---

## 📅 FASE 5: Testes e Refinamentos
**Duração estimada:** 30 minutos  
**Arquivos a modificar:** Nenhum (apenas testes)

### 5.1 Testes Funcionais
- [x] **Teste 1:** Uma localização (compatibilidade)
- [x] **Teste 2:** Duas localizações
- [x] **Teste 3:** Cinco localizações
- [x] **Teste 4:** Limite de 15 localizações
- [x] **Teste 5:** Deduplicação funcionando

### 5.2 Testes de Validação
- [x] Campos obrigatórios
- [x] Limite de caracteres
- [x] Validação de localizações vazias
- [x] Verificação de licença ativa

### 5.3 Testes de Performance
- [x] Tempo de execução com 3 localizações
- [x] Tempo de execução com 10 localizações
- [x] Verificar se não há timeout
- [ ] Monitorar uso de memória

### 5.4 Ajustes Finais
- [x] Melhorar mensagens de erro
- [x] Ajustar timeouts se necessário
- [x] Verificar logs de debug
- [x] Testar download de arquivos

---

## 📅 FASE 6: Documentação e Deploy
**Duração estimada:** 20 minutos  
**Arquivos a modificar:** `README.md`

### 6.1 Atualizar Documentação
- [x] Documentar nova funcionalidade
- [x] Adicionar exemplos de uso
- [x] Explicar processo de deduplicação
- [x] Documentar limitações

### 6.2 Preparar Deploy
- [x] Testar em ambiente de produção
- [x] Verificar compatibilidade com Docker
- [x] Atualizar requirements.txt se necessário
- [x] Fazer commit das mudanças

### 6.3 Monitoramento
- [ ] Verificar logs de erro
- [ ] Monitorar performance
- [ ] Coletar feedback dos usuários
- [ ] Planejar melhorias futuras

---

## 🚨 Pontos de Atenção

### ⚠️ Limitações Conhecidas
- **Máximo 15 localizações** para evitar timeouts
- **Tempo de execução** proporcional ao número de bairros
- **Rate limiting** do Google Maps pode afetar buscas muito rápidas

### 🔧 Soluções para Problemas Comuns
- **Timeout:** Aumentar pausa entre buscas
- **Rate limiting:** Implementar delays maiores
- **Deduplicação:** Verificar campos únicos no Business.__hash__()

### 📊 Métricas de Sucesso
- **Taxa de sucesso:** >95% das buscas completadas
- **Deduplicação:** <5% de duplicatas no resultado final
- **Performance:** <2 minutos por bairro em média
- **Coluna Status:** 100% das linhas com valor "1" na coluna status

---

## 🎯 Resultado Final Esperado

### Interface:
```
Palavra-chave: [clínica médica                    ]
Localizações:  [Portão, Curitiba                 ] [×]
               [Alto da XV, Curitiba              ] [×]
               [Boqueirão, Curitiba               ] [×]
               [Mercês, Curitiba                   ] [×]
               [Batel, Curitiba                    ] [×]
               [+ Adicionar Localização]
```

### Arquivo de Saída:
- **Nome:** `clínica_médica_múltiplos_bairros_2025-01-XX.csv`
- **Conteúdo:** Todos os resultados concatenados e deduplicados
- **Coluna Status:** Nova coluna "status" com valor "1" em todas as linhas
- **Metadados:** Informação sobre quantas localizações foram buscadas

### Logs de Execução:
```
🔍 Buscando: clínica médica in Portão, Curitiba
✅ 45 resultados encontrados para Portão, Curitiba
🔍 Buscando: clínica médica in Alto da XV, Curitiba  
✅ 38 resultados encontrados para Alto da XV, Curitiba
📊 Total final: 67 resultados únicos (16 duplicatas removidas)
```

---

**💡 Dica:** Execute uma fase por vez, testando completamente antes de prosseguir para a próxima. Isso garante que cada etapa funcione corretamente e facilita a identificação de problemas.
