import os
import redis
from rq import Worker, Queue, Connection
from dotenv import load_dotenv
import logging
from main import run_scraper  # Import run_scraper directly to monkeypatch it if needed, or rely on worker_scraper imports

# Force headless=False in main.py logic by setting env var or monkeypatching
# Since main.py uses headless=headless argument, we can just run the worker and hopes the task passes headless=False
# BUT the task is enqueued with arguments. The job arguments are fixed when enqueued.
# If the user enqueues from the UI, it sends headless=True (default).

# Load environment first
load_dotenv()

# Override DB config to localhost if running outside docker but accessing docker services (mapped ports)
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_PORT'] = '5432'
os.environ['DB_USER'] = 'postgres'
os.environ['DB_PASSWORD'] = 'devpassword'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'

# Strategy: Monkeypatch run_scraper_task in worker_scraper to force headless=False
import worker_scraper
import main

# Original function
original_run_scraper_with_progress = main.run_scraper_with_progress

def patched_run_scraper_with_progress(*args, **kwargs):
    print(">>> FORCING HEADLESS = FALSE FOR DEBUGGING <<<")
    kwargs['headless'] = False
    return original_run_scraper_with_progress(*args, **kwargs)

# Apply patch
main.run_scraper_with_progress = patched_run_scraper_with_progress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker-local")

def start_local_worker():
    print("Iniciando Worker Local com Browser Visível...")
    print("Certifique-se que o Docker está rodando com as portas 5432 e 6379 expostas.")
    
    redis_url = os.environ.get('REDIS_URL')
    conn = redis.from_url(redis_url)
    
    listen = ['default']
    with Connection(conn):
        worker = Worker(list(map(Queue, listen)))
        logger.info(f"Worker Local iniciado presencialmente.")
        worker.work()

if __name__ == '__main__':
    start_local_worker()
