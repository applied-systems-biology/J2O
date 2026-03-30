from datetime import datetime
import json
import logging
import os
import signal
import uuid
from typing import Optional
import traceback
import tempfile
import contextlib
import fnmatch
import io
import shutil
import mimetypes
from pathlib import Path
import zipfile
import io
from io import BytesIO

from JIPipePlugin import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_protect

from JIPipePlugin.celery import app
from J2O.tasks import run_jipipe_ephemeral
from celery.result import AsyncResult

import omero
import omero.model
from omero.rtypes import rstring, rlong
from omeroweb.decorators import login_required
from omero.cli import CLI, NonZeroReturnCode
import omero.model as omodel
from omero.model import ProjectI

# Directory where JIPipe files are stored (customizable via Django settings)
LOG_DIR = settings.J2O_LOG_DIR
JIPIPE_TEMP_DIR = settings.J2O_TEMP_DIR

# Time (in seconds) to keep PIDs in cache before expiring (None == never expire)
CACHE_TIMEOUT: Optional[int] = None

# Initialize the logger
logger = logging.getLogger(__name__)

@login_required()
def j2o_index(request, conn=None, **kwargs) -> HttpResponse:
    """
    Display the J2O HTML.
    """
    return render(
        request,
        'J2O/dataset_input.html'
    )

@require_POST
@login_required()
def start_jipipe_job(request, conn=None, **kwargs) -> JsonResponse:
    """
    Start a JIPipe job in the background using Celery.
    Expects a JSON payload with jip_file_id - the JIPipe file content is fetched
    server-side from OMERO to avoid large request bodies.
    Returns JSON with the unique job ID of the started job.

    URL: J2O/start_jipipe_job/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """

    # Check for a live worker via inspect().ping()
    try:
        inspector = app.control.inspect()
        ping_response = inspector.ping() or {}

        # If no worker replies, don't start job
        if not ping_response:
            return JsonResponse({'error': 'Could not start job. No Celery workers are currently running.'}, status=503)

        # Parse the incoming configuration
        json_request = json.loads(request.body.decode('utf-8'))
        parameter_override_json = json_request.get('jip_parameter_overrides', {})
        user_directory_override_json = json_request.get('jip_user_directory_overrides', {})
        jip_file_name = json_request.get('jip_name', 'JIPipeProject.jip')
        major_version = json_request.get('major_version')
        temp_input = json_request.get('input_path')
        temp_output = json_request.get('output_path')
        jip_file_id = json_request.get('jip_file_id')

        # Server-side fetch of JIPipe file content from OMERO (avoids large request body)
        jip_file = conn.getObject('originalfile', jip_file_id)
        if jip_file is None:
            raise FileNotFoundError(f"JIPipe file with ID {jip_file_id} not found.")

        raw_bytes = b''.join(jip_file.getFileInChunks())
        if jip_file.getName().endswith('.jip'):
            jipipe_json = json.loads(raw_bytes.decode('utf-8'))
        elif jip_file.getName().endswith('.zip'):
            # Extract .jip from zip archive
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zip_ref:
                zip_contents = zip_ref.namelist()
                json_filename = None
                for file in zip_contents:
                    if file.endswith('.jip'):
                        json_filename = file
                        break
                if not json_filename:
                    raise FileNotFoundError("No .jip file found inside the zip archive.")
                with zip_ref.open(json_filename) as json_file:
                    jipipe_json = json.loads(json_file.read().decode('utf-8'))
        else:
            raise TypeError("Selected file is neither .jip nor .zip and is therefore not supported!")

        # For .zip files, also extract all other files to temp_input for the Celery task
        # (jipipe_json was already extracted above)
        if jip_file_name.endswith(".zip"):
            # Store the zip for debugging/reproducibility
            zip_path = os.path.join(temp_input, jip_file_name)
            with open(zip_path, "wb") as f:
                f.write(raw_bytes)

            # Extract everything into temp_input/<zip_root_contents...>
            with zipfile.ZipFile(BytesIO(raw_bytes)) as zf:
                for member in zf.infolist():
                    member_path = member.filename.replace("\\", "/")

                    # Skip weird entries (empty or root) and the .jip file (already processed)
                    if not member_path or member_path.endswith("/") and member_path.strip("/") == "":
                        continue
                    if member_path.endswith('.jip'):
                        continue  # Already extracted as jipipe_json

                    # Final path under temp_input
                    out_path = os.path.join(temp_input, member_path)

                    # Zip-slip protection: ensure extraction stays within temp_input
                    norm_root = os.path.abspath(temp_input)
                    norm_out = os.path.abspath(out_path)
                    if not (norm_out == norm_root or norm_out.startswith(norm_root + os.sep)):
                        raise ValueError(f"Unsafe path in zip entry: {member.filename}")

                    # Directory entry
                    if member.is_dir():
                        os.makedirs(out_path, exist_ok=True)
                        continue

                    # File entry
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with zf.open(member) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

        # Prepare the log file path and unique job identifier to reference the job later on
        job_uuid = uuid.uuid4().hex
        start_time = datetime.now().strftime('%d-%m-%Y_%H:%M:%S')
        log_file = os.path.join(LOG_DIR, f'{start_time}_{jip_file_name}_{job_uuid}.log')
        
        # Create an empty log file immediately so it's available when the frontend tries to fetch it
        # The Celery worker will append to this file once it starts
        Path(log_file).touch()

        # Launch the background thread to run the JIPipe task using Celery and attach the unique job ID for reference
        owner = conn.getUser().getName()
        run_jipipe_ephemeral.apply_async(
            args=[jipipe_json, parameter_override_json, user_directory_override_json, job_uuid, owner, log_file, major_version, temp_input, temp_output],
            task_id=job_uuid,
            ignore_result=True,
        )

        # Collect job information to store in the cache
        job_info = {
        "job_uuid": job_uuid,
        "name": jip_file_name,
        "start_time": start_time,
        "log_file_path": log_file
        }

        # Update the cache to track active jobs for the user
        logger.info(f"Starting JIPipe job for user {owner} with job ID {job_uuid}")
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        active.append(job_info)
        cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({'job_id': job_uuid, 'job_name': jip_file_name, 'job_start_time': start_time, 'log_file_path': log_file})
    
    except Exception as e:
        logger.exception("Exception while starting JIPipe job")
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=500)

