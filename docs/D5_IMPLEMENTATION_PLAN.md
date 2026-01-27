# Debugging Production Worker & Apify Integration

## Goal
Fix the issue where scraping jobs are not processing in production, and ensure the Apify integration works correctly by validating configuration and improving logging.

## User Review Required
> [!IMPORTANT]
> **Dokploy Configuration**: You MUST add the `APIFY_TOKEN` to your Dokploy Environment Variables for the `leads-infinitos-saas` application (both web and worker services if defined separately, or global). Use the token from your local `.env`.

## Proposed Changes

### Backend (`app.py`)
- [ ] **Add `APIFY_TOKEN` Check**: Before enqueueing a scraping job, verify if the token exists. If not, inform the user immediately via specific error message.
- [ ] **Safe Enqueue**: Add `try/except` block around `q.enqueue` to catch and log Redis connection errors, preventing silent failures or generic 500 errors.

### Worker (`worker_scraper.py`)
- [ ] **Enhanced Logging**: Add explicit print statements at the very beginning of `run_scraper_task` to confirm the function is actually called by the worker. This helps distinguish between "job not queued" vs "job failed silently".

## Verification Plan

### Automated Tests
- None (Production environment debugging).

### Manual Verification
1.  **Configure Dokploy**: Add `APIFY_TOKEN` in Dokploy > Settings > Environment Variables.
2.  **Redeploy**: Rebuild/Restart containers.
3.  **Test Scrape**:
    - Go to UI > Scraper.
    - Enter a keyword.
    - Submit.
    - **Check 1**: Did you get a success message "Scraping enfileirado!"?
    - **Check 2**: Look at Worker Logs in Dokploy. You should see:
        - `INFO:worker:Módulo worker_scraper importado com sucesso.`
        - `Starting job <job_id>` (This is the new log we added).
        - `Executing Apify Actor...`
4.  **Failure Case**: If `APIFY_TOKEN` is removed, the UI should now show "Erro de Configuração: APIFY_TOKEN não encontrado" instead of failing silently or later in the worker.
