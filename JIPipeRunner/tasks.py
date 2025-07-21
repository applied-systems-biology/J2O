from celery import shared_task
import uuid, json, os, tempfile, subprocess, shutil, logging
from pathlib import Path
from django.core.cache import cache
from omero.config import ConfigXml
import signal
import time
from django.conf import settings
import docker
from docker.errors import ImageNotFound, NotFound

# Turn SIGTERM into KeyboardInterrupt so it can be caught by the task (necessary to shutdown child processes)
signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))

# Directory where JIPipe log files are stored (customize via Django settings)
LOG_DIR = getattr(settings, 'JIPIPE_LOG_ROOT', '/tmp/jipipe/logs')
os.makedirs(LOG_DIR, exist_ok=True)

"""
This task runs a JIPipe project in the background using ImageJ CLI.
It creates temporary directories for input and output, runs the JIPipe 
project using xvfb-run to handle GUI elements, and logs the output to a 
specified log file. When the task is interrupted or fails, it cleans up 
the temporary directories and logs the error.

param jipipe_project_config: JSON configuration of the JIPipe project
param job_uuid: Unique identifier for the JIPipe job
param omero_user_name: Username of the OMERO user running the job
param jipipe_log_file_path: Path to the log file for the JIPipe job
"""
@shared_task(bind=True)
def run_jipipe_task(self, jipipe_project_config, parameter_override_json, job_uuid, omero_user_name, jipipe_log_file_path, major_version):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Create temporary directories for handling input and output
    temp_input = tempfile.mkdtemp()
    temp_output = tempfile.mkdtemp()

    # Create process variable
    process = None

    try:
        # Save the JIPipe project configuration to a file to access it via ImageJ CLI
        jip_project_file_path = Path(temp_input) / 'JIPipeProject.jip'
        with open(jip_project_file_path, 'w') as f:
            json.dump(jipipe_project_config, f)

        # Save the JIPipe project parameter override to a file to access it via ImageJ CLI
        jip_parameter_override_file_path = Path(temp_input) / 'JIPipeProject_Parameter_Override.json'
        with open(jip_parameter_override_file_path, 'w') as f:
            json.dump(parameter_override_json, f)

        # Get the ImageJ path from the OMERO configuration to run JIPipe on
        cfg_file = os.path.join(os.environ["OMERODIR"], "etc", "grid", "config.xml")
        cfg = ConfigXml(cfg_file, read_only=True)
        imagej_path = cfg.as_map().get("omero.web.imagej")

        # Define the command to run JIPipe using ImageJ CLI
        # TODO: Make memory configurable
        command = [
            'xvfb-run', '-a', imagej_path,
            '-Dorg.apache.logging.log4j.simplelog.StatusLogger.level=ERROR',
            '-Dorg.apache.logging.log4j.simplelog.level=ERROR',
            '--memory', '8G',
            '--pass-classpath', '--full-classpath',
            '--main-class', 'org.hkijena.jipipe.cli.JIPipeCLIMain',
            'run', '--project', str(jip_project_file_path),
            '--output-folder', temp_output, 
            '--overwrite-parameters', str(jip_parameter_override_file_path)
        ]

        # Add --fast-init to command if version is supporting it 
        if major_version >= 5:
            command.append('--fast-init')

        # Run the command and log the output
        with open(jipipe_log_file_path, 'w') as log_file:
            log_file.write("Executable ImageJ at: " + imagej_path + "\n")

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )

            # Write the output of the process to the log file
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()

            # Wait for the process to complete
            process.wait()

    except KeyboardInterrupt:
        # On receiving SIGTERM terminate the process if one was defined
        if process:
            os.killpg(process.pid, signal.SIGTERM)
        
    except Exception as e:
        # Log any exceptions that occur during the task and throw an exception in OMERO log
        with open(jipipe_log_file_path, 'a') as log_file:
            log_file.write(f"\nERROR in JIPipe background job: {e}\n")
        log.exception("Error in Celery JIPipe task")

    finally:
        # Close cfg and clean up cache and temporary directories
        cfg.close()
        user_key = f"active_jipipe_jobs_{omero_user_name}"
        active = cache.get(user_key, [])
        active = [job for job in active if job["job_uuid"] != job_uuid]
        cache.set(user_key, active, timeout=None)
        safe_rmtree(temp_input)
        safe_rmtree(temp_output)

# Helper function that makes the rmtree call more resilient
def safe_rmtree(path, retries=3, delay=1):
    for i in range(retries):
        try:
            shutil.rmtree(path)
            return
        except OSError as e:
            if i < retries - 1:
                time.sleep(delay)
            else:
                raise e

@shared_task(bind=True, acks_late=True)
def run_jipipe_ephemeral(self, jipipe_project_config: dict, parameter_override_json: dict, job_uuid: str, omero_user_name: str, jipipe_log_file_path: str, jipipe_version: int | None = None):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Create temporary directories for handling input and output
    temp_input = tempfile.mkdtemp()
    temp_output = tempfile.mkdtemp()

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
            "--project",              "/work/input/JIPipeProject.jip",
            "--overwrite-parameters", "/work/input/JIPipeProject_Parameter_Override.json",
            "--output-folder",        "/work/output",
        ]

        uid = os.getuid()
        gid = os.getgid()

        container = client.containers.run(
            image,
            command=command,
            name=name,
            detach=True,
            auto_remove=False,
            network_mode="host",
            user=f"{uid}:{gid}",
            volumes={
                str(temp_input) : {"bind": "/work/input",  "mode": "ro"},
                str(temp_output): {"bind": "/work/output", "mode": "rw"},
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
        safe_rmtree(temp_input)
        safe_rmtree(temp_output)