# Leads Infinitos - Google Maps Scraper SaaS  

A powerful, multi-location web scraper built with **Playwright** to extract business listings from Google Maps. Features user authentication, license management, and intelligent data concatenation across multiple locations.

**Features:**
- 🔐 **User Authentication** - Secure login system with license management
- 🌍 **Multiple Locations** - Scrape across multiple cities/regions simultaneously  
- 📊 **Smart Concatenation** - Automatic deduplication and data merging
- 📈 **Status Column** - Ready-to-import data with status indicators
- 💼 **SaaS Ready** - Production-ready with user isolation and file management

**Note:** This project is for **educational purposes only**. Always respect Google's Terms of Service and scraping policies.  

---

## 📂 Output Examples  

### Single Location Mode
The data is saved in a folder named `GMaps Data` in a folder of the date the script was executed.
Check the generated files to understand the data structure:  
- **`niche in place.csv`**  
- **`niche in place.xlsx`**  

### Multiple Locations Mode (NEW!)
When scraping multiple locations, the system automatically:
- **Concatenates all results** into a single file
- **Removes duplicates** across different locations  
- **Adds status column** (value: 1) for CRM import
- **Generates unified filename**: `{keyword}_múltiplos_bairros.xlsx`

**Example:** Scraping "veterinária" in "Curitiba", "São Paulo", "Rio de Janeiro" → `veterinária_múltiplos_bairros.xlsx`

Each entry includes:  
- Business name  
- Rating (avg. and count)  
- Contact info (phone, website, WhatsApp links)  
- Address & location details  
- **Status column** (for CRM import)
- Additional metadata (reviews, features, etc.)  

---

## ⚙️ Installation  

### 1. Set Up a Virtual Environment (Recommended)  
```bash
virtualenv venv  
source venv/bin/activate  # Linux/Mac  
venv\Scripts\activate     # Windows  
```  

### 2. Install Dependencies  
```bash
pip install -r requirements.txt  
playwright install chromium  # Headless browser for scraping  
```  

---

## 🚀 How to Run  

### Option 0: Web App (com autenticação)  
1. Defina variáveis (opcional):
   - `FLASK_SECRET_KEY` (recomendado em prod)
   - `STORAGE_DIR` (ex.: `storage` ou `/data/storage` em prod)
2. Inicie o app:
   ```bash
   python app.py
   ```  
3. Acesse `http://localhost:8000` → primeiro registre-se, depois faça login e use a tela de extração.

Os arquivos gerados são salvos em `storage/<userId>/<YYYY-MM-DD>/...` e os downloads são protegidos por usuário.

### Deploy com Dokploy (Dockerfile)
1. Faça push deste repositório.
2. No Dokploy: New App → Dockerfile → selecione este repo.
3. Porta: 8000. Healthcheck: `/healthz`.
4. Variáveis de ambiente: `FLASK_SECRET_KEY`, `STORAGE_DIR=/app/storage`.
5. Volumes: monte volumes persistentes em `/app/app.db` (SQLite) e `/app/storage` (arquivos por usuário).
6. Opcional: ajuste workers Gunicorn via `WEB_CONCURRENCY`.

---

## 🛠️ Admin scripts (Python)

### Criar/atualizar usuário (`create_user.py`)

Criar usuário simples:
```bash
python create_user.py --email "cliente@exemplo.com" --password "senha123456"
```

Criar usuário e licença anual:
```bash
python create_user.py --email "cliente@exemplo.com" --password "senha123456" --create-license --license-type anual
```

Criar usuário e licença "vitalícia" (anual com expiração de 50 anos):
```bash
python create_user.py --email "cliente@exemplo.com" --password "senha123456" --create-license --lifetime
```

Atualizar senha de usuário existente:
```bash
python create_user.py --email "cliente@exemplo.com" --password "novaSenha" --update-password
```

### Criar licenças anuais para todos os usuários (`create_annual_licenses.py`)

Apenas para quem ainda não tem nenhuma licença:
```bash
python create_annual_licenses.py --yes
```

Forçar criação para todos (mesmo que já tenham licença):
```bash
python create_annual_licenses.py --yes --force
```

Personalizar dias até expiração (padrão: 365):
```bash
python create_annual_licenses.py --yes --expires-days 365
```

### Outros utilitários

Migração do schema (primeiro uso / cada deploy):
```bash
python scripts/run_migrate_db.py
```
Ver também `docs/DEPLOY_ORDER.md` (Compose: serviço `migrate` antes do web/workers).

Listar usuários e licenças:
```bash
python list_all_users.py
```

### Option 1: Single Search  
```bash
python3 main.py -s="<query>" -t=<result_count>  
```  
**Example:**  
```bash
python3 main.py -s="coffee shops in Seattle" -t=50  
```  

### Option 2: Batch Searches (via `input.txt`)  
1. Add queries to **`input.txt`** (one per line):  
   ```text
   dentists in Boston, MA  
   plumbers in Austin, TX  
   ```  
2. Run the scraper:  
   ```bash
   python3 main.py -t=30  # Optional: Limit results per query  
   ```  

---

## 💡 Pro Tips  

### Maximizing Results  
Google Maps limits visible results (~120 per search). To bypass this:  
- **Use granular queries** (e.g., split "US dentists" into city/state-level searches).  
- **Combine keywords** (e.g., `"emergency dentist Chicago 24/7"`).  

### Customization  
- Adjust **`main.py`** to scrape additional fields (e.g., hours, pricing).  
- Modify **`playwright`** settings in `scraper.py` to change timeouts or headless mode.  

---

## ❓ Troubleshooting  
- **Slow scraping?** Add delays between requests (edit `scraper.py`).  
- **Missing data?** Google may block frequent requests—try proxies or reduce speed.  

--- 

**Happy scraping!** 🛠️