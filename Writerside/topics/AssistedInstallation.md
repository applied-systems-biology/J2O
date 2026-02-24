# Assisted installation

To ease the installation process of the plugin, a bash script is provided in the repository. In case of errors, check the [guide for manual installation](ManualInstallationGuide.md).

This plugin assumes that [omero-web](https://github.com/ome/omero-web) has been setup as described in its [documentation](https://omero.readthedocs.io/en/stable/sysadmins/unix/install-web/web-deployment). 

### Step 1 - Clone the repository
Clone the repository and navigate to the folder:
```bash
git clone https://asb-git.hki-jena.de/MWank/OMERO_JIPipe_Plugin.git
cd OMERO_JIPipe_Plugin
```

### Step 2 - Setup redis as cache backend
>You may ignore this step if you have redis already setup as your caching backend

First, install redis-server and start the service:
```bash
apt-get install -y redis-server
service redis-server start
```
Then, **as the omero-web user**, edit the omero cache config to point to your redis server location:

⚠️ **Be sure your omero setup does not depend on other caching methods** ⚠️

```bash
omero config set omero.web.caches '{"default": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "redis://127.0.0.1:6379/0"}}'
```


### Step 3 - Install J2O to OMERO
Run the bash script as root:
```bash
bash installJ2O.sh
```

The script will ask for input if you have deviated from the default setup used in the [official omero-web installation documentation](https://omero.readthedocs.io/en/stable/sysadmins/unix/install-web/web-deployment). It will check if you have followed the installation process correctly, automatically set the remaining omero config, install J2O via pip, check if redis is reachable, check if podman is working correctly, start a celery background worker and restart omero-web to apply changes if you wish so. 

### After the installation
After running the script, the J2O plugin should be available in the OMERO webclient. To check if everything is functioning, first check if the J2O tab appears in the right panel of the webclient. After that, start a small demo workflow to see if Celery, redis and podman are functioning as expected (normally the script checks this as well).

> If there should be any problems, check the [troubleshooting page](Troubleshooting.md). If you can't find your issue there, feel free to contact us or open an issue in the [community](https://image.sc).