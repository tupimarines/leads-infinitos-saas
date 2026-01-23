import os
import redis
from rq import Worker, Queue, Connection
from dotenv import load_dotenv
import logging

# Carregar variáveis de ambiente
load_dotenv()

# Configuração de Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

# Importar módulos de tarefas para garantir que estejam disponíveis
# Isso é crucial para que o RQ encontre as funções
try:
    import main  # Contém tarefas de scraping
    logger.info("Módulo main importado com sucesso.")
except ImportError as e:
    logger.warning(f"Não foi possível importar main: {e}")

try:
    import worker_email  # Contém tarefas de email
    logger.info("Módulo worker_email importado com sucesso.")
except ImportError as e:
    logger.warning(f"Não foi possível importar worker_email: {e}")

try:
    import worker_scraper  # Contém tarefas de scraping (wrapper)
    logger.info("Módulo worker_scraper importado com sucesso.")
except ImportError as e:
    logger.warning(f"Não foi possível importar worker_scraper: {e}")


# Configuração do Redis
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
LISTEN = ['default']

def start_worker():
    conn = redis.from_url(REDIS_URL)
    with Connection(conn):
        worker = Worker(list(map(Queue, LISTEN)))
        logger.info(f"Worker iniciado, escutando filas: {LISTEN}")
        worker.work()

if __name__ == '__main__':
    start_worker()
