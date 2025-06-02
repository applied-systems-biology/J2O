from celery import shared_task
import json, os, tempfile, subprocess, shutil, logging
from pathlib import Path
from django.core.cache import cache
from omero.config import ConfigXml
import signal
from django.conf import settings

# Turn SIGTERM into KeyboardInterrupt so it can be caught by the task (necessary to shutdown child processes)
signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))

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
def run_jipipe_task(self, jipipe_project_config, parameter_override_json, job_uuid, omero_user_name, jipipe_log_file_path):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Create temporary directories for handling input and output
    temp_input = tempfile.mkdtemp()
    temp_output = tempfile.mkdtemp()

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
            '--overwrite-parameters', str(jip_parameter_override_file_path),
        ]
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

            log_file.write(f"\n[ JIPipe exited with code {process.returncode} ]\n")

    except KeyboardInterrupt:
        # On receiving SIGTERM terminate the process
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
        shutil.rmtree(temp_input)
        shutil.rmtree(temp_output)
