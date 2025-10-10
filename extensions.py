# /app/extensions.py

from celery import Celery

# Create the Celery instance. It will be configured by the app factory.
celery = Celery(__name__, config_source="celery_config")
