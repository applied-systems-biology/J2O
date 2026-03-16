# Troubleshooting

This page covers common issues that can arise during installation or usage of J2O.

> If you can't find your issue here, feel free to contact us or open an issue in the [community](https://image.sc).

## Error starting JIPipe task

This error can occur when you have entered all parameters and press the "Start J2O" button. Multiple issues can be the cause for this depending on the exact error message.

### Could not start job. No Celery workers are currently running.

This issue arises when there is no Celery worker attached to the plugin. This issue must be resolved by your system administrator and may be caused by a faulty installation.

#### Solution

As described in the [manual installation guide](ManualInstallationGuide.md), you can start a Celery worker  in your omero-web environment like this:

```bash
celery -A JIPipePlugin worker --loglevel=info -E --detach
```

To terminate workers associated with the plugin, simply run this command:
```bash
celery -A JIPipePlugin control shutdown
```

## Error fetching logs

Usually this error occurs when the directory where the log files are stored is not readable and/or writable by the omero-web user. This issue must be resolved by your system administrator.

### Solution 1: Changing the log directory ownership

Change the ownership of the log directory. You can find the path with this command:

```bash
omero config get omero.web.jipipe.logdir
```

If nothing is displayed, the log directory default is ~/j2o-files/logs. By default, the home directory of the omero-web user should be accessible by it but if it is not, you can change ownership as root by executing:

```bash
chown omero-web:omero-web path/to/directory
```

### Solution 2: Move the log directory

As described in the [optional settings section](Optional-settings.md), you can change the directory where log files are stored to a directory that is owned by the omero-web users:

```bash
omero config set omero.web.jipipe.logdir path/to/owned/directory
```

## User directories are not set up correctly

A warning will be displayed if the user directories are not set up as described in the [workflow preparation guide](WorkflowDesign.md). If you ignore the warnings and execute the workflow there may be multiple possible issues.

### Solution 1: Change the key value of user directories

The user directories used in workflows for J2O must contain either "input" or "output" in their key. If the key contains both, there may be unexpected behavior. If the key contains neither, the pipeline will fail. Check the original file and [change the user directories](WorkflowDesign.md#create-user-directories).

## J2O gets stuck when exporting data

This error can occur when the name of the file you are exporting is too long. In JIPipe, a too long name will trigger an interactive window. Since J2O runs on a virtual frame buffer, this does not terminate the process. However, since there is no actual GUI on the server, the process will get stuck as no new name is chosen. 

### Solution 1: Change the file name creation in the workflow

We recommend building you filenames using the JIPipe annotation system. If you use the auto_file_name expression, consider replacing it with a manual concatenation of annotations, as the auto_file_name function uses all annotation and can end up creating a too long name, especially when annotations include nested structures. If you are already using a manual concatenation of annotations, check the length of the annotation string. If some annotations are too long, consider not including them in the filename. If you are using a lot of annotations, consider only using the smallest possible set to identify your data. If you are worried about loosing information by this, consider exporting the annotations as a CSV file instead. 