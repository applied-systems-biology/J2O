# J2O/settings.py (plugin scope, not the project)
from pathlib import Path
import os
from omero.config import ConfigXml

def get_omero_config(key: str, default: str) -> str:
    try:
        omerodir = os.environ.get("OMERODIR")
        if not omerodir:
            raise RuntimeError("OMERODIR environment variable not defined")
        
        # Build path to config.xml
        cfg_path = os.path.join(omerodir, "etc", "grid", "config.xml")

        # Load config
        cfg = ConfigXml(cfg_path, read_only=True)

        cfg_map = cfg.as_map()  # returns a dict of name → value

        value = cfg_map.get(key)

        # Return found value if not None
        if value:
            return value
        
        # Return default if no value is stored
        return default
    except Exception:
        return default
    finally:
        cfg.close()

HOME = Path("~").expanduser()
# Plugin-specific defaults or OMERO overrides
J2O_TEMP_DIR = get_omero_config("omero.web.jipipe.tempdir", os.fspath(HOME / "j2o-files" / "data"))
J2O_LOG_DIR = get_omero_config("omero.web.jipipe.logdir", os.fspath(HOME / "j2o-files" / "logs"))
CPU_PERIOD = get_omero_config("omero.web.jipipe.cpu_period", 100000)
PER_JOB_CPU_QUOTA = get_omero_config("omero.web.jipipe.cpu_quota", 200000)
PER_JOB_MEM_LIMIT = get_omero_config("omero.web.jipipe.mem_limit", "8g")

os.makedirs(J2O_LOG_DIR, exist_ok=True)
os.makedirs(J2O_TEMP_DIR, exist_ok=True)