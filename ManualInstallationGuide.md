# Manual installation guide

**Note that all the commands should be run within your [omero-web](https://github.com/ome/omero-web) virtual environment!**

### Step 0
Docker is required for the plugin to run. So be sure to install it according to the [docker documentation](https://docs.docker.com/engine/install/) before starting the installation process.

Additionally, the omero-web user needs to be part of the docker group. You can test this by running:
```bash
groups omero-web
```

If docker is missing, add it by running:
```bash
sudo usermod -aG docker omero-web
```

and restart to apply:
```bash
sudo systemctl restart omero-web
```

### Step 1
Clone the repository and navigate to the folder:
```bash
git clone https://asb-git.hki-jena.de/MWank/OMERO_JIPipe_Plugin.git
cd OMERO_JIPipe_Plugin
```

### Step 2
Install the plugin using [pip](https://pip.pypa.io/en/stable/):
```bash
pip install .
```
Alternatively, if you want to experiment with the code, install it with the editable flag:
```bash
pip install -e .
```

### Step 3
Install the required python libraries using the requirements.txt:
```bash
pip install -r requirements.txt
```

### Step 4
Setup redis if you have not done so before:
```bash
service redis-server start
omero config set omero.web.caches '{"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "redis://127.0.0.1:6379/0"}}'
```
>⚠️ **Be sure your omero setup does not depend on other caching methods** ⚠️

### Step 5

Add "JIPipeRunner" to the list of installed apps using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.apps '"JIPipeRunner"'
```
**Be sure not to add it twice!**

### Step 6

Add the plugin to the right panel plugins using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.ui.right_plugins '["JIPipeRunner", "JIPipeRunner/right_plugin_example.js.html", "jipipe_form_container"]'
```
**Be sure not to add it twice!**

### Step 7
Start a celery worker to manage started tasks:
```bash
celery -A JIPipePlugin worker --loglevel=info -E --detach
```

Should you wish to terminate the workers associated with the plugin, simply run this in your omero-web environment:
```bash
celery -A JIPipePlugin control shutdown
```

### Step 8
Restart [omero-web](https://github.com/ome/omero-web) for the changes to take effect:
```bash
omero web restart
```