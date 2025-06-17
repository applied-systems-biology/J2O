from datetime import datetime
import json
import logging
import os
import signal
import uuid
from typing import Optional
import traceback

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from JIPipePlugin.celery import app
from JIPipeRunner.tasks import run_jipipe_task
from celery.result import AsyncResult

import omero
import omero.model
from omero.rtypes import rstring
from omeroweb.decorators import login_required


# Directory where JIPipe log files are stored (customize via Django settings)
LOG_DIR = getattr(settings, 'JIPIPE_LOG_ROOT', '/tmp/jipipe_logs')
os.makedirs(LOG_DIR, exist_ok=True)

# Time (in seconds) to keep PIDs in cache before expiring (None == never expire)
CACHE_TIMEOUT: Optional[int] = None

# Intialize the logger
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
        log_file = os.path.join(LOG_DIR, f'{job_uuid}.log')

        # Collect job information to store in the cache
        job_info = {
        "job_uuid": job_uuid,
        "name": jip_file_name,
        "start_time": datetime.now().strftime('%d-%m-%Y %H:%M:%S'),
        "log_file_path": log_file
        }

        # Update the cache to track active jobs for the user
        owner = conn.getUser().getName()
        logger.info(f"Starting JIPipe job for user {owner} with job ID {job_uuid}")
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        active.append(job_info)
        cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        # Launch the background thread to run the JIPipe task using Celery and attach the unique job ID for reference
        run_jipipe_task.apply_async(
            args=[jipipe_json, parameter_override_json, job_uuid, owner, log_file],
            task_id=job_uuid,
            ignore_result=True,
        )
        return JsonResponse({'job_id': job_uuid, 'job_name': jip_file_name, 'job_start_time': datetime.now().strftime('%d-%m-%Y %H:%M:%S')})
    
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
            return JsonResponse({'error': 'Missing job_id'}, status=400)
        
        # Get the current user and their active jobs from cache to verify ownership
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        job_exists = any(job["job_uuid"] == job_uuid for job in active)
        if not job_exists:
            return JsonResponse({'error': 'Job not found or not owned by you'}, status=404)

        # Revoke the Celery task (terminate immediately with SIGTERM)
        result = AsyncResult(job_uuid)
        result.revoke(terminate=True, signal=signal.SIGTERM)

        # Remove the job from the active jobs cache after successful revoke
        active = [job for job in active if job["job_uuid"] != job_uuid]
        cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({'status': 'terminated', 'job_id': job_uuid})

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

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
        log_file = os.path.join(LOG_DIR, f'{job_uuid}.log')
        
        # Raise an error if the log file does not exist
        if not os.path.exists(log_file):
            raise Http404(f'Job not found: {job_uuid}')

        # Get the log lines from the file to return them
        with open(log_file, 'r') as file_handle:
            log_lines = file_handle.read().splitlines()
        
        # Check if the job is still active by looking in the cache
        owner = conn.getUser().getName()
        user_key = f"active_jipipe_jobs_{owner}"
        active = cache.get(user_key, [])
        active_job_uuid_list = [job["job_uuid"] for job in active]

        # Determine if the job finished by checking the exit code message or if it is still in the active set
        finished = any('JIPipe exited with code' in line for line in log_lines[-3:]) or (job_uuid not in active_job_uuid_list)
        status = 'finished' if finished else 'running'

        # Remove the job from active cache if it has finished but not removed yet
        if finished and job_uuid in active_job_uuid_list:
            active = [job for job in active if job["job_uuid"] != job_uuid]
            cache.set(user_key, active, timeout=CACHE_TIMEOUT)

        return JsonResponse({'status': status, 'logs': log_lines})
    
    except Exception as parse_error:
        logger.exception('Failed to retrieve jipipe log: %s', parse_error)
        return HttpResponse(
            f'Error retrieving jipipe log: {parse_error}',
            status=400,
        )

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
    # Get the .jip file from OMERO using the provided file ID
    jip_file = conn.getObject('originalfile', jip_file_id)

    # If the file does not exist, return a 404 error
    if jip_file is None or not jip_file.getName().endswith('.jip'):
        return HttpResponse(f'.jip file with ID {jip_file_id} not found.', status=404)
    
    try:
        # Read and parse the JSON data from the annotation
        raw_bytes = b''.join(jip_file.getFileInChunks())
        config_text = raw_bytes.decode('utf-8')
        config_data = json.loads(config_text)
    except Exception as parse_error:
        logger.error('Failed to parse JIPipe JSON: %s', parse_error)
        return HttpResponse(
            f'Error parsing JIPipe JSON: {parse_error}',
            status=400,
        )

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
        logger.exception("Failed to list JIPipe files")
        return JsonResponse(
            {'error': f'Internal server error retrieving JIPipe files: {e}'},
            status=500
        )
    
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
        logger.exception("Failed to list available datasets")
        return JsonResponse(
            {'error': f'Internal server error listing available datasets: {e}'},
            status=500
        )
    
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

    # Get the ID of the newly created project and return the Project object
    new_id = saved_model.getId().getValue()
    return conn.getObject('Project', new_id)
