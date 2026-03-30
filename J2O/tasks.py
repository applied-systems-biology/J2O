from celery import shared_task
import json, os, logging
from pathlib import Path
from django.core.cache import cache
import podman
from podman.errors import ImageNotFound, NotFound
from JIPipePlugin import settings
import time

CPU_PERIOD = settings.CPU_PERIOD
PER_JOB_CPU_QUOTA = settings.PER_JOB_CPU_QUOTA
PER_JOB_MEM_LIMIT = settings.PER_JOB_MEM_LIMIT

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
def run_jipipe_ephemeral(self, jipipe_project_config: dict, parameter_override_json: dict, user_directory_override_json: dict, job_uuid: str, omero_user_name: str, jipipe_log_file_path: str, jipipe_version: int, temp_input: str, temp_output: str | None = None):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Empty container variable
    container = None

    try:
        # Client for Podman (use CONTAINER_HOST, or fall back to the default rootless socket)
        base_url = os.environ.get("CONTAINER_HOST") or f"unix:///run/user/{os.getuid()}/podman/podman.sock"
        client = podman.PodmanClient(base_url=base_url)  # Podman REST API client

        # Dump input into temp directory
        if not (Path(temp_input) / "project.jip").is_file(): # Create project.jip for non RO-crate projects
            (Path(temp_input) / "project.jip").write_text(json.dumps(jipipe_project_config))
        (Path(temp_input) / "JIPipeProject_Parameter_Override.json").write_text(json.dumps(parameter_override_json))
        (Path(temp_input) / "JIPipeProject_User_Directory_Override.json").write_text(json.dumps(user_directory_override_json))

        # Run the container on the image corresponding to the JIPipe version
        repo    = f"docker.io/mariuswank/jipipe_headless"
        tag     = str(jipipe_version)
        image   = f"docker.io/mariuswank/jipipe_headless:{jipipe_version}"
        name    = f"jipipe-{jipipe_version}-{job_uuid[:8]}"

        
        # Stream layer-by-layer progress as JSON events
        for evt in client.images.pull(
                repository=repo,
                tag=tag,
                stream=True,          # <- stream progress
                decode=True,          # <- yield dicts, not bytes
                policy="newer"      # <- only pull if newer version exists
            ):

            stream = evt.get("stream")
            with open(jipipe_log_file_path, "a") as logfile:
                logfile.write(f"[pull] {stream}")

        with open(jipipe_log_file_path, "a") as logfile:
                logfile.write(f"\n[pull] Found image {image}")

        command = [
            "run",
            "--project",              f"{temp_input}/project.jip",
            "--overwrite-parameters", f"{temp_input}/JIPipeProject_Parameter_Override.json",
            "--overwrite-user-directories", f"{temp_input}/JIPipeProject_User_Directory_Override.json",
            "--output-folder",        f"{temp_output}",
        ]

        if jipipe_version >= 4:
             command.append("--fast-init")

        container = client.containers.run(
            image,
            command=command,
            name=name,
            detach=True,
            auto_remove=False,
            mounts=[
                {"type": "bind", "source": str(temp_input),  "target": temp_input},
                {"type": "bind", "source": str(temp_output), "target": temp_output},
            ],
            stdout=True,
            stderr=True,
            cpu_period=CPU_PERIOD,
            cpu_quota=PER_JOB_CPU_QUOTA,
            mem_limit=PER_JOB_MEM_LIMIT
        )

        # Stream the log
        while True:
            for chunk in container.logs(stream=True, follow=True):
                decoded = chunk.decode()
                with open(jipipe_log_file_path, "a") as logfile:
                    logfile.write(f"\n{decoded}")
            
            container.reload()
            if bool(container.attrs.get("State", {}).get("Running")):
                 continue
            else:
                 break

        
        exit_code = container.wait(condition="exited")
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