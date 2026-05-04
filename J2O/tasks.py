from celery import shared_task
import json, os, logging
from pathlib import Path
from django.core.cache import cache
import podman
from podman.errors import ImageNotFound, NotFound, APIError
from JIPipePlugin import settings
import time

CPU_PERIOD = settings.CPU_PERIOD
PER_JOB_CPU_QUOTA = settings.PER_JOB_CPU_QUOTA
PER_JOB_MEM_LIMIT = settings.PER_JOB_MEM_LIMIT
GPU_DEVICES = settings.GPU_DEVICES
GPU_COUNT = settings.GPU_COUNT
GPU_RESERVATION_TTL = int(settings.GPU_RESERVATION_TTL) if settings.GPU_RESERVATION_TTL != None else settings.GPU_RESERVATION_TTL
JIPIPE_ARTIFACTS_DIR = settings.JIPIPE_ARTIFACTS_DIR

# Host paths for Podman (when running on Docker host)
# These translate container paths to host-accessible paths
J2O_HOST_DATA_PATH = settings.J2O_HOST_DATA_PATH
J2O_HOST_ARTIFACTS_PATH = settings.J2O_HOST_ARTIFACTS_PATH


def container_path_to_host_path(container_path: str, container_prefix: str, host_prefix: str) -> str:
    """
    Translate a container path to a host path.
    
    When Podman runs on the Docker host, it needs host paths, not container paths.
    This function replaces the container path prefix with the host path prefix.
    
    Args:
        container_path: The path inside the container (e.g., /opt/omero/web/.../data/tmpXXX)
        container_prefix: The container path prefix to replace (e.g., /opt/omero/web/.../data)
        host_prefix: The host path prefix to use instead (e.g., /tmp/j2o-files/data)
    
    Returns:
        The translated host path
    """
    if container_path.startswith(container_prefix):
        return container_path.replace(container_prefix, host_prefix, 1)
    return container_path


class GPUReservationError(RuntimeError):
    """Raised when no GPU could be reserved within the timeout."""
    pass


def reserve_gpu(job_uuid: str, log: logging.Logger) -> int | None:
    """Try to reserve an exclusive GPU for *job_uuid* via Redis.

    Returns the GPU index on success, or ``None`` when the scheduler is
    disabled (``GPU_COUNT == 0``). Also does not reserve GPU when no device is listed in GPU_DEVICES.

    Raises ``RuntimeError`` if no GPU is available.
    """
    if GPU_COUNT <= 0 or not GPU_DEVICES:
        return None  # scheduler disabled – caller will fall back to GPU_DEVICES

    deadline = time.monotonic()

    while True:
        for gpu_idx in range(GPU_COUNT):
            key = f"j2o_gpu_reserved_{gpu_idx}"
            # cache.add() is atomic (Redis SETNX) – only succeeds if key is absent
            if cache.add(key, job_uuid, timeout=GPU_RESERVATION_TTL):
                log.info("Reserved GPU %d for job %s", gpu_idx, job_uuid)
                return gpu_idx

        # All GPUs are reserved – wait or give up
        if time.monotonic() >= deadline:
            raise GPUReservationError(
                f"No GPU available. "
                f"All GPUs are reserved by other J2O jobs."
            )
        time.sleep(2)


def release_gpu(gpu_idx: int, job_uuid: str, log: logging.Logger) -> None:
    """Release a GPU reservation, but only if it still belongs to *job_uuid*."""
    if gpu_idx is None:
        return
    key = f"j2o_gpu_reserved_{gpu_idx}"
    current_owner = cache.get(key)
    if current_owner == job_uuid:
        cache.delete(key)
        log.info("Released GPU %d from job %s", gpu_idx, job_uuid)
    else:
        log.warning(
            "GPU %d reservation was already taken over by another job (expected %s, found %s). "
            "Skipping release to avoid stealing.",
            gpu_idx, job_uuid, current_owner,
        )

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
def run_jipipe_ephemeral(self, jipipe_project_config: dict, parameter_override_json: dict, user_directory_override_json: dict, job_uuid: str, omero_user_name: str, jipipe_log_file_path: str, jipipe_version: int, temp_input: str, temp_output: str | None = None, requires_gpu: bool = False):

    # Initialize logging
    log = logging.getLogger(__name__)

    # Empty container variable
    container = None
    reserved_gpu = None  # track GPU index for cleanup

    try:
        # Pre-flight: ensure artifacts directory exists and is writable
        artifacts_path = Path(JIPIPE_ARTIFACTS_DIR)
        if not artifacts_path.is_dir():
            try:
                artifacts_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                raise RuntimeError(
                    f"JIPipe artifacts directory '{JIPIPE_ARTIFACTS_DIR}' does not exist "
                    f"and cannot be created by the current user (permission denied). "
                    f"Please ask your system administrator to run: "
                    f"sudo mkdir -p {JIPIPE_ARTIFACTS_DIR} && "
                    f"sudo chown -R $(whoami) {JIPIPE_ARTIFACTS_DIR}"
                )
        if not os.access(JIPIPE_ARTIFACTS_DIR, os.W_OK):
            raise RuntimeError(
                f"JIPipe artifacts directory '{JIPIPE_ARTIFACTS_DIR}' is not writable. "
                f"Please ask your system administrator to run: "
                f"sudo chown -R $(whoami) {JIPIPE_ARTIFACTS_DIR}"
            )

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

        # Determine GPU device list and environment based on requires_gpu flag and scheduler state
        container_env = {}

        if not requires_gpu:
            # CPU-only workflow: no GPU devices needed
            device_list = []
            with open(jipipe_log_file_path, "a") as logfile:
                logfile.write("\n[gpu] CPU-only workflow – GPU passthrough disabled\n")
        else:
            # GPU workflow: reserve a GPU (or fall back to GPU_DEVICES when scheduler is disabled)
            reserved_gpu = reserve_gpu(job_uuid, log)

            if reserved_gpu is not None:
                # Scheduler enabled: pass all GPUs via CDI but restrict visibility
                # to a single GPU using NVIDIA_VISIBLE_DEVICES env var.
                # This works on WSL2 and bare metal without needing per-GPU CDI specs.
                device_list = [GPU_DEVICES] if GPU_DEVICES else []
                container_env["NVIDIA_VISIBLE_DEVICES"] = str(reserved_gpu)
                with open(jipipe_log_file_path, "a") as logfile:
                    logfile.write(f"\n[gpu] Assigned GPU {reserved_gpu} to job {job_uuid} (NVIDIA_VISIBLE_DEVICES={reserved_gpu})\n")
            else:
                # Scheduler disabled: use the static GPU_DEVICES setting
                device_list = [d.strip() for d in GPU_DEVICES.split(",") if d.strip()] if GPU_DEVICES else []
                with open(jipipe_log_file_path, "a") as logfile:
                    if device_list:
                        logfile.write(f"\n[gpu] Using static GPU devices: {device_list}\n")
                    else:
                        logfile.write("\n[gpu] No GPU devices configured\n")

        # Build container run kwargs; only include cgroup limits when configured.
        # When CPU_PERIOD/PER_JOB_CPU_QUOTA/PER_JOB_MEM_LIMIT are None (default),
        # no limits are applied — the container uses all available host resources.
        
        # Translate container paths to host paths for Podman running on Docker host (if HOST_PATHs are not set we still use config defined path)
        # The container paths are like /opt/omero/web/.../data/tmpXXX
        # The host paths are like /tmp/j2o-files/data/tmpXXX
        host_temp_input = container_path_to_host_path(
            str(temp_input),
            str(Path(J2O_TEMP_DIR).parent),  # /opt/omero/web/.../data -> parent of J2O_TEMP_DIR
            J2O_HOST_DATA_PATH  # /tmp/j2o-files
        )
        host_temp_output = container_path_to_host_path(
            str(temp_output),
            str(Path(J2O_TEMP_DIR).parent),
            J2O_HOST_DATA_PATH
        )
        host_artifacts = container_path_to_host_path(
            JIPIPE_ARTIFACTS_DIR,
            str(Path(JIPIPE_ARTIFACTS_DIR).parent),  # /root/.local/share/JIPipe -> parent
            J2O_HOST_ARTIFACTS_PATH  # /tmp/j2o-artifacts
        )
        
        run_kwargs = dict(
            image=image,
            command=command,
            name=name,
            detach=True,
            auto_remove=False,
            mounts=[
                {"type": "bind", "source": host_temp_input,  "target": temp_input},
                {"type": "bind", "source": host_temp_output, "target": temp_output},
                {"type": "bind", "source": host_artifacts, "target": "/root/.local/share/JIPipe/artifacts"},
            ],
            environment=container_env,
            devices=device_list,
            stdout=True,
            stderr=True,
        )

        if CPU_PERIOD and PER_JOB_CPU_QUOTA:
            run_kwargs["cpu_period"] = CPU_PERIOD
            run_kwargs["cpu_quota"] = PER_JOB_CPU_QUOTA
            with open(jipipe_log_file_path, "a") as logfile:
                logfile.write(f"\n[cgroup] CPU limits applied (period={CPU_PERIOD}, quota={PER_JOB_CPU_QUOTA})\n")

        if PER_JOB_MEM_LIMIT:
            run_kwargs["mem_limit"] = PER_JOB_MEM_LIMIT
            with open(jipipe_log_file_path, "a") as logfile:
                logfile.write(f"\n[cgroup] Memory limit applied (mem_limit={PER_JOB_MEM_LIMIT})\n")

        container = client.containers.run(**run_kwargs)

        # Stream the log
        while True:
            for chunk in container.logs(stream=True, follow=True):
                decoded = chunk.decode().rstrip("\n")
                if decoded:
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
        # tag doesn't exist or pull failed
        log.error("Docker image not found: %s", err)
        with open(jipipe_log_file_path, "a") as logfile:
                logfile.write("\n[J2O_ERROR] The requested docker image was not found. Check if the JIPipe version you used creating the .jip file is supported by the plugin!\n")
                logfile.write("Stopping task!\n")

    except GPUReservationError as err:
        log.error("GPU reservation failed: %s", err)
        with open(jipipe_log_file_path, "a") as logfile:
            logfile.write(f"\n[J2O_ERROR] GPU reservation failed: {err}\n")
            logfile.write("Stopping task!\n")

    except RuntimeError as err:
        log.error("Unexpected error in JIPipe task: %s. Check the JIPipe log!", err)
        with open(jipipe_log_file_path, "a") as logfile:
            logfile.write("\n[J2O_ERROR] Unexpected error in JIPipe execution. Check the JIPipe log above.\n")
            logfile.write("Stopping task!\n")

    except APIError as err:
        err_msg = str(err)
        log.error("Error in podman API: %s", err)
        with open(jipipe_log_file_path, "a") as logfile:
            logfile.write(f"\n[J2O_ERROR] Podman API error: {err}\n")
            if "cgroup controller" in err_msg.lower():
                logfile.write(
                    "\n[J2O_ERROR] This error is caused by missing cgroup controller delegation.\n"
                    "On cgroups v2, the cpu and memory controllers must be delegated to the\n"
                    "rootless Podman user for CPU/memory limits to be enforced.\n\n"
                    "To fix this on WSL2 (with systemd), run inside WSL:\n"
                    "  sudo mkdir -p /etc/systemd/system/user@.service.d\n"
                    "  sudo tee /etc/systemd/system/user@.service.d/delegate.conf <<EOF\n"
                    "  [Service]\n"
                    "  Delegate=cpu memory pids\n"
                    "  EOF\n"
                    "  Then restart WSL (wsl --shutdown from PowerShell).\n\n"
                    "Alternatively, add a boot command to /etc/wsl.conf:\n"
                    "  [boot]\n"
                    "  command=\"mkdir -p /sys/fs/cgroup/user.slice && echo +cpu +memory +pids > /sys/fs/cgroup/cgroup.subtree_control\"\n"
                    "  Then restart WSL.\n"
                )
            logfile.write("Stopping task!\n")

    finally:
        if container is not None:
            container.remove(force=True)
        # Release GPU reservation (no-op if no GPU was reserved)
        release_gpu(reserved_gpu, job_uuid, log)
        user_key = f"active_jipipe_jobs_{omero_user_name}"
        active = cache.get(user_key, [])
        active = [job for job in active if job["job_uuid"] != job_uuid]
        cache.set(user_key, active, timeout=None)