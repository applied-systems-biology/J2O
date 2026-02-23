import os
import logging
from celery import Celery
from django.conf import settings
from pathlib import Path

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "omeroweb.settings")

# Get the redis cache location from the omero.web settings
cache_location = settings.CACHES["default"]["LOCATION"]

app = Celery(
    "JIPipePlugin",
    broker=cache_location,
    backend=cache_location,
    include=["J2O.tasks"]
)
# run on omero-web: celery -A JIPipePlugin worker --loglevel=info -E

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')