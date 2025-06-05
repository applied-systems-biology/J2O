import os
import logging
from celery import Celery
from omero.config import ConfigXml
import json

# Locate the grid config.xml from your OMERO installation
cfg_file = os.path.join(os.environ["OMERODIR"], "etc", "grid", "config.xml")
cfg = ConfigXml(cfg_file, read_only=True)
raw = cfg.as_map().get("omero.web.caches")
caches = json.loads(raw)
redis_backend = caches["default"]["LOCATION"]

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JIPipePlugin.settings')

app = Celery('JIPipePlugin', broker=redis_backend, backend=redis_backend)
# run on omero-web: celery -A JIPipePlugin worker --loglevel=info -E

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)

print(f"Using broker: {app.conf.broker_url}")
print(f"Using backend: {app.conf.result_backend}")


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')