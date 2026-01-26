from celery import Celery
import json
import os

# Configure Celery (match app.py config)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

celery = Celery('app', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

def inspect_tasks():
    inspector = celery.control.inspect()
    active = inspector.active() or {}
    print(json.dumps(active, indent=2, default=str))

if __name__ == "__main__":
    inspect_tasks()
