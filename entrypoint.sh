#!/bin/bash
set -e

# Get LOCATION from env (injected from OMERO config externally)
BROKER_URL=$(echo "$OMERO_CONFIG_CACHES" | jq -r '.default.LOCATION')

# Patch localhost/127.0.0.1 if needed
if [[ "$BROKER_URL" == *"127.0.0.1"* || "$BROKER_URL" == *"localhost"* ]]; then
    if getent hosts host.docker.internal > /dev/null; then
        HOST_REDIS="host.docker.internal"
    else
        HOST_REDIS="172.17.0.1"
    fi
    BROKER_URL=$(echo "$BROKER_URL" | sed "s/127\.0\.0\.1/$HOST_REDIS/" | sed "s/localhost/$HOST_REDIS/")
fi

echo "🔌 Using Redis at: $BROKER_URL"

# Patch the JSON with the new LOCATION
PATCHED_JSON=$(echo "$OMERO_CONFIG_CACHES" | jq --arg newloc "$BROKER_URL" '.default.LOCATION = $newloc')
export OMERO_CONFIG_CACHES="$PATCHED_JSON"

# Start Celery
exec /opt/omero/web/venv3/bin/celery -A JIPipePlugin worker --loglevel=info -E
