import os
import logging
from celery import Celery
from django.conf import settings
import json

logger = logging.getLogger(__name__)

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'JIPipePlugin.settings')

try:
    cache_settings_raw = os.getenv("OMERO_CONFIG_CACHES")
    
    if cache_settings_raw is None:
        raise ValueError("Environment variable OMERO_CONFIG_CACHES is not set.")
    
    try:
        cache_settings = json.loads(cache_settings_raw)
    except json.JSONDecodeError:
        raise ValueError("OMERO_CONFIG_CACHES does not contain valid JSON.")

    # Now check for required fields
    if "default" not in cache_settings:
        raise ValueError("OMERO_CONFIG_CACHES is missing 'default' cache config.")

    default_cache = cache_settings["default"]
    default_cache_location = default_cache.get("LOCATION")
    default_cache_backend = default_cache.get("BACKEND")

    if not default_cache_location or not default_cache_backend:
        raise ValueError("Default cache config must include both 'LOCATION' and 'BACKEND'.")

    if "redis" not in default_cache_location or "redis" not in default_cache_backend:
        raise ValueError("Default cache location and/or backend is not redis! "
                         "Check that OMERO_CONFIG_CACHES is set correctly and omero.web.caches is using redis.")

except ValueError as error:
    logger.error(f"Cache configuration error: {error}")
    raise

app = Celery('JIPipePlugin', broker=default_cache_location, backend=default_cache_location)
# run on omero-web: celery -A JIPipePlugin worker --loglevel=info -E

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')