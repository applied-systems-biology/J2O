# Manual installation

If you had troubles with the assisted installation or want to have a better overview of what operations are performed, this page will help you.

This plugin assumes that [omero-web](https://github.com/ome/omero-web) has been set up as described in its [documentation](https://omero.readthedocs.io/en/stable/sysadmins/unix/install-web/web-deployment).

> Note that these commands should be run in your [omero-web](https://github.com/ome/omero-web) virtual environment!

### Step 1 - Clone the repository
Clone the repository and navigate to the folder:
```bash
git clone https://asb-git.hki-jena.de/MWank/OMERO_JIPipe_Plugin.git
cd OMERO_JIPipe_Plugin
```

### Step 2 - Install J2O
Install the plugin using [pip](https://pip.pypa.io/en/stable/):
```bash
pip install .
```
Alternatively, if you want to experiment with the code, install it with the editable flag:
```bash
pip install -e .
```

### Step 3 - Install python requirements
Install the required python libraries using the requirements.txt:
```bash
pip install -r requirements.txt
```

### Step 4 - Setup podman socket
If not done before, you will need to activate a user systemd socket for podman to work:

```bash
systemctl --user enable --now podman.socket
```

### Step 5 - Setup redis as cache backend
>You may ignore this step if you have redis already setup as your caching backend

First, install redis-server and start the service as the root user:
```bash
apt-get install -y redis-server
service redis-server start
```
Then, as the omero-web system user, edit the omero cache config to point to your redis server location:
```bash
omero config set omero.web.caches '{"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "redis://127.0.0.1:6379/0"}}'
```
⚠️ **Be sure your omero setup does not depend on other caching methods** ⚠️

### Step 6 - Edit OMERO config

Add "J2O" to the list of installed apps using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.apps '"J2O"'
```

Add the plugin to the right panel plugins using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.ui.right_plugins '["J2O", "J2O/right_plugin_example.js.html", "jipipe_form_container"]'
```
⚠️ **Be sure not to add anything twice!** ⚠️

### Step 7 - Start a Celery worker
Start a celery worker to manage started tasks:
```bash
celery -A JIPipePlugin worker --loglevel=info -E --detach
```

Should you wish to terminate the workers associated with the plugin, simply run this in your omero-web environment:
```bash
celery -A JIPipePlugin control shutdown
```

### Step 8 - Restart omero web
Restart [omero-web](https://github.com/ome/omero-web) for the changes to take effect:
```bash
omero web restart
```

### After the installation
The J2O plugin should now be available in the OMERO webclient. To check if everything is functioning, first check if the J2O tab appears in the right panel of the webclient. After that, start a small demo workflow to see if Celery, redis and podman are functioning as expected.

> If there should be any problems, check the [troubleshooting page](Troubleshooting.md). If you can't find your issue there, feel free to contact us or open an issue in the [community](https://image.sc).