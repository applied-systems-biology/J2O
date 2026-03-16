from django.urls import path
from . import views

urlpatterns = [
    path('j2o_index/', views.j2o_index, name='j2o_index'),
    path('get_jipipe_config/<int:jip_file_id>/', views.get_jipipe_config, name='get_jipipe_config'),
    path("jipipe_start_job/", views.start_jipipe_job, name="jipipe_start_job"),
    path("fetch_jipipe_logs/<str:job_uuid>/", views.fetch_jipipe_logs, name="fetch_jipipe_logs"),
    path("stop_jipipe_job/", views.stop_jipipe_job, name="stop_jipipe_job"),
    path("list_jipipe_jobs/", views.list_jipipe_jobs, name="list_jipipe_jobs"),
    path("list_jipipe_files/", views.list_jipipe_files, name="list_jipipe_files"),
    path("list_available_datasets/", views.list_available_datasets, name="list_available_datasets"),
    path("list_available_files/", views.list_available_files, name="list_available_files"),
    path("list_available_projects/", views.list_available_projects, name="list_available_projects"),
    path("get_latest_jipipe_job/", views.get_latest_jipipe_job, name="get_latest_jipipe_job"),
    path("create_temp_directories/", views.create_temp_directories, name="create_temp_directories"),
    path("create_temp_subdirectories/", views.create_temp_subdirectories, name="create_temp_subdirectories"),
    path("get_temp_output_subdirectories/", views.get_temp_output_subdirectories, name="get_temp_output_subdirectories"),
    path("save_input_to_server/", views.save_input_to_server, name="save_input_to_server"),
    path("save_to_omero/", views.save_to_omero, name="save_to_omero"),
    path("remove_temp_directories/", views.remove_temp_directories, name="remove_temp_directories"),
]