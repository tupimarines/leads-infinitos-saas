import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

import json
from datetime import datetime
from main import run_scraper_with_progress

load_dotenv()

# Configuração
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
STORAGE_ROOT = os.environ.get("STORAGE_DIR", "storage")

def get_db_connection():
    conn = psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'leads_infinitos'),
        user=os.environ.get('DB_USER', 'postgres'),
        password=os.environ.get('DB_PASSWORD', 'devpassword'),
        port=os.environ.get('DB_PORT', '5432')
    )
    return conn

def update_job_status(job_id, status, progress=None, current_location=None, results_path=None, error_message=None, lead_count=None):
    """Updates job status in the database safely from the worker"""
    conn = get_db_connection()
    try:
        update_fields = ["status = %s"]
        params = [status]
        
        if progress is not None:
            update_fields.append("progress = %s")
            params.append(progress)
            
        if current_location is not None:
            update_fields.append("current_location = %s")
            params.append(current_location)
            
        if results_path is not None:
            update_fields.append("results_path = %s")
            params.append(results_path)
            
        if error_message is not None:
            update_fields.append("error_message = %s")
            params.append(error_message)
        
        if lead_count is not None:
            update_fields.append("lead_count = %s")
            params.append(lead_count)

        if status == 'running':
             pass 
        
        if status in ['completed', 'failed']:
            update_fields.append("completed_at = %s")
            params.append(datetime.now().isoformat())

        params.append(job_id)

        sql = f"UPDATE scraping_jobs SET {', '.join(update_fields)} WHERE id = %s"
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
        conn.commit()
    except Exception as e:
        print(f"Error updating DB for job {job_id}: {e}")
    finally:
        conn.close()

def run_scraper_task(job_id: int):
    """
    Função executada pelo RQ Worker.
    Substitui a antiga 'run_scraping_job' baseada em threads.
    """
    print(f"🚀 [WORKER] Received task for Job {job_id}")
    print(f"Start job {job_id}")
    
    # 1. Ler Job do Banco
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM scraping_jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
    conn.close()
    
    if not job:
        print(f"Job {job_id} not found in DB")
        return

    try:
        # 2. Atualizar Status -> Running
        update_job_status(job_id, 'running', progress=0)

        # 3. Parse Data
        locations = json.loads(job['locations'])
        keyword = job['keyword']
        total_results = job['total_results']
        user_id = job['user_id']
        
        queries = [f"{keyword} in {loc}" for loc in locations]
        
        # 3.1. Ensure STORAGE_ROOT exists (fix for FileNotFoundError)
        os.makedirs(STORAGE_ROOT, exist_ok=True)
        
        user_base_dir = os.path.join(STORAGE_ROOT, str(user_id), "GMaps Data")
        
        # 4. Run Scraper
        # Definindo callback para atualizar progresso no BD
        def progress_callback(prog, loc):
            # Otimização: pode-se limitar updates ao BD (ex: a cada 5%)
            update_job_status(job_id, 'running', progress=prog, current_location=loc)

        results = run_scraper_with_progress(
            queries, 
            total=total_results, 
            headless=True, 
            save_base_dir=user_base_dir, 
            concatenate_results=True,
            progress_callback=progress_callback
        )
        
        # 5. Conclusão
        # 5. Conclusão
        if results and len(results) > 0:
            import pandas as pd
            
            # --- DEDUPLICATION AND MERGE LOGIC ---
            final_df = pd.DataFrame()
            
            # 1. Load all result CSVs
            dfs = []
            for res in results:
                csv_path = res.get('csv_path')
                if csv_path and os.path.exists(csv_path):
                    try:
                        # Assuming Main.py saves as string to match existing logic
                        df_part = pd.read_csv(csv_path, dtype=str)
                        dfs.append(df_part)
                    except Exception as e:
                        print(f"Error loading partial CSV {csv_path}: {e}")
            
            if dfs:
                final_df = pd.concat(dfs, ignore_index=True)
                
                # 2. Normalize columns (lowercase for consistent check)
                # But we want to keep original names for export. 
                # Let's create a map or just work with what we have.
                # Standard scraper columns: 'name', 'phone_number', 'address', etc.
                
                # 3. Deduplicate
                # Priority: Phone Number, then Name+Address
                
                # Create a temporary normalized phone column for deduplication if 'phone_number' exists
                if 'phone_number' in final_df.columns:
                     final_df['__norm_phone'] = final_df['phone_number'].astype(str).str.replace(r'\D', '', regex=True)
                     # Treat empty phones as unique or null? 
                     # If phone is empty, we fall back to name+address.
                     # Let's split into two groups: with phone and without phone.
                     
                     # Actually, simpler: drop_duplicates by phone if valid, then remaining by name.
                     
                     # Separate rows with valid phone (>8 digits)
                     mask_valid_phone = final_df['__norm_phone'].str.len() >= 8
                     df_phones = final_df[mask_valid_phone].copy()
                     df_others = final_df[~mask_valid_phone].copy()
                     
                     # Dedupe phones
                     df_phones = df_phones.drop_duplicates(subset=['__norm_phone'], keep='first')
                     
                     # Dedupe others by name + address (if available)
                     subset_cols = ['name', 'address']
                     # Filter only existing columns
                     subset_cols = [c for c in subset_cols if c in df_others.columns]
                     
                     if subset_cols:
                         df_others = df_others.drop_duplicates(subset=subset_cols, keep='first')
                     
                     # Combine back
                     final_df = pd.concat([df_phones, df_others], ignore_index=True)
                     
                     # Drop helper
                     final_df.drop(columns=['__norm_phone'], inplace=True)
                
                else:
                     # Fallback if no phone column: dedupe by name (and address if exists)
                     subset_cols = ['name']
                     if 'address' in final_df.columns:
                         subset_cols.append('address')
                     final_df = final_df.drop_duplicates(subset=subset_cols, keep='first')
                
                # 4. Save Merged CSV
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                # Use same dir as first result
                first_dir = os.path.dirname(results[0]['csv_path'])
                merged_filename = f"Merged_Job_{job_id}_{timestamp}.csv"
                final_path = os.path.join(first_dir, merged_filename)
                
                final_df.to_csv(final_path, index=False)
                print(f"✅ Merged {len(results)} files into {final_path}. Total uniques: {len(final_df)}")
                
                lead_count = len(final_df)
                
                # Update DB
                update_job_status(job_id, 'completed', progress=100, results_path=final_path, lead_count=lead_count)
            else:
                 # No valid CSVs loaded
                 final_path = results[0].get('csv_path', '') # Fallback to first even if empty?
                 lead_count = 0
                 update_job_status(job_id, 'completed', progress=100, results_path=final_path, lead_count=lead_count)

            # Registrar no histórico imutável (anti-bypass de limite)
            if lead_count > 0:
                try:
                    from datetime import datetime, timedelta
                    
                    # Buscar licença do usuário para calcular ciclo
                    conn = get_db_connection()
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT purchase_date FROM licenses 
                            WHERE user_id = %s AND status = 'active'
                            ORDER BY created_at DESC LIMIT 1
                        """, (user_id,))
                        license_row = cur.fetchone()
                        
                        if license_row:
                            purchase_date = license_row[0]
                            
                            # Converter para datetime se for string
                            if isinstance(purchase_date, str):
                                purchase_date = datetime.fromisoformat(purchase_date.replace('Z', '+00:00'))
                            
                            # Remover timezone
                            if hasattr(purchase_date, 'tzinfo') and purchase_date.tzinfo is not None:
                                purchase_date = purchase_date.replace(tzinfo=None)
                            
                            # Calcular ciclo atual
                            today = datetime.now()
                            days_since_purchase = (today - purchase_date).days
                            months_elapsed = days_since_purchase // 30
                            
                            cycle_start = purchase_date + timedelta(days=30 * months_elapsed)
                            cycle_end = cycle_start + timedelta(days=30)
                            
                            # Inserir no histórico imutável
                            cur.execute("""
                                INSERT INTO monthly_usage_history 
                                (user_id, cycle_start, cycle_end, leads_extracted, job_id)
                                VALUES (%s, %s, %s, %s, %s)
                            """, (user_id, cycle_start.date(), cycle_end.date(), lead_count, job_id))
                            conn.commit()
                            print(f"✅ Registered {lead_count} leads in monthly usage history (cycle {cycle_start.date()} to {cycle_end.date()})")
                        else:
                            print(f"⚠️ No active license found for user {user_id}, skipping usage history")
                    
                    conn.close()
                except Exception as e:
                    print(f"⚠️ Error recording monthly usage history: {e}")
        else:
            update_job_status(job_id, 'failed', error_message="Nenhum resultado encontrado.")

    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        update_job_status(job_id, 'failed', error_message=str(e))

if __name__ == '__main__':
    # Script para iniciar o worker: python worker_scraper.py
    listen = ['default']
    conn = redis.from_url(REDIS_URL)

    with Connection(conn):
        worker = Worker(list(map(Queue, listen)))
        worker.work()
