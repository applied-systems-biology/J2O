import os
import logging
from celery import Celery
from omero.config import ConfigXml
import json

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JIPipePlugin.settings')

try:
    cfg_file = os.path.join(os.environ["OMERODIR"], "etc", "grid", "config.xml")
    cfg = ConfigXml(cfg_file, read_only=True)
    raw = cfg.as_map().get("omero.web.caches")

    if raw:
        CACHES = json.loads(raw)
    else:
        raise RuntimeError("omero.web.caches not found in config.xml")

except Exception as e:
    raise RuntimeError(f"Failed to load OMERO cache config: {e}")

try:
    cache_location = CACHES["default"]["LOCATION"]
except Exception as e:
    raise RuntimeError("omero.web.caches not correctly defined!")

app = Celery(
    "JIPipePlugin",
    broker=cache_location,
    backend=cache_location,
    include=["JIPipeRunner.tasks"]
)
# run on omero-web: celery -A JIPipePlugin worker --loglevel=info -E

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

logger = logging.getLogger(__name__)

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')