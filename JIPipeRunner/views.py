from datetime import datetime
import json
import logging
import os
from queue import Full
import signal
import uuid
from typing import Optional
import traceback
import tempfile
import contextlib
import fnmatch
import io
import re
import shutil

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_protect

from JIPipePlugin.celery import app
from JIPipeRunner.tasks import run_jipipe_ephemeral
from celery.result import AsyncResult

import omero
import omero.model
from omero.rtypes import rstring
from omeroweb.decorators import login_required
from omero.plugins.export import ExportControl
from omero.cli import CLI, NonZeroReturnCode
import omero.model as omodel
from omero.model import ProjectI


# Directory where JIPipe log files are stored (customize via Django settings)
LOG_DIR = getattr(settings, 'JIPIPE_LOG_ROOT', '/tmp/jipipe/logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Time (in seconds) to keep PIDs in cache before expiring (None == never expire)
CACHE_TIMEOUT: Optional[int] = None

# Initialize the logger
logger = logging.getLogger(__name__)

@login_required()
def jipipe_runner_index(request, conn=None, **kwargs) -> HttpResponse:
    """
    Display the JIPipeRunner HTML.
    """
    return render(
        request,
        'JIPipeRunner/dataset_input.html'
    )

@require_POST
@login_required()
def start_jipipe_job(request, conn=None, **kwargs) -> JsonResponse:
    """
    Start a JIPipe job in the background using Celery.
    Expects a JSON payload containing the .jip file content.
    Returns JSON with the unique job ID of the started job.

    URL: JIPipeRunner/start_jipipe_job/
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
        jipipe_json = json_request.get('jip_content')
        parameter_override_json = json_request.get('jip_parameter_overrides', {})
        jip_file_name = json_request.get('jip_name', 'JIPipeProject.jip')
        custom_output_config_enabled = json_request.get('custom_output_config_enabled', False)
        major_version = json_request.get('major_version')
        temp_input = json_request.get('input_path')
        temp_output = json_request.get('output_path')

        # TODO: Validate the JIPipe JSON structure here for security and correctness

        if not custom_output_config_enabled:
            # Ensure there is a JIPipeResults project to store outputs
            results_project = _get_or_create_results_project(conn)
            results_project_id = int(results_project.getId())

            # Assign dataset IDs of target output dataset to the define-project-ids nodes to save outputs to
            for node in jipipe_json.get('graph', {}).get('nodes', {}).values():
                node_alias_id = node.get('jipipe:alias-id', '').lower()
                if 'define-project-ids' in node_alias_id:
                    node['dataset-ids'] = [results_project_id]

        # Prepare the log file path and unique job identifier to reference the job later on
        job_uuid = uuid.uuid4().hex
        start_time = datetime.now().strftime('%d-%m-%Y_%H:%M:%S')
        log_file = os.path.join(LOG_DIR, f'{start_time}_{jip_file_name}_{job_uuid}.log')

        # Launch the background thread to run the JIPipe task using Celery and attach the unique job ID for reference
        owner = conn.getUser().getName()
        run_jipipe_ephemeral.apply_async(
            args=[jipipe_json, parameter_override_json, job_uuid, owner, log_file, major_version, temp_input, temp_output],
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

        return JsonResponse({'job_id': job_uuid, 'job_name': jip_file_name, 'job_start_time': start_time})
    
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

    URL: JIPipeRunner/stop_jipipe_job/
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
            return JsonResponse({'job_id': None, 'name': None, 'start_time': None})

        # Get the infos for the latest job
        latest_job_time = 0.0
        latest_job_index = -1
        for job_index, job_info in enumerate(active):
            start_time_dt = datetime.strptime(job_info["start_time"], '%d-%m-%Y_%H:%M:%S')
            absolute_start_time = int(start_time_dt.timestamp())

            if absolute_start_time > latest_job_time:
                latest_job_time = absolute_start_time
                latest_job_index = job_index

        return JsonResponse({'job_uuid': active[latest_job_index]["job_uuid"],'name': active[latest_job_index]["name"], 'start_time': active[latest_job_index]["start_time"]})
    
    except Exception as e:
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc(), 'active_jobs': active}, status=500)

@require_GET
@login_required()
def fetch_jipipe_logs(request, job_uuid: str, conn=None, **kwargs) -> JsonResponse:
    """
    Fetch the logs for a specific JIPipe job using its UUID.
    Expects the job UUID as a URL parameter.
    Returns a JSON response with the job status and log lines.
    If the job is not found, returns a 404 error.

    URL: JIPipeRunner/fetch_jipipe_logs/<str:job_uuid>/
    param request: Django HTTP request object
    param job_uuid: Unique identifier for the JIPipe job
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        # Get the log file from LOG_DIR using the job UUID
        log_file = "an/imaginary/path/that/does/not/exist"
        for file in os.listdir(LOG_DIR):
            if job_uuid in file:
                log_file = os.path.join(LOG_DIR, file)
        
        # Raise an error if the log file does not exist
        if not os.path.exists(log_file):
            return JsonResponse({"status": "pending", "logs": []}, status=204)

        # Get the log lines from the file to return them
        with open(log_file, 'r') as file_handle:
            log_lines = file_handle.read().splitlines()
        
        # Check if the job is still active by looking in the cache
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        active_job_uuid_list = [job["job_uuid"] for job in active]

        # Determine if the job finished by checking the exit code message or if it is still in the active set
        status = (
            'finished' if any('Run ending at' in line for line in log_lines[-10:]) or any("JIPipe container exited with code 0" in line for line in log_lines[-1:]) else
            'canceled' if (job_uuid not in active_job_uuid_list) else
            'running'
        )

        # Remove the job from active cache if it has finished but not removed yet
        if status != "running" and job_uuid in active_job_uuid_list:
            active = [job for job in active if job["job_uuid"] != job_uuid]
            cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({'status': status, 'logs': log_lines})
    
    except Exception as error:
        logger.exception('Failed to retrieve jipipe log: %s', error)
        return JsonResponse({'error': f'Error retrieving jipipe log: {error}'}, status=400)

@login_required()
def get_jipipe_config(request, jip_file_id: int, conn=None, **kwargs) -> JsonResponse:
    """
    Fetch the .jip file based on its file ID in OMERO.
    Expects the file ID as a URL parameter.
    Returns a JSON response with the parsed content of the .jip file.

    URL: JIPipeRunner/get_jipipe_config/<jip_file_id>/
    param request: Django HTTP request object
    param jip_file_id: Unique identifier for the JIPipe file in OMERO
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        # Get the .jip file from OMERO using the provided file ID
        jip_file = conn.getObject('originalfile', jip_file_id)

        # If the file does not exist, return a 404 error
        if jip_file is None or not jip_file.getName().endswith('.jip'):
            raise FileNotFoundError(f".jip file with ID {jip_file_id} not found.")
        
        # Read and parse the JSON data from the annotation
        raw_bytes = b''.join(jip_file.getFileInChunks())
        config_text = raw_bytes.decode('utf-8')
        config_data = json.loads(config_text)
    
    except FileNotFoundError as e:
        logger.error(str(e), exc_info=1)
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=404)
    
    except json.JSONDecodeError as parse_error:
        logger.error('Failed to parse JIPipe JSON: %s', parse_error)
        return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=400)

    return JsonResponse(config_data, safe=False)

@login_required()
def list_jipipe_files(request, conn=None, **kwargs) -> JsonResponse:
    """
    List all unique JIPipe-related files attached to projects in 
    all OMERO groups of the current user. 
    Returns a JSON response with file IDs and names.

    URL: JIPipeRunner/list_jipipe_files/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        groups = conn.listGroups()

        # Keep track of which file IDs we’ve already seen
        seen_file_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_jip_annotations = []

        # 3) Loop through each group, switch the service‐opts, and fetch every FileAnnotation
        for group in groups:
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
                if file_name.endswith(".jip"):
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

    URL: JIPipeRunner/list_available_datasets/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        groups = conn.listGroups()

        # Keep track of which file IDs we’ve already seen
        seen_dataset_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_available_datasets = []

        # 3) Loop through each group, switch the service‐opts, and fetch every FileAnnotation
        for group in groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all FileAnnotation objects visible in this group
            # (returns a list of OMERO‐wrappers for FileAnnotation)
            datasets = conn.getObjects("Dataset")
            for dataset in datasets:

                # Extract the numeric ID and the original filename
                dataset_id = dataset.getId()

                # Skip if we've already added this file_id
                if dataset_id in seen_dataset_ids:
                    continue
                seen_dataset_ids.add(dataset_id)

                # The FileAnnotation wrapper has a .getFile() method returning a FileI
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
def list_available_projects(request, conn=None, **kwargs) -> JsonResponse:
    """
    Lists all projects in all OMERO groups of the current user. 
    Returns a JSON response with project_id and project_name.

    URL: JIPipeRunner/list_available_projects/
    param request: Django HTTP request object
    param conn: OMERO connection object (optional, used for user context)
    """
    try:
        original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
        groups = conn.listGroups()

        # Keep track of which file IDs we’ve already seen
        seen_project_ids = set()

        # Prepare a Python list to hold {fileID:…, fileName:…} dicts for .jip annotations
        all_available_projects = []

        # 3) Loop through each group, switch the service‐opts, and fetch every FileAnnotation
        for group in groups:
            # Set the session’s “active group” to gid
            conn.SERVICE_OPTS.setOmeroGroup(group.id)

            # Retrieve all FileAnnotation objects visible in this group
            # (returns a list of OMERO‐wrappers for FileAnnotation)
            projects = conn.getObjects("Project")
            for project in projects:

                # Extract the numeric ID and the original filename
                project_id = project.getId()

                # Skip if we've already added this file_id
                if project_id in seen_project_ids:
                    continue
                seen_project_ids.add(project_id)

                # The FileAnnotation wrapper has a .getFile() method returning a FileI
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

# Helper: ensure the results project exists
def _get_or_create_results_project(conn) -> omero.gateway.ProjectWrapper:
    """
    Ensure that a project named 'JIPipeResults' exists on the 
    OMERO server and in the current group of the active user.
    If it does not exist, create it with a description.
    Returns the Project object if it exists or was created successfully.
    """

    # Define the project name to look for or create
    project_name = 'JIPipeResults'

    # Set a group to save
    original_group_id = conn.SERVICE_OPTS.getOmeroGroup()
    groups = list(conn.listGroups())
    conn.SERVICE_OPTS.setOmeroGroup(groups[0].id)

    # Attempt to find an existing project
    existing_results_project = conn.getObject('Project', attributes={'name': project_name})
    if existing_results_project:
        return existing_results_project

    # Create a new Project with the specified name if it does not exist
    new_project_model = omero.model.ProjectI()
    new_project_model.setName(rstring(project_name))
    new_project_model.setDescription(rstring('Project to save all JIPipe results'))
    saved_model = conn.getUpdateService().saveAndReturnObject(
        new_project_model,
        conn.SERVICE_OPTS,
    )

    conn.SERVICE_OPTS.setOmeroGroup(original_group_id)

    # Get the ID of the newly created project and return the Project object
    new_id = saved_model.getId().getValue()
    return conn.getObject('Project', new_id)

def create_temp_directories(request) -> JsonResponse:
    
    # Create temporary directories for handling input and output
    temp_input = tempfile.mkdtemp()
    temp_output = tempfile.mkdtemp()

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
    try:
        payload = json.loads(request.body.decode("utf-8"))
        dirs = payload.get("temp_directories", [])
        if not isinstance(dirs, list):
            return JsonResponse({"error": "temp_directories must be a list"})
    except Exception:
        return JsonResponse({"error": "Invalid JSON payload"})

    deleted, skipped, errors = [], [], []

    for raw in dirs:
        try:
            path = os.path.abspath(str(raw))

            if os.path.isdir(path):
                shutil.rmtree(path)
                deleted.append(path)
            else:
                skipped.append({"path": raw, "reason": "not a directory"})
        except Exception as e:
            errors.append({"path": raw, "error": str(e)})

    status = 207 if errors else 200
    return JsonResponse({"deleted": deleted, "skipped": skipped, "errors": errors}, status=status)

@require_POST
@login_required()
def download_input(request, conn=None, **kwargs):
    try:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        out_dir = payload['path']
        dataset_id = int(payload['dataset_id'])

        if conn is None:
            return JsonResponse({'error': 'No OMERO connection'}, status=401)

        os.makedirs(out_dir, exist_ok=True)

        dataset = conn.getObject("Dataset", dataset_id)
        if dataset is None:
            return JsonResponse({'error': f'Dataset {dataset_id} not found'}, status=404)

        downloaded_files = 0
        processed_images = 0
        downloads = []
        errors = []

        for img in dataset.listChildren():
            processed_images += 1
            fileset = img.getFileset()
            if fileset is None:
                errors.append({'image_id': img.id, 'error': 'Image has no Fileset (no original files to download).'})
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
                        # `conn.c.download(OriginalFile, local_path)` downloads the binary
                        conn.c.download(ofile._obj, target_path)
                        downloaded_files += 1

                    downloads.append({
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

        status = 200 if not errors else 207  # 207 = partial success
        return JsonResponse({
            'dataset_id': dataset_id,
            'processed_images': processed_images,
            'downloaded_files': downloaded_files,
            'downloads': downloads,
            'errors': errors
        }, status=status)

    except KeyError as e:
        return JsonResponse({'error': f'Missing key {e} in request JSON'}, status=400)
    except BaseException as e:
        return JsonResponse({'error': f'{e}', 'trace': traceback.format_exc()}, status=500)
    

@require_POST
@login_required()
def upload_output(request, conn=None, **kwargs):
    """
    POST JSON:
    {
      "path": "/abs/path/to/dir",
      "project_id": 123,
      "dataset_name": "My New Dataset",   // optional; defaults to basename(path)
      "recursive": true,                  // optional (default true)
      "patterns": ["*.tif","*.czi"],     // optional; omit = all files
      "dry_run": false                    // optional
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
        dry_run      = bool(payload.get("dry_run", False))
        temp_output = payload.get("temp_output")

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

        # Gather candidate files
        candidates = _gather_files(root_dir, recursive=recursive, patterns=patterns)
        if not candidates:
            return JsonResponse({"error": "No matching files to import."}, status=400)

        # Embedded CLI bound to this connection (no creds/session string)
        cli = CLI()
        cli.loadplugins()
        cli.set_client(conn.c)

        results, errors = [], []
        imported = 0
        skipped = 0

        # Import per file; ensure importer runs in same group via -g
        for fpath in candidates:
            args = ["import", "-g", str(gid), "-T", f"Dataset:{dataset_id}", "--no-upgrade-check", fpath]
            if dry_run:
                results.append({
                    "file": fpath,
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "args": " ".join(args),
                    "status": "dry_run"
                })
                skipped += 1
                continue

            buf_out, buf_err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                    cli.invoke(args, strict=True)
                out = buf_out.getvalue()
                imported += 1
                m = re.search(r"Image:(\d+)", out) or re.search(r"Imported\s+image\s+id:(\d+)", out, re.I)
                image_id = int(m.group(1)) if m else None
                results.append({"file": fpath, "image_id": image_id, "stdout": out.strip()})
            except NonZeroReturnCode:
                err = buf_err.getvalue() or buf_out.getvalue()
                errors.append({"file": fpath, "stderr": (err or "").strip()})
            except Exception as e:
                errors.append({"file": fpath, "error": str(e)})

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
            "files_skipped": skipped,
            "results": results,
            "errors": errors
        }, status=status)

    except KeyError as e:
        return JsonResponse({"error": f"Missing key {e} in request JSON"}, status=400)
    except BaseException as e:
        return JsonResponse({"error": f"{e}", "trace": traceback.format_exc()}, status=500)
    finally:
        shutil.rmtree(temp_output)


# Helper function to go through files
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