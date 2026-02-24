#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Please run this script as root or with sudo"
    exit 1
fi

check_redis_connection() {
    local redis_uri="$1"

    # Extract host and port from URI (assumes format: redis://host:port[/db])
    local host port
    host=$(echo "$redis_uri" | sed -E 's#^redis://([^:/]+).*#\1#')
    port=$(echo "$redis_uri" | sed -E 's#^redis://[^:/]+:([0-9]+).*#\1#')

    if [[ -z "$host" || -z "$port" ]]; then
        echo "Invalid redis URI format: $redis_uri"
        return 1
    fi

    echo "Checking redis at $host:$port..."

    # Check if nc is available
    if command -v nc &> /dev/null; then
        if nc -z -w2 "$host" "$port"; then
            echo "redis is reachable at $host:$port"
            return 0
        else
            echo "redis not reachable at $host:$port"
            return 1
        fi
    elif command -v redis-cli &> /dev/null; then
        if redis-cli -h "$host" -p "$port" ping | grep -q PONG; then
            echo "redis responded at $host:$port"
            return 0
        else
            echo "redis did not respond at $host:$port"
            return 1
        fi
    else
        echo "Neither 'nc' nor 'redis-cli' available to check Redis"
        return 2
    fi
}

# === SET ENV VAR FOR SCRIPT ===
echo "Setting env variables..."
CURRENT_DIR=$(pwd)
read -p "Are you using the default directory names and omero-web local system user for OMERO.web? [y/n] " DEFAULT_ANSWER

while [[ "$DEFAULT_ANSWER" != "n" && "$DEFAULT_ANSWER" != "y" ]]; do
    echo "Invalid answer, only y or n are accepted answers. Try again!"
    read -p "Are you using the default directory names and omero-web local system user for OMERO.web? [y/n] " DEFAULT_ANSWER
done

if [ "$DEFAULT_ANSWER" = "n" ]; then
    read -p "Enter the system username used to run OMERO (default should be omero-web): " OMERO_USER
    read -p "Enter the path to omero bin (default should be /opt/omero/web/venv3/bin): " OMERO_BIN_PATH
    read -p "Enter the OMERODIR path (default should be /opt/omero/web/omero-web): " OMERODIR
else
    OMERO_USER="omero-web"
    OMERO_BIN_PATH="/opt/omero/web/venv3/bin"
    OMERODIR="/opt/omero/web/omero-web"
fi

sudo chown -R "$OMERO_USER":"$OMERO_USER" "$CURRENT_DIR"

# === INSTALL PYTHON REQUIREMENTS ===
echo "Installing python requirements..."
"$OMERO_BIN_PATH/pip" install -r requirements.txt

run_as_omero-web() {
    sudo -u "$OMERO_USER" env PATH="$OMERO_BIN_PATH:$PATH" OMERODIR="$OMERODIR" "$@"
}

if ! run_as_omero-web omero -h &> /dev/null; then
    echo "Error: 'omero' CLI not usable for user $OMERO_USER"
    exit 1
fi

if ! run_as_omero-web pip -h &> /dev/null; then
    echo "Error: pip does not exist or is not executable"
    exit 1
fi

# === CHECK FOR PODMAN INSTALLATION ===
echo "Checking if podman is installed and usable by user '$OMERO_USER'..."
# Check if podman is installed
if ! command -v podman &> /dev/null; then
    echo "Podman is not installed. Please install podman before running this script."
    exit 1
fi

# Check if OMERO_USER can run podman directly
if sudo -u "$OMERO_USER" podman ps > /dev/null 2>&1; then
    echo "podman is installed and usable by $OMERO_USER (without sudo)."
elif sudo -u "$OMERO_USER" sudo -n podman ps > /dev/null 2>&1; then
    echo "podman requires sudo for $OMERO_USER, but passwordless sudo is available."
else
    echo "User '$OMERO_USER' cannot execute podman commands."
    echo "As root, adding the user to the 'podman' group can help:"
    echo "    sudo usermod -aG podman $OMERO_USER"
    echo "    Then log out and back in."
    exit 1
fi

# === ENABLE PODMAN USER SOCKET IF NOT ALREADY ENABLED ===
echo "Ensuring podman user socket is enabled for $OMERO_USER..."

uid=$(id -u "$OMERO_USER")
xr="/run/user/$uid"
sock="$xr/podman/podman.sock"

