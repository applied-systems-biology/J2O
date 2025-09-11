from celery import shared_task
import json, os, logging
from pathlib import Path
from django.core.cache import cache
import docker
from docker.errors import ImageNotFound, NotFound
from JIPipePlugin import settings

# Directory where JIPipe log files are stored (customize via Django settings)
HOME = Path("~").expanduser()     
LOG_DIR = settings.JIPIPERUNNER_LOG_DIR or HOME / "jipipe-runner" / "logs"
os.makedirs(LOG_DIR, exist_ok=True)

"""
This task runs an ephemeral docker container that will execute the provided .jip file using JIPipe.
It utilizes a temporary filesystem for input/output handling and will update the redis cache to track active tasks.
After it finishes, the container is removed, but the output will in the temporary filesystem until it is uploaded to OMERO later on.

param jipipe_project_config: JSON configuration of the JIPipe project (the contents of the selected .jip file)
param job_uuid: Unique identifier for the JIPipe job
param omero_user_name: Username of the OMERO user running the job for tracking ownership of jobs
param jipipe_log_file_path: Path to the log file for the JIPipe job
param temp_input: Path to the temporary input directory in the filesystem to store JIPipe input
param temp_output: Path to the temporary output directory in the filesystem to store JIPipe output
"""
@shared_task(bind=True, acks_late=True)
def run_jipipe_ephemeral(self, jipipe_project_config: dict, parameter_override_json: dict, job_uuid: str, omero_user_name: str, jipipe_log_file_path: str, jipipe_version: int, temp_input: str, temp_output: str | None = None):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Empty container variable
    container = None

    try:
        # Client for docker
        client = docker.from_env()

        # Dump input into temp directory
        (Path(temp_input) / "JIPipeProject.jip").write_text(json.dumps(jipipe_project_config))
        (Path(temp_input) / "JIPipeProject_Parameter_Override.json").write_text(
            json.dumps(parameter_override_json)
        )

        # Run the container on the image corresponding to the JIPipe version
        image   = f"mariuswank/jipipe_headless:{jipipe_version}"
        name    = f"jipipe-{jipipe_version}-{job_uuid[:8]}"

        command = [
            "run",
            "--project",              f"{temp_input}/JIPipeProject.jip",
            "--overwrite-parameters", f"{temp_input}/JIPipeProject_Parameter_Override.json",
            "--output-folder",        f"{temp_output}",
        ]

        uid = os.getuid()
        gid = os.getgid()

        container = client.containers.run(
            image,
            command=command,
            name=name,
            detach=True,
            auto_remove=False,
            user=f"{uid}:{gid}",
            network_mode="host",
            volumes={
                str(temp_input) : {"bind": temp_input,  "mode": "rw"},
                str(temp_output): {"bind": temp_output, "mode": "rw"}
            },
            stdout=True,
            stderr=True
        )

        # Stream the log
        for line in container.logs(stream=True):
            decoded = line.decode()
            with open(jipipe_log_file_path, "a") as logfile:
                logfile.write(decoded)
        
        exit_code = container.wait()["StatusCode"]
        with open(jipipe_log_file_path, "a") as logfile:
            logfile.write("JIPipe container exited with code {}\n".format(str(exit_code)))

        if exit_code != 0 and exit_code < 128:
            raise RuntimeError

    except (ImageNotFound, NotFound) as err:
        # tag doesn’t exist or pull failed
        log.error("Docker image not found: %s", err)
        with open(jipipe_log_file_path, "a") as logfile:
                logfile.write("The requested docker image was not found. Check if the JIPipe version you used creating the .jip file is supported by the plugin!\n")
                logfile.write("Stopping task!\n")

    except RuntimeError as err:
        log.error("Unexpected error in JIPipe task: %s. Check the JIPipe log!", err)
        with open(jipipe_log_file_path, "a") as logfile:
            logfile.write("Unexpected error in JIPipe execution. Check the JIPipe log above.\n")
            logfile.write("Stopping task!\n")

    finally:
        if container is not None:
            container.remove(force=True)   
        user_key = f"active_jipipe_jobs_{omero_user_name}"
        active = cache.get(user_key, [])
        active = [job for job in active if job["job_uuid"] != job_uuid]
        cache.set(user_key, active, timeout=None)