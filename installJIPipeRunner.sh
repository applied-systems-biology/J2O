#!/bin/bash
set -euo pipefail
set -x

# === SET ENV VAR FOR SCRIPT ===
echo "Setting env variables..."
CURRENT_DIR=$(pwd)
read -p "Are you using the default directory names and omero-web local system user for OMERO.web? [y/n]" DEFAULT_ANSWER

while [[ "$DEFAULT_ANSWER" != "n" && "$DEFAULT_ANSWER" != "y" ]]; do
    echo "Invalid answer, only y or n are accepted answers. Try again!"
    read -p "Are you using the default directory names and omero-web local system user for OMERO.web? [y/n]" DEFAULT_ANSWER
done

if [ "$DEFAULT_ANSWER" = "n" ]; then
    read -p "Enter the system username used to run OMERO (default should be omero-web): " OMERO_USER
    read -p "Enter the path to omero-web pip (default should be /opt/omero/web/venv3/bin/pip): " PIP_PATH
    read -p "Enter the path to omero bin (default should be /opt/omero/web/venv3/bin/omero): " OMERO_PATH
    read -p "Enter the OMERODIR path (default should be /opt/omero/web/omero-web): " OMERODIR
else
    OMERO_USER="omero-web"
    PIP_PATH="/opt/omero/web/venv3/bin/pip"
    OMERO_PATH="/opt/omero/web/venv3/bin/omero"
    OMERODIR="/opt/omero/web/omero-web"
fi

# === VALIDATE ENV VAR ===
OMERO_CLI=(sudo -u "$OMERO_USER" env OMERODIR="$OMERODIR" "$OMERO_PATH")
if [ "$EUID" -ne 0 ]; then
    echo "Please run this script as root or with sudo"
    exit 1
fi

if ! "${OMERO_CLI[@]}" -h &> /dev/null; then
    echo "Error: 'omero' CLI not usable for user $OMERO_USER"
    exit 1
fi

if [ ! -x "$PIP_PATH" ]; then
    echo "Error: pip path '$PIP_PATH' does not exist or is not executable"
    exit 1
fi

# === INSTALL JIPIPERUNNER ===
echo "Installing OMERO plugin..."
if sudo -u "$OMERO_USER" "$PIP_PATH" show "JIPipeRunner" > /dev/null 2>&1; then
    read -p "Plugin 'JIPipeRunner' is already installed. Do you want to reinstall it? [y/n]" REINSTALL_ANSWER

    while [[ "$REINSTALL_ANSWER" != "n" && "$REINSTALL_ANSWER" != "y" ]]; do
        echo "Invalid answer, only y or n are accepted answers. Try again!"
        read -p "Plugin 'JIPipeRunner' is already installed. Do you want to reinstall it? [y/n]" REINSTALL_ANSWER
    done

    if [ "$REINSTALL_ANSWER" = "y" ]; then
        sudo -u "$OMERO_USER" "$PIP_PATH" install --upgrade --force-reinstall "$CURRENT_DIR"
    fi
else
    sudo -u "$OMERO_USER" "$PIP_PATH" install "$CURRENT_DIR"
fi

# === CONFIGURE OMERO WEB ===
echo "Configuring omero config..."

OMERO_CONFIG_CACHES=$("${OMERO_CLI[@]}" config get omero.web.caches)
OMERO_CLI=(sudo -u "$OMERO_USER" env OMERODIR="$OMERODIR" OMERO_CONFIG_CACHES="$OMERO_CONFIG_CACHES" "$OMERO_PATH")
APP_ENTRY='"JIPipeRunner"'
PLUGIN_ENTRY='["JIPipeRunner", "JIPipeRunner/right_plugin_example.js.html", "jipipe_form_container"]'

CURRENT_APPS=$("${OMERO_CLI[@]}" config get omero.web.apps 2>/dev/null || echo "")

if echo "$CURRENT_APPS" | grep -Fq "$APP_ENTRY"; then
    echo "$APP_ENTRY already present in omero.web.apps"
else
    echo "Appending $APP_ENTRY to omero.web.apps"
    "${OMERO_CLI[@]}" config append omero.web.apps "$APP_ENTRY"
fi

# === Append to omero.web.ui.right_plugins if not already present ===
echo "Checking omero.web.ui.right_plugins..."
CURRENT_PLUGINS=$("${OMERO_CLI[@]}" config get omero.web.ui.right_plugins 2>/dev/null || echo "[]")

# Use Python to parse the list of lists and check for an exact match
PLUGIN_CHECK=$(python3 -c "
import json
import sys
current = json.loads('$CURRENT_PLUGINS')
target = $PLUGIN_ENTRY
if target in current:
    sys.exit(0)
else:
    sys.exit(1)
")

if [ "$?" -eq 0 ]; then
    echo "$PLUGIN_ENTRY already present in omero.web.ui.right_plugins"
else
    echo "Appending $PLUGIN_ENTRY to omero.web.ui.right_plugins"
    "${OMERO_CLI[@]}" config append omero.web.ui.right_plugins "$PLUGIN_ENTRY"
fi

# === ASK IF OMERO WEB SHOULD BE RESTARTED ===
read -p "Restart omero.web to apply changes? [y/n]" RESTART_ANSWER

while [[ "$RESTART_ANSWER" != "n" && "$RESTART_ANSWER" != "y" ]]; do
        echo "Invalid answer, only y or n are accepted answers. Try again!"
        read -p "Restart omero.web to apply changes? [y/n]" RESTART_ANSWER
    done

    if [ "$RESTART_ANSWER" = "y" ]; then
        "${OMERO_CLI[@]}" web restart
    fi