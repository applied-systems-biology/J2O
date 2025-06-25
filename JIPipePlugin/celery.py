import os
import logging
from celery import Celery
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JIPipePlugin.settings')

try:
    cache_location = settings.CACHES["default"]["LOCATION"]
except Exception as e:
    raise RuntimeError("omero.web.caches not correctly defined!")

app = Celery('JIPipePlugin', broker=cache_location, backend=cache_location)
# run on omero-web: celery -A JIPipePlugin worker --loglevel=info -E

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')