run_user_systemctl() {
  sudo -u "$OMERO_USER" env \
    XDG_RUNTIME_DIR="$xr" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$xr/bus" \
    systemctl --user "$@"
}

# Make sure user manager exists
loginctl enable-linger "$OMERO_USER" >/dev/null 2>&1 || true
systemctl start "user@${uid}.service" >/dev/null 2>&1 || true

# Enable/start socket if needed
if run_user_systemctl is-enabled podman.socket >/dev/null 2>&1; then
  echo "podman.socket is already enabled for $OMERO_USER."
else
  echo "Enabling podman.socket for $OMERO_USER..."
  run_user_systemctl enable --now podman.socket
fi

# --- Health check: can we connect to the UDS? ---
podman_socket_healthy() {
  sudo -u "$OMERO_USER" python3 - <<PY
import socket, sys
p="$sock"
s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect(p)
except Exception:
    sys.exit(1)
else:
    sys.exit(0)
PY
}

if podman_socket_healthy; then
  echo "Podman socket is connectable."
else
  echo "Podman socket not connectable (stale listener). Restarting socket..."
  run_user_systemctl stop podman.socket || true
  sudo -u "$OMERO_USER" rm -f "$sock" || true
  run_user_systemctl start podman.socket

  if podman_socket_healthy; then
    echo "Podman socket recovered and is connectable."
  else
    echo "ERROR: Podman socket still not connectable after restart."
    run_user_systemctl status podman.socket podman.service --no-pager || true
    exit 1
  fi
fi

# === INSTALL J2O ===
echo "Installing OMERO plugin..."
if "$OMERO_BIN_PATH/pip" show "J2O" > /dev/null 2>&1; then
    read -p "Plugin 'J2O' is already installed. Do you want to reinstall it? [y/n] " REINSTALL_ANSWER

    while [[ "$REINSTALL_ANSWER" != "n" && "$REINSTALL_ANSWER" != "y" ]]; do
        echo "Invalid answer, only y or n are accepted answers. Try again!"
        read -p "Plugin 'J2O' is already installed. Do you want to reinstall it? [y/n] " REINSTALL_ANSWER
    done

    if [ "$REINSTALL_ANSWER" = "y" ]; then
        "$OMERO_BIN_PATH/pip" install --upgrade --force-reinstall "$CURRENT_DIR"
    fi
else
    "$OMERO_BIN_PATH/pip" install "$CURRENT_DIR"
fi

# === CONFIGURE OMERO WEB ===
echo "Configuring omero config..."
APP_ENTRY='"J2O"'
PLUGIN_ENTRY='["J2O", "J2O/right_plugin_example.js.html", "jipipe_form_container"]'

CURRENT_APPS=$(run_as_omero-web omero config get omero.web.apps 2>/dev/null || echo "")

echo "Checking omero.web.apps..."
if echo "$CURRENT_APPS" | grep -Fq "$APP_ENTRY"; then
    echo "$APP_ENTRY already present in omero.web.apps"
else
    echo "Appending $APP_ENTRY to omero.web.apps"
    run_as_omero-web omero config append omero.web.apps "$APP_ENTRY"
fi

echo "Checking omero.web.ui.right_plugins..."
CURRENT_PLUGINS=$(run_as_omero-web omero config get omero.web.ui.right_plugins 2>/dev/null || echo "[]")

if CURRENT_PLUGINS="$CURRENT_PLUGINS" PLUGIN_ENTRY="$PLUGIN_ENTRY" python3 -c '
import json, os, sys
current = json.loads(os.environ["CURRENT_PLUGINS"])
target = json.loads(os.environ["PLUGIN_ENTRY"])
if target in current:
    sys.exit(0)
sys.exit(1)
'; then
    echo "$PLUGIN_ENTRY already present in omero.web.ui.right_plugins"
else
    echo "Appending $PLUGIN_ENTRY to omero.web.ui.right_plugins"
    run_as_omero-web omero config append omero.web.ui.right_plugins "$PLUGIN_ENTRY"
fi

echo "Checking redis..."
CURRENT_CACHES=$(run_as_omero-web omero config get omero.web.caches 2>/dev/null || echo "")

if CURRENT_CACHES="$CURRENT_CACHES" python3 -c '
import json, os, sys
try:
    raw = os.environ.get("CURRENT_CACHES", "").strip()
    current = json.loads(raw) if raw else {}
    backend = current.get("default", {}).get("BACKEND", "")
    if backend == "django_redis.cache.RedisCache":
        sys.exit(0)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
'; then
    echo "redis already setup as default backend!"
else
    read -p "redis is not set as default cache backend. Do you want to set it? [y/n] " REDIS_ANSWER
    while [[ "$REDIS_ANSWER" != "n" && "$REDIS_ANSWER" != "y" ]]; do
        echo "Invalid answer, only y or n are accepted answers. Try again!"
        read -p "redis is not set as default cache backend. Do you want to set it? [y/n] " REDIS_ANSWER
    done
    if [ "$REDIS_ANSWER" = "y" ]; then
        echo "Setting redis as default backend..."
        read -p "Enter Redis location (e.g., redis://127.0.0.1:6379/0): " REDIS_LOCATION
        CACHE_JSON=$(printf '{"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "%s"}}' "$REDIS_LOCATION")
        run_as_omero-web omero config set omero.web.caches "$CACHE_JSON"
    fi
fi

# === Extract redis URI from config JSON ===
UPDATED_CACHES=$(run_as_omero-web omero config get omero.web.caches 2>/dev/null || echo "")
REDIS_LOCATION=$(python3 -c "
import json, sys
try:
    config = json.loads('''$UPDATED_CACHES''')
    print(config['default']['LOCATION'])
except Exception:
    sys.exit(1)
")

# === Check wether redis is running on the given location ===
echo "Checking wether redis service is running..."
if check_redis_connection "$REDIS_LOCATION"; then
    echo "redis is running!"
else
    echo "Cannot connect to redis — please check the address or start the service."
    exit 1
fi

# === CHECK CELERY ===
echo "Checking if Celery worker is running for app JIPipePlugin..."

# Define Celery start command with proper environment
# The --detach flag spawns a new process that needs all env vars explicitly set
start_celery_worker() {
    local log_file="$CURRENT_DIR/celery_worker.log"
    
    # Use sudo to run as omero-web with full environment
    # Use bash -c with cd to set working directory for the detached process
    sudo -u "$OMERO_USER" env \
        PATH="$OMERO_BIN_PATH:$PATH" \
        OMERODIR="$OMERODIR" \
        DJANGO_SETTINGS_MODULE="omeroweb.settings" \
        PYTHONPATH="$CURRENT_DIR:$OMERODIR" \
        bash -c "cd $CURRENT_DIR && celery -A JIPipePlugin worker --loglevel=info -E --detach --pidfile=$CURRENT_DIR/celery_worker.pid --logfile=$log_file"
}

if pgrep -f "celery.*-A JIPipePlugin.*worker" > /dev/null; then
    echo "Celery worker is already running."
    read -p "Do you want to restart the Celery worker to apply changes? [y/n] " CELERY_RESTART_ANSWER
    while [[ "$CELERY_RESTART_ANSWER" != "n" && "$CELERY_RESTART_ANSWER" != "y" ]]; do
        echo "Invalid answer, only y or n are accepted answers. Try again!"
        read -p "Do you want to restart the Celery worker to apply changes? [y/n] " CELERY_RESTART_ANSWER
    done
    
    if [ "$CELERY_RESTART_ANSWER" = "y" ]; then
        echo "Stopping Celery worker..."
        pkill -f "celery.*-A JIPipePlugin.*worker" || true
        sleep 2
        
        echo "Starting Celery worker..."
        start_celery_worker
        echo "Celery worker restarted."
    else
        echo "Keeping existing Celery worker running."
    fi
else
    echo "No Celery worker found. Starting one..."
    
    # Start celery as background job
    start_celery_worker

    # Wait a moment and verify the worker started
    sleep 2
    if pgrep -f "celery.*-A JIPipePlugin.*worker" > /dev/null; then
        echo "Celery worker started successfully."
    else
        echo "Warning: Celery worker may have failed to start. Check $CURRENT_DIR/celery_worker.log for details."
    fi
fi

# === OPTIONALLY RESTART OMERO.WEB ===s
read -p "Restart omero.web to apply changes? [y/n] " RESTART_ANSWER
while [[ "$RESTART_ANSWER" != "n" && "$RESTART_ANSWER" != "y" ]]; do
    echo "Invalid answer, only y or n are accepted answers. Try again!"
    read -p "Restart omero.web to apply changes? [y/n] " RESTART_ANSWER
done

if [ "$RESTART_ANSWER" = "y" ]; then
    run_as_omero-web omero web restart
fi
