import os
import redis
from rq import Queue
import sys

# Load env vars same as app
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

print(f"🔍 Diagnosing Redis Connection to: {REDIS_URL}")

try:
    conn = redis.from_url(REDIS_URL)
    conn.ping()
    print("✅ Redis Connected Successfully!")
except Exception as e:
    print(f"❌ Redis Connection Failed: {e}")
    sys.exit(1)

# Inspect Queue
q = Queue(name='default', connection=conn)
print(f"📊 Queue 'default' Size: {len(q)}")

print("\n📋 Jobs in Queue:")
jobs = q.get_jobs()
if not jobs:
    print("   (Queue is empty)")
else:
    for job in jobs:
        print(f"   - Job ID: {job.id}")
        print(f"     Function: {job.func_name}")
        print(f"     Origin: {job.origin}")
        print(f"     Status: {job.get_status()}")
        print(f"     Enqueued At: {job.enqueued_at}")
        print("-" * 30)

print("\n👀 Worker Registry:")
from rq.registry import StartedJobRegistry
registry = StartedJobRegistry(queue=q)
running_jobs = registry.get_job_ids()
print(f"   Running Jobs: {running_jobs}")

# List Workers
from rq import Worker
workers = Worker.all(connection=conn)
print(f"\n👷 Workers Found: {len(workers)}")
for w in workers:
    print(f"   - Name: {w.name}")
    print(f"     Queues: {w.queues}")
    print(f"     State: {w.state}")
    print(f"     Last Heartbeat: {w.last_heartbeat}")