@require_POST
@login_required()
def stop_jipipe_job(request, conn=None, **kwargs) -> JsonResponse:
    """
    Stop a running JIPipe job by revoking the Celery task.
    Expects a JSON payload containing the job_id to stop.
    Returns JSON with the status and job_id of the stopped 
    job if successful, or an error otherwise.

    URL: J2O/stop_jipipe_job/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        # Parse the incoming JSON payload to get the job_id
        job_dict = json.loads(request.body)
        job_uuid = job_dict.get('job_id')
        if not job_uuid:
            return JsonResponse({'error': 'No job ID was provided'}, status=422)
        
        # Get the current user and their active jobs from cache to verify ownership
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        job_exists = any(job_info["job_uuid"] == job_uuid for job_info in active)
        if not job_exists:
            return JsonResponse({'error': f'Job does not exist for user {owner}'}, status=404)
        
        # Get active job infos
        active_job_info = [job_info for job_info in active if job_info["job_uuid"] == job_uuid][0]

        # Revoke the Celery task (terminate immediately with SIGTERM)
        result = AsyncResult(job_uuid)
        result.revoke(terminate=True, signal=signal.SIGTERM)

        # Remove the job from the active jobs cache after successful revoke
        active = [job for job in active if job["job_uuid"] != job_uuid]
        cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({'status': 'terminated', 'job_uuid': job_uuid, 'name': active_job_info["name"], 'start_time': active_job_info["start_time"]})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON', 'trace': traceback.format_exc()}, status=400)

@require_GET
@login_required()
def list_jipipe_jobs(request, conn=None, **kwargs):
    """
    List all active JIPipe jobs for the current user.
    Returns a JSON response with job IDs of currently 
    running jobs owned by the current user.
    
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """

    # Get the current user and their active jobs from cache
    owner = conn.getUser().getName()
    user_key = f"active_jipipe_jobs_{owner}"
    active = cache.get(user_key, [])
    return JsonResponse({'job_infos': active})

@require_GET
@login_required()
def get_latest_jipipe_job(request, conn=None, **kwargs):

    try:
        # Get the current user and their active jobs from cache
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])

        if not active:
            return JsonResponse({'job_uuid': None, 'name': None, 'start_time': None, 'active_list': None})

        # Get the infos for the latest job
        latest_job_time = 0.0
        latest_job_index = -1
        for job_index, job_info in enumerate(active):
            start_time_dt = datetime.strptime(job_info["start_time"], '%d-%m-%Y_%H:%M:%S')
            absolute_start_time = int(start_time_dt.timestamp())

            if absolute_start_time > latest_job_time:
                latest_job_time = absolute_start_time
                latest_job_index = job_index

        return JsonResponse({'job_uuid': active[latest_job_index]["job_uuid"],'name': active[latest_job_index]["name"], 'start_time': active[latest_job_index]["start_time"], 'active_list': active})
    
    except Exception as e:
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc(), 'active_jobs': active}, status=500)

@require_GET
@login_required()
def fetch_jipipe_logs(request, job_uuid: str, conn=None, **kwargs) -> JsonResponse:
    """
    Fetch the logs for a specific JIPipe job using its UUID.
    Expects the job UUID as a URL parameter.
    Returns a JSON response with the job status and log lines.
    If the log is not found, returns a 400 error.

    URL: J2O/fetch_jipipe_logs/<str:job_uuid>/
    param request: Django HTTP request object
    param job_uuid: Unique identifier for the JIPipe job
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        # Get the log file from LOG_DIR using the job UUID
        log_file_path = None
        for file in os.listdir(LOG_DIR):
            if job_uuid in file:
                log_file_path = os.path.join(LOG_DIR, file)
        
        # Raise an error if the log file does not exist
        if not log_file_path:
            return JsonResponse({'error': f'Error retrieving jipipe log: Log file has not been created yet'}, status=400)

        # Get pagination parameters from query string
        # offset: starting line number (default: 0 = end of file for backward compatibility)
        # limit: max number of lines to return (default: 2000)
        # If offset is not provided, we return the last 'limit' lines (tail mode)
        offset = request.GET.get('offset', None)
        limit = int(request.GET.get('limit', 2000))
        
        # Read all log lines
        with open(log_file_path, 'r') as file_handle:
            all_log_lines = file_handle.read().splitlines()
        
        total_lines = len(all_log_lines)
        
        # Determine which lines to return
        if offset is not None:
            # Specific offset requested (forward pagination)
            offset = int(offset)
            log_lines = all_log_lines[offset:offset + limit]
        else:
            # No offset = return last 'limit' lines (tail mode - default for live streaming)
            log_lines = all_log_lines[-limit:] if total_lines > limit else all_log_lines
        
        # Check if the job is still active by looking in the cache
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        active_job_uuid_list = [job["job_uuid"] for job in active]

        # Determine if the job finished by checking the exit code message or if it is still in the active set
        # Check the last lines of the FULL log file (not just the returned subset)
        status = (
            'finished' if any('Run ending at' in line for line in all_log_lines[-10:]) or any("JIPipe container exited with code 0" in line for line in all_log_lines[-1:]) else
            'canceled' if (job_uuid not in active_job_uuid_list) else
            'running'
        )

        # Remove the job from active cache if it has finished but not removed yet
        if status != "running" and job_uuid in active_job_uuid_list:
            active = [job for job in active if job["job_uuid"] != job_uuid]
            cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({
            'status': status,
            'logs': log_lines,
            'total_lines': total_lines,
            'returned_offset': total_lines - len(log_lines) if offset is None else offset
        })
    
    except Exception as error:
        logger.exception('Failed to retrieve jipipe log: %s', error)
        return JsonResponse({'error': f'Error retrieving jipipe log: {error}'}, status=400)

@login_required()
def get_jipipe_config(request, jip_file_id: int, conn=None, **kwargs) -> JsonResponse:
    """
    Fetch the .jip file based on its file ID in OMERO.
    Expects the file ID as a URL parameter.
    Returns a JSON response with the parsed content of the .jip file.

    URL: J2O/get_jipipe_config/<jip_file_id>/
    param request: Django HTTP request object
    param jip_file_id: Unique identifier for the JIPipe file in OMERO
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        # Get the .jip file from OMERO using the provided file ID
        jip_file = conn.getObject('originalfile', jip_file_id)

        # If the file does not exist, return a 404 error
        if jip_file is None:
            raise FileNotFoundError(f".jip file with ID {jip_file_id} not found.")
        
        # Read and parse the JSON data from the annotation (file is either .jip or .zip for RO-Crates)
        raw_bytes = b''.join(jip_file.getFileInChunks())
        if jip_file.getName().endswith('.jip'):
            config_text = raw_bytes.decode('utf-8')
            config_data = json.loads(config_text)
        elif jip_file.getName().endswith('.zip'):
            # Open the zip file in memory using a BytesIO object
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zip_ref:
                # List files inside the zip to find the .jip file
                zip_contents = zip_ref.namelist()
                json_filename = None
                
                # Find the first .jip file (assuming only one exists)
                for file in zip_contents:
                    if file.endswith('.jip'):
                        json_filename = file
                        break
                
                if not json_filename:
                    raise FileNotFoundError("No .jip file found inside the zip archive.")
                
                # Extract the JSON file content
                with zip_ref.open(json_filename) as json_file:
                    config_text = json_file.read().decode('utf-8')
                    config_data = json.loads(config_text)
        else:
            raise TypeError("Selected file is neither .jip nor .zip and is therefore not supported!")

    
    except FileNotFoundError as e:
        logger.error(str(e), exc_info=1)
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=404)
    
    except json.JSONDecodeError as parse_error:
        logger.error('Failed to parse JIPipe JSON: %s', parse_error)
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=400)
    
    except TypeError as e:
        logger.error(str(e), exc_info=1)
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=422)

    return JsonResponse(config_data, safe=False)

@login_required()
def list_jipipe_files(request, conn=None, **kwargs) -> JsonResponse:
    """
    List all unique JIPipe-related files attached to projects in 
    all OMERO groups of the current user. 
    Returns a JSON response with file IDs and names.

    URL: J2O/list_jipipe_files/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        user_id = conn.getUserId()
        user_groups = list(conn.getOtherGroups(user_id))

        # Keep track of which file IDs we’ve already seen
        seen_file_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_jip_annotations = []

        # 3) Loop through each group, switch the service‐opts, and fetch every FileAnnotation
        for group in user_groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all FileAnnotation objects visible in this group
            # (returns a list of OMERO‐wrappers for FileAnnotation)
            file_annotations = conn.getObjects("FileAnnotation")
            for file_annotation in file_annotations:

                # Extract the numeric ID and the original filename
                file_id = file_annotation.getFile().getId()

                # Skip if we've already added this file_id
                if file_id in seen_file_ids:
                    continue
                seen_file_ids.add(file_id)

                # The FileAnnotation wrapper has a .getFile() method returning a FileI
                file_name = file_annotation.getFile().getName()

                # Only add .jip and .zip files
                if file_name.endswith(".jip") or file_name.endswith(".zip"):
                    all_jip_annotations.append({
                        'file_id': file_id,
                        'file_name': file_name
                })

        return JsonResponse({'files': all_jip_annotations})
    
    except Exception as e:
        # log the full stack trace so you can see what went wrong
        logger.exception('Failed to list JIPipe files: {e}')
        return JsonResponse(
            {'error': f'Internal server error retrieving JIPipe files: {e}', 'trace': traceback.format_exc()}, status=500)
    
    finally:
        conn.SERVICE_OPTS.setOmeroGroup(original_group_id)

@login_required()
def list_available_datasets(request, conn=None, **kwargs) -> JsonResponse:
    """
    Lists all datasets associated with projects in 
    all OMERO groups of the current user. 
    Returns a JSON response with dataset_id and dataset_name.

    URL: J2O/list_available_datasets/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        user_id = conn.getUserId()
        user_groups = list(conn.getOtherGroups(user_id))

        # Keep track of which file IDs we’ve already seen
        seen_dataset_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_available_datasets = []

        # 3) Loop through each group, switch the service‐opts, and fetch every Dataset
        for group in user_groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all Dataset objects visible in this group
            # (returns a list of OMERO‐wrappers for Dataset)
            datasets = conn.getObjects("Dataset")
            for dataset in datasets:

                # Extract the numeric ID and the original filename
                dataset_id = dataset.getId()

                # Skip if we've already added this file_id
                if dataset_id in seen_dataset_ids:
                    continue
                seen_dataset_ids.add(dataset_id)

                # The Dataset wrapper has a .getName() method returning dataset name
                dataset_name = dataset.getName()
                all_available_datasets.append({
                    'dataset_id': dataset_id,
                    'dataset_name': dataset_name
                })

        return JsonResponse({'available_datasets': all_available_datasets})
    
    except Exception as e:
        # log the full stack trace so you can see what went wrong
        logger.exception("Error when trying to lists available datasets: {e}", exc_info=1)
        return JsonResponse({'error': f'Internal server error listing available datasets: {e}', 'trace': traceback.format_exc()}, status=500)
    
    finally:
        conn.SERVICE_OPTS.setOmeroGroup(original_group_id)

@login_required()
def list_available_files(request, conn=None, **kwargs) -> JsonResponse:
    """
    Lists all files associated with projects in 
    all OMERO groups of the current user. 
    Returns a JSON response with file_id and file_name.

    URL: J2O/list_available_files/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        user_id = conn.getUserId()
        user_groups = list(conn.getOtherGroups(user_id))

        # Keep track of which file IDs we’ve already seen
        seen_file_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_available_files = []

        # 3) Loop through each group, switch the service‐opts, and fetch every FileAnnotation
        for group in user_groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all FileAnnotation objects visible in this group
            # (returns a list of OMERO‐wrappers for FileAnnotation)
            annotations = conn.getObjects("FileAnnotation")
            for annotation in annotations:

                # Extract the numeric ID and the original filename
                file_obj = annotation.getFile()
                file_id = file_obj.getId()

                # Skip if we've already added this file_id
                if file_id in seen_file_ids:
                    continue
                seen_file_ids.add(file_id)

                # The FileAnnotation wrapper has a .getFile() method returning a FileI
                file_name = file_obj.getName()
                all_available_files.append({
                    'file_id': file_id,
                    'file_name': file_name
                })

        return JsonResponse({'available_files': all_available_files})
    
    except Exception as e:
        # log the full stack trace so you can see what went wrong
        logger.exception("Error when trying to lists available files: {e}", exc_info=1)
        return JsonResponse({'error': f'Internal server error listing available files: {e}', 'trace': traceback.format_exc()}, status=500)
    
    finally:
        conn.SERVICE_OPTS.setOmeroGroup(original_group_id)

@login_required()
def list_available_projects(request, conn=None, **kwargs) -> JsonResponse:
    """
    Lists all projects in all OMERO groups of the current user. 
    Returns a JSON response with project_id and project_name.

    URL: J2O/list_available_projects/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        user_id = conn.getUserId()
        user_groups = list(conn.getOtherGroups(user_id))

        # Keep track of which file IDs we’ve already seen
        seen_project_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_available_projects = []

        # 3) Loop through each group, switch the service‐opts, and fetch every Project
        for group in user_groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all Project objects visible in this group
            # (returns a list of OMERO‐wrappers for Project)
            projects = conn.getObjects("Project")
            for project in projects:

                # Extract the numeric ID and the original filename
                project_id = project.getId()

                # Skip if we've already added this file_id
                if project_id in seen_project_ids:
                    continue
                seen_project_ids.add(project_id)

                # The Project wrapper has a .getName() method returning the project name
                project_name = project.getName()
                all_available_projects.append({
                    'project_id': project_id,
                    'project_name': project_name
                })

        return JsonResponse({'available_projects': all_available_projects})
    
    except Exception as e:
        # log the full stack trace so you can see what went wrong
        logger.exception("Failed to list available projects")
        return JsonResponse(
            {'error': f'Internal server error listing available projects: {e}'},
            status=500
        )
    
    finally:
        conn.SERVICE_OPTS.setOmeroGroup(original_group_id)


def create_temp_directories(request) -> JsonResponse:

    # Check permissions early (optional but recommended)
    var_map = {"LOG_DIR": "omero.web.jipipe.logdir", "JIPIPE_TEMP_DIR": "omero.web.jipipe.tempdir"}
    error_string = ""
    for var in ["LOG_DIR", "JIPIPE_TEMP_DIR"]:
        try:
            path = Path(globals()[var])
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            error_string += f"\nCannot create or write to directory '{path}' defined in {var_map[var]}: {e}"

    if len(error_string) > 0: 
        error_string += "\n\nConsult your system administrator to change permissions on the server or change the file location in the config!"
        return JsonResponse({'error': error_string}, status=500)

    # Create temporary directories for handling input and output
    temp_input = tempfile.mkdtemp(dir=JIPIPE_TEMP_DIR)
    temp_output = tempfile.mkdtemp(dir=JIPIPE_TEMP_DIR)

    return JsonResponse({'temp_input': temp_input, 'temp_output': temp_output})

@require_POST
def create_temp_subdirectories(request) -> JsonResponse:

    # Get the uuid lists from the request
    json_request = json.loads(request.body.decode('utf-8'))
    parent_path = json_request.get('parent_path')
    uuid = json_request.get('uuid')

    # Create subdirectory
    sub_path = os.path.join(parent_path, uuid)
    os.makedirs(sub_path, exist_ok=True)

    return JsonResponse({'sub_path': sub_path}, status=201)

@require_POST
@csrf_protect
def remove_temp_directories(request):
    """
    Delete temp directories, but ONLY if they are within JIPIPE_TEMP_DIR.

    Expects JSON body: { "temp_directories": ["<path1>", "<path2>", ...] }

    Security:
    - only allows directories within JIPIPE_TEMP_DIR
    - refuses deleting JIPIPE_TEMP_DIR itself
    """
    try:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"error": "Invalid JSON payload"}, status=400)

        dirs = payload.get("temp_directories", [])
        if not isinstance(dirs, list):
            return JsonResponse({"error": "temp_directories must be a list"}, status=400)

        allowed_base = Path(JIPIPE_TEMP_DIR).resolve()
        if not allowed_base.exists() or not allowed_base.is_dir():
            return JsonResponse(
                {"error": "Server misconfiguration: JIPIPE_TEMP_DIR invalid"},
                status=400,
            )

        deleted, skipped, errors = [], [], []

        for raw in dirs:
            try:
                if raw is None or str(raw).strip() == "":
                    skipped.append({"path": raw, "reason": "empty path"})
                    continue

                requested = Path(str(raw))

                # If relative, interpret relative to allowed base
                if not requested.is_absolute():
                    requested = allowed_base / requested

                # Resolve to collapse '..' and symlinks
                requested = requested.resolve()

                # Ensure requested path is inside allowed_base
                try:
                    is_allowed = requested.is_relative_to(allowed_base)
                except AttributeError:
                    # Python < 3.9 fallback
                    is_allowed = (
                        str(requested).startswith(str(allowed_base) + os.sep)
                        or requested == allowed_base
                    )

                if not is_allowed:
                    skipped.append({"path": raw, "reason": "path not allowed"})
                    continue

                # Refuse deleting the base temp dir itself
                if requested == allowed_base:
                    skipped.append({"path": raw, "reason": "refusing to delete base temp dir"})
                    continue

                if requested.is_dir():
                    shutil.rmtree(str(requested))
                    deleted.append(str(requested))
                else:
                    skipped.append({"path": raw, "reason": "not a directory"})

            except Exception as e:
                logger.exception("Failed deleting temp directory %r", raw)
                errors.append({"path": raw, "error": str(e)})

        status = 207 if errors else 200
        return JsonResponse(
            {"deleted": deleted, "skipped": skipped, "errors": errors},
            status=status,
        )

    except Exception as e:
        logger.exception("remove_temp_directories unexpected failure: %s", e)
        return JsonResponse({"error": f"Unexpected error: {e}"}, status=400)

@require_POST
@login_required()
def get_temp_output_subdirectories(request, conn=None, **kwargs) -> JsonResponse:
    """
    Return immediate subdirectories of a given temp_output directory.

    Expects JSON body: { "temp_output": "<path>" }

    Security:
    - user must be logged in
    - only allows paths within JIPIPE_TEMP_DIR
    - lists only immediate subdirectories (no recursion)

    URL: J2O/get_temp_output_subdirectories/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional)
    """
    try:
        # Parse JSON body
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"error": "Invalid JSON body"}, status=400)

        raw_path = payload.get("temp_output", "")
        if not raw_path:
            return JsonResponse({"error": "Missing required field: temp_output"}, status=400)

        allowed_base = Path(JIPIPE_TEMP_DIR).resolve()
        if not allowed_base.exists() or not allowed_base.is_dir():
            return JsonResponse({"error": "Server misconfiguration: JIPIPE_TEMP_DIR invalid"}, status=400)

        requested = Path(raw_path)

        # If temp_output is relative, interpret relative to allowed base
        if not requested.is_absolute():
            requested = allowed_base / requested

        # Resolve to remove '..' etc.
        requested = requested.resolve()

        # Ensure requested path is inside allowed_base
        try:
            is_allowed = requested.is_relative_to(allowed_base)
        except AttributeError:
            # Python < 3.9 fallback
            is_allowed = str(requested).startswith(str(allowed_base) + os.sep) or requested == allowed_base

        if not is_allowed:
            return JsonResponse({"error": "Path not allowed"}, status=403)

        if not requested.exists() or not requested.is_dir():
            return JsonResponse({"error": "Temp output directory not found"}, status=400)

        # List immediate subdirectories
        subdirs = []
        for name in os.listdir(str(requested)):
            full_path = requested / name
            if full_path.is_dir():
                subdirs.append(name)

        subdirs.sort()
        return JsonResponse({"subdirectories": subdirs})

    except Exception as error:
        logger.exception("Failed to list temp output subdirectories: %s", error)
        return JsonResponse({"error": f"Error retrieving temp output subdirectories: {error}"}, status=400)

@require_POST
@login_required()
def save_input_to_server(request, conn=None, **kwargs):
    try:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        out_dir = payload['path']
        input_key = payload['input_key']

        ids = [int(x.strip()) for x in payload['ids'].split(",")]

        if conn is None:
            return JsonResponse({'error': 'No OMERO connection'}, status=401)

        os.makedirs(out_dir, exist_ok=True)

        saved_files = 0
        processed_files = 0
        saves = []
        errors = []
        destination_path_s = []

        # Code for one folder structures (input are dataset ids)
        if input_key == "folder-path":
            destination_path_s = out_dir
            for dataset_id in ids:
                dataset = conn.getObject("Dataset", dataset_id)
                if dataset is None:
                    return JsonResponse({'error': f'Dataset {dataset_id} not found'}, status=404)

                for img in dataset.listChildren():
                    processed_files += 1
                    fileset = img.getFileset()
                    if fileset is None:
                        errors.append({'image_id': img.id, 'error': 'Image has no Fileset (no original files to save).'})
                        continue

                    # Mirror the CLI `omero download Image:<id> <dir>` behavior:
                    # keep the relative path under the Fileset’s template prefix.
                    template_prefix = fileset.getTemplatePrefix() or ""

                    for ofile in fileset.listFiles():
                        try:
                            rel_dir = ofile.path.replace(template_prefix, "", 1)
                            target_dir = os.path.join(out_dir, rel_dir)
                            os.makedirs(target_dir, exist_ok=True)

                            target_path = os.path.join(target_dir, ofile.name)

                            if not os.path.exists(target_path):
                                # `conn.c.download(OriginalFile, local_path)` saves the binary
                                conn.c.download(ofile._obj, target_path)
                                saved_files += 1

                            saves.append({
                                'image_id': img.id,
                                'file_name': ofile.name,
                                'saved_to': target_path
                            })

                        except Exception as e:
                            errors.append({
                                'image_id': img.id,
                                'file_name': getattr(ofile, 'name', None),
                                'error': str(e)
                            })

        # Code for multiple folders structures (input are datasets ids)
        if input_key == "folder-paths":
            for dataset_id in ids:

                dataset_dir = os.path.join(out_dir, str(dataset_id))
                os.makedirs(dataset_dir, exist_ok=True)

                dataset = conn.getObject("Dataset", dataset_id)
                if dataset is None:
                    return JsonResponse({'error': f'Dataset {dataset_id} not found'}, status=404)

                for img in dataset.listChildren():
                    processed_files += 1
                    fileset = img.getFileset()
                    if fileset is None:
                        errors.append({'image_id': img.id, 'error': 'Image has no Fileset (no original files to save).'})
                        continue

                    # Mirror the CLI `omero download Image:<id> <dir>` behavior:
                    # keep the relative path under the Fileset’s template prefix.
                    template_prefix = fileset.getTemplatePrefix() or ""

                    for ofile in fileset.listFiles():
                        try:
                            rel_dir = ofile.path.replace(template_prefix, "", 1)
                            target_dir = os.path.join(dataset_dir, rel_dir)
                            os.makedirs(target_dir, exist_ok=True)

                            target_path = os.path.join(target_dir, ofile.name)

                            if not os.path.exists(target_path):
                                # `conn.c.download(OriginalFile, local_path)` saves the binary
                                conn.c.download(ofile._obj, target_path)
                                saved_files += 1
                                destination_path_s.append(target_dir)

                            saves.append({
                                'image_id': img.id,
                                'file_name': ofile.name,
                                'saved_to': target_path
                            })

                        except Exception as e:
                            errors.append({
                                'image_id': img.id,
                                'file_name': getattr(ofile, 'name', None),
                                'error': str(e)
                            })

        # Code for one folder structures (input are OriginalFile ids)
        if input_key == "file-name":
            for file_id in ids:
                # Get the OriginalFile object directly
                original_file = conn.getObject("OriginalFile", file_id)
                if original_file is None:
                    return JsonResponse({'error': f'OriginalFile with ID {file_id} not found'}, status=404)

                # Get the filename directly from the OriginalFile object
                file_name = original_file.getName() or original_file.name or f"file_{file_id}"
                
                # Create target directory
                target_dir = out_dir
                os.makedirs(target_dir, exist_ok=True)
                
                target_path = os.path.join(target_dir, file_name)
                
                try:
                    if not os.path.exists(target_path):
                        # Download the file using conn.c.download
                        conn.c.download(original_file._obj, target_path)
                        saved_files += 1
                        destination_path_s = target_path

                    saves.append({
                        'file_id': file_id,
                        'file_name': file_name,
                        'saved_to': target_path
                    })
                    processed_files += 1

                except Exception as e:
                    errors.append({
                        'file_id': file_id,
                        'file_name': file_name,
                        'error': str(e)
                    })

        # Code for one folder structures (input are OriginalFile ids)
        if input_key == "file-names":
            for file_id in ids:
                # Get the OriginalFile object directly
                original_file = conn.getObject("OriginalFile", file_id)
                if original_file is None:
                    return JsonResponse({'error': f'OriginalFile with ID {file_id} not found'}, status=404)

                # Get the filename directly from the OriginalFile object
                file_name = original_file.getName() or original_file.name or f"file_{file_id}"
                
                # Create target directory
                target_dir = out_dir
                os.makedirs(target_dir, exist_ok=True)
                
                target_path = os.path.join(target_dir, file_name)
                
                try:
                    if not os.path.exists(target_path):
                        # Download the file using conn.c.download
                        conn.c.download(original_file._obj, target_path)
                        saved_files += 1
                        destination_path_s.append(target_path)

                    saves.append({
                        'file_id': file_id,
                        'file_name': file_name,
                        'saved_to': target_path
                    })
                    processed_files += 1

                except Exception as e:
                    errors.append({
                        'file_id': file_id,
                        'file_name': file_name,
                        'error': str(e)
                    })

        status = 200 if not errors else 207  # 207 = partial success
        return JsonResponse({
            'ids': ids,
            'input_key': input_key,
            'processed_files': processed_files,
            'saved_files': saved_files,
            'saves': saves,
            'errors': errors, 
            'destination_path_s': destination_path_s
        }, status=status)

    except KeyError as e:
        return JsonResponse({'error': f'Missing key {e} in request JSON'}, status=400)
    except BaseException as e:
        return JsonResponse({'error': f'{e}', 'trace': traceback.format_exc()}, status=500)
    
@require_POST
@login_required()
def save_to_omero(request, conn=None, **kwargs):
    """
    POST JSON:
    {
      "path": "/abs/path/to/dir",
      "project_id": 123,
      "dataset_name": "My New Dataset",   // optional; defaults to basename(path)
      "recursive": true,                  // optional (default true)
      "patterns": ["*.tif","*.czi"],     // optional; omit = all files
    }
    Behavior:
      - Creates (or reuses) Dataset in Project
      - Imports files as Images into that Dataset using embedded CLI bound to current conn
      - No session ID string and no username/password required
      - Uses per-call Ice context to set the group (works across OMERO versions)
    """
    DEFAULT_PROJECT_NAME = "JipipeResultsDefault"
    try:
        # Parse body
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception as e:
            return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

        root_dir     = payload["path"]
        project_id   = int(payload["project_id"])
        dataset_name = payload.get("dataset_name") or os.path.basename(os.path.abspath(root_dir)) or "Imported Dataset"
        recursive    = bool(payload.get("recursive", True))
        patterns     = payload.get("patterns")
        log_path = payload.get("log_file_path")

        if conn is None:
            return JsonResponse({"error": "No OMERO connection"}, status=401)
        if not os.path.isdir(root_dir):
            return JsonResponse({"error": f"Path not found or not a directory: {root_dir}"}, status=400)

        # Ensure project exists
        project = conn.getObject("Project", project_id)
        if project is None:
            # Try to find an existing default project by name (in current group context)
            default_project = next(conn.getObjects("Project", attributes={'name': DEFAULT_PROJECT_NAME}), None)

            if default_project is None:
                # Create a new default project
                pr = ProjectI()
                pr.setName(rstring(DEFAULT_PROJECT_NAME))
                pr = conn.getUpdateService().saveAndReturnObject(pr)  # saved in current group/security context
                project = conn.getObject("Project", pr.id.val)        # wrap as gateway object
            else:
                project = default_project
        
            # From here on, use this project
            project_id = project.getId()

        # ---- Per-call Ice context: force saves into the project's group ----
        gid = project.getDetails().group.id.val
        ctx = {"omero.group": str(gid)}  # <- key bit; no SecurityContext/ServiceOptions needed
        u = conn.c.sf.getUpdateService()  # raw proxy so we can pass _ctx explicitly
        # -------------------------------------------------------------------

        # Ensure (or create) dataset in project using per-call context
        ds_obj = None
        for d in project.listChildren():
            if d.getName() == dataset_name:
                ds_obj = d
                break
        if ds_obj is None:
            ds_m = omodel.DatasetI()
            ds_m.setName(rstring(dataset_name))
            ds_m.setDescription(rstring(f"Bulk import from {root_dir}"))
            ds_m = u.saveAndReturnObject(ds_m, _ctx=ctx)

            link = omodel.ProjectDatasetLinkI()
            link.setParent(project._obj)
            link.setChild(ds_m)
            u.saveAndReturnObject(link, _ctx=ctx)

            dataset_id = ds_m.id.val
        else:
            dataset_id = ds_obj.getId()


        # ---- Attach log.txt to the Dataset using ONLY raw services + the same ctx ----
        attached = []

        if log_path and os.path.isfile(log_path):
            basename = os.path.basename(log_path)

            # 0) Check if a FileAnnotation with the same OriginalFile name is already linked to the Dataset
            q = conn.getQueryService()
            params = omero.sys.ParametersI()
            params.addId(dataset_id)
            params.addString("fname", basename)

            hql = """
                select fa
                from DatasetAnnotationLink dal
                join dal.child fa
                join fa.file f
                where dal.parent.id = :id
                and f.name = :fname
            """

            existing = q.findAllByQuery(hql, params, _ctx=ctx)
            if existing and len(existing) > 0:
                # A file with the same name is already attached to this Dataset; do nothing.
                # (If you prefer, you could log/print a message here.)
                pass
            else:
                # 1) Create an OriginalFile in the target group
                of = omodel.OriginalFileI()
                of.setName(rstring(os.path.basename(log_path)))
                of.setPath(rstring("/"))  # logical server-side path; avoid local absolute paths here
                of.setSize(rlong(os.path.getsize(log_path)))
                of.setMimetype(rstring("text/plain"))
                of = u.saveAndReturnObject(of, _ctx=ctx)  # IMPORTANT: saved with proper group

                # 2) Upload bytes via RawFileStore (note: use createRawFileStore)
                rfs = conn.c.sf.createRawFileStore()
                try:
                    rfs.setFileId(of.getId().getValue(), _ctx=ctx)
                    with open(log_path, "rb") as fh:
                        offset = 0
                        while True:
                            chunk = fh.read(64 * 1024)
                            if not chunk:
                                break
                            rfs.write(chunk, offset, len(chunk), _ctx=ctx)
                            offset += len(chunk)
                    rfs.save(_ctx=ctx)
                finally:
                    rfs.close()

                # 3) Create FileAnnotation referencing the OriginalFile
                fa = omodel.FileAnnotationI()
                fa.setFile(omodel.OriginalFileI(of.getId().getValue(), False))
                fa.setNs(rstring("omero.jipipe/log"))  # optional namespace
                fa = u.saveAndReturnObject(fa, _ctx=ctx)

                # 4) Link FileAnnotation -> Dataset
                dal = omodel.DatasetAnnotationLinkI()
                dal.setParent(omodel.DatasetI(dataset_id, False))
                dal.setChild(omodel.FileAnnotationI(fa.getId().getValue(), False))
                u.saveAndReturnObject(dal, _ctx=ctx)
                attached.append({"file": log_path, "type": "attachment"})
            # -----------------------------------------------------------------------------

        # Gather candidate files
        candidates = _gather_files(root_dir, recursive=recursive, patterns=patterns)
        if not candidates:
            # Still return success (no error)
            return JsonResponse({
                "ok": True,
                "nothing_to_upload": True,
                "message": "No matching files found; nothing uploaded.",
                "project_id": project_id,
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "root_dir": root_dir,
                "recursive": recursive,
                "patterns": patterns,
                "files_considered": 0,
                "files_imported": 0,
                "files_attached": len(attached),   # log attachment count if any
                "files_skipped": 0,
                "results": attached,               # include log attachment results if you made them
                "errors": [],
                "stdout": "",
                "stderr": "",
            }, status=200)
        
        # Decide what we consider importable image files
        IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".dv", ".czi", ".nd2", ".lif", ".ics", ".ids", ".svs", ".mrxs"}
        files_for_import = [p for p in candidates if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
        files_for_attach = [p for p in candidates if p not in files_for_import]

        results, errors = [], []

        if files_for_attach:
            for apath in files_for_attach:
                try:
                    of = omodel.OriginalFileI()
                    of.setName(rstring(os.path.basename(apath)))
                    of.setPath(rstring("/"))
                    of.setSize(rlong(os.path.getsize(apath)))
                    mt, _ = mimetypes.guess_type(apath)
                    of.setMimetype(rstring(mt or "application/octet-stream"))
                    of = u.saveAndReturnObject(of, _ctx=ctx)

                    rfs = conn.c.sf.createRawFileStore()
                    try:
                        rfs.setFileId(of.getId().getValue(), _ctx=ctx)
                        with open(apath, "rb") as fh:
                            offset = 0
                            while True:
                                chunk = fh.read(64 * 1024)
                                if not chunk:
                                    break
                                rfs.write(chunk, offset, len(chunk), _ctx=ctx)
                                offset += len(chunk)
                        rfs.save(_ctx=ctx)
                    finally:
                        rfs.close()

                    fa = omodel.FileAnnotationI()
                    fa.setFile(omodel.OriginalFileI(of.getId().getValue(), False))
                    fa.setNs(rstring("omero.jipipe/attachment"))
                    fa = u.saveAndReturnObject(fa, _ctx=ctx)

                    dal = omodel.DatasetAnnotationLinkI()
                    dal.setParent(omodel.DatasetI(dataset_id, False))
                    dal.setChild(omodel.FileAnnotationI(fa.getId().getValue(), False))
                    u.saveAndReturnObject(dal, _ctx=ctx)

                    attached.append({"file": apath, "type": "attachment"})
                except Exception as e:
                    errors.append({"file": apath, "error": f"attach failed: {e}"})

        imported = 0

        if files_for_import:
            base_args = [
                "import",
                "-g", str(gid),
                "-T", f"Dataset:{dataset_id}",
                "--no-upgrade-check",
            ]
            full_args = base_args + files_for_import

            buf_out, buf_err = io.StringIO(), io.StringIO()
            try:
                cli = CLI()
                cli.loadplugins()
                cli.set_client(conn.c)
                with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                    cli.invoke(full_args, strict=True)

                for fpath in files_for_import:
                    results.append({"file": fpath, "type": "image"})
                    imported += 1

            except NonZeroReturnCode as nz:
                # Surface BOTH streams so we don't lose the real message
                err_text = "\n".join([buf_err.getvalue().strip(), buf_out.getvalue().strip()]).strip()
                return JsonResponse({
                    "error": err_text or f"OMERO import failed (exit code {getattr(nz, 'returncode', '?')})",
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "root_dir": root_dir,
                    "recursive": recursive,
                    "patterns": patterns,
                    "files_for_import": files_for_import,
                    "files_for_attach": files_for_attach,
                }, status=500)
            except Exception as e:
                return JsonResponse({"error": str(e), "trace": traceback.format_exc()}, status=500)
        else:
            # No images to import; that's fine if we attached something
            pass

        status = 200 if not errors else 207
        return JsonResponse({
            "project_id": project_id,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "root_dir": root_dir,
            "recursive": recursive,
            "patterns": patterns,
            "files_considered": len(candidates),
            "files_imported": imported,
            "files_attached": len(attached),
            "files_skipped": len(candidates) - imported - len(attached),
            "results": results + attached,
            "errors": errors,
            "stdout": (buf_out.getvalue().strip() if files_for_import else ""),
            "stderr": (buf_err.getvalue().strip() if files_for_import else ""),
        }, status=status)

    except KeyError as e:
        return JsonResponse({"error": f"Missing key {e} in request JSON"}, status=400)
    except BaseException as e:
        return JsonResponse({"error": f"{e}", "trace": traceback.format_exc()}, status=500)

def _gather_files(root_dir, recursive=True, patterns=None):
    if not recursive:
        files = [os.path.join(root_dir, f) for f in os.listdir(root_dir)
                 if os.path.isfile(os.path.join(root_dir, f))]
        if patterns:
            files = [p for p in files
                     if any(fnmatch.fnmatch(os.path.basename(p), pat) for pat in patterns)]
        return list(dict.fromkeys(files))
    out = []
    for base, _, fs in os.walk(root_dir):
        for f in fs:
            if not patterns or any(fnmatch.fnmatch(f, pat) for pat in patterns):
                out.append(os.path.join(base, f))
    return list(dict.fromkeys(out))