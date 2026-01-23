import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

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

def update_job_status(job_id, status, progress=None, current_location=None, results_path=None, error_message=None):
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
    print(f"Starting job {job_id}")
    
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
        if results and len(results) > 0:
            final_path = results[0].get('csv_path', '')
            update_job_status(job_id, 'completed', progress=100, results_path=final_path)
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
