# Optional settings

This section describes optional settings that can be used by system administrators to customize their J2O installation.

## Changing log and data storage paths
J2O by default uses the home directory of the omero-web user to handle temporary file transfers (e.g. /home/omero-web/j2o-files). You can change these directories using these commands:

```bash
omero config set omero.web.jipipe.tempdir "/path/to/tempdir"
omero config set omero.web.jipipe.logdir "/path/to/logdir"
```

The defined paths don't have to exist yet but will be created by the omero-web user if the permissions exist. Usually changing these should not be necessary. However, if you are experiencing permission errors or want to move the directories for any other reason you can do so. Remember to restart OMERO.web to apply those changes by running:
```bash
omero web restart
```