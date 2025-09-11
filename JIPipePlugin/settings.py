# JIPipeRunner/settings.py (plugin scope, not the project)
from pathlib import Path
import os

HOME = Path("~").expanduser()

# Plugin-specific defaults
JIPIPERUNNER_TEMP_DIR = os.fspath(HOME / "jipipe-runner" / "data")
JIPIPERUNNER_LOG_DIR  = os.fspath(HOME / "jipipe-runner" / "logs")

# Let admins override these via `omero config set ...`
CUSTOM_SETTINGS_MAPPINGS = {
    # "omero.web.<yourprefix>.<name>": ["DJANGO_SETTING_NAME", <default>, <parser>]
    "omero.web.jipipe.tempdir": ["JIPIPERUNNER_TEMP_DIR", JIPIPERUNNER_TEMP_DIR, str],
    "omero.web.jipipe.logdir":  ["JIPIPERUNNER_LOG_DIR",  JIPIPERUNNER_LOG_DIR,  str],
}