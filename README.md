
# JIPipeRunner documentation

JIPipeRunner is a plugin for [omero-web](https://github.com/ome/omero-web) that makes it possible to run [JIPipe](https://jipipe.hki-jena.de/) workflows directly on the server that is hosting the OMERO database. This eliminates the need for users to share their data and workflows outside of OMERO and greatly reduces the data traffic as well as aiding reproducibility.

## License & Attribution

Marius Wank, Ruman Gerst, Marc Thilo Figge

Research Group Applied Systems Biology - Head: Prof. Dr. Marc Thilo Figge\
https://www.leibniz-hki.de/en/applied-systems-biology.html \
HKI-Center for Systems Biology of Infection\
Leibniz Institute for Natural Product Research and Infection Biology - Hans Knöll Institute (HKI)\
Adolf-Reichwein-Straße 23, 07745 Jena, Germany

This plugin is licensed under the **Creative Commons Attribution 4.0 International License (CC BY 4.0)**.  
You are free to share and adapt it with proper attribution.  
See: [CC BY 4.0 License](https://creativecommons.org/licenses/by/4.0/)

### Dependencies & Third-Party Tools

- **Tom Select** (UI select widget)  
  Licensed under the [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0)

### Required Software

This plugin is designed to work with **JIPipe**, developed by **Ruman Gerst and Zoltán Csereynes**.  
> ⚠️ JIPipe is **not** included in this plugin’s distribution. Please visit [jipipe.org](https://jipipe.org) for download and license details.

## Requirements

- Python 3.10
- omero-web 5.28
- Django 4.2
- JIPipe 5.0 or later
- XVFB
- Celery 
- redis

## Installation

This section assumes that [omero-web](https://github.com/ome/omero-web) is already setup with redis caching and that JIPipe has been installed on the server via ImageJ as described by the [JIPipe documentation for manual installation](https://jipipe.hki-jena.de/documentation/manual-installation.html). If the plugin has trouble finding the ImageJ executable, double check if the application is installed to the server and that the environment variable **OMERODIR** is set as described in the [OMERO.web installation guide](https://docs.openmicroscopy.org/omero/5.6.0/sysadmins/unix/install-web/web-deployment.html). This is also important for the redis caching to work properly.

### Step 1

Within your [omero-web](https://github.com/ome/omero-web) virtual environment, install the plugin using [pip](https://pip.pypa.io/en/stable/):
```bash
pip install git+https://asb-git.hki-jena.de/MWank/OMERO_JIPipe_Plugin.git
```

### Step 2

Add "JIPipeRunner" to the list of installed apps using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.apps '"JIPipeRunner"'
```

### Step 3

Add the plugin to the right panel plugins using [omero-web](https://github.com/ome/omero-web):
```bash
omero config append omero.web.ui.right_plugins '["JIPipeRunner", "JIPipeRunner/right_plugin_example.js.html", "jipipe_form_container"]
```

### Step 4

Set the path to the ImageJ executable from the [manual installation of JIPipe](https://jipipe.hki-jena.de/documentation/manual-installation.html) using [omero-web](https://github.com/ome/omero-web):
```bash
omero config set omero.web.imagej "/path/to/ImageJ"
```

### Step 5

Restart [omero-web](https://github.com/ome/omero-web) for the changes to take effect:
```bash
omero web restart
```

Should there be any errors please regarding the webclient plugin installation refer to the official [OMERO WebclientPlugin documentation](https://docs.openmicroscopy.org/omero/5.6.3/developers/Web/WebclientPlugin.html).

### Step 6

Launch a Celery worker that will manage the jobs launched by the JIPipeRunner Celery app using:
```bash
celery -A JIPipePlugin worker --loglevel=info
```

The worker will use the redis cache as a backend that is defined in the OMERO settings, so be sure to have followed the [OMERO.web installation guide](https://docs.openmicroscopy.org/omero/5.6.0/sysadmins/unix/install-web/web-deployment.html) to include redis caching and define the OMERODIR environment variable correctly. JIPipeRunner will use your default cache backend location at:

```text
omero.web.caches = {
  "default": {
    "BACKEND": "django_redis.cache.RedisCache",
    "LOCATION": "redis://127.0.0.1:6379/0"
  }
}
```

## User guide

After the installation is completed, you can login to your OMERO server. If the installation was successful, you should see a tab called **JIPipeRunner** in the right panel. 

<p align="center">
  <img src="./assets/images/TabPanel.png"/>
</p>

Below you will find a detailed explanation for all sections displayed within the plugin.

### FILE SELECTION

The plugin will load its content depending on the .jip file you select at the top of this section. When checking **Enable output config**, the [output node configuration](#output-node-configuration) will be accessible.

<p align="center">
  <img src="./assets/images/FileSelectionSection.png"/>
</p>

JIPipeRunner will automatically offer you all .jip files as an option that are accessible by your OMERO groups. If you have none available, you can upload files as attachments to any of your projects or datasets. To do so, select a dataset or project and go to <b>General → Attachments</b> in the right panel. Click the <b>+</b> to attach a .jip file. To ensure compatibility, be sure that it adheres to the <a href="#pipeline-design-constraints">pipeline design constraints</a>.

<p align="center">
  <img src="./assets/images/Attach_JIP_File.png"/>
</p>


### RUNNING JOBS

In this section you will find a list of all the JIPipe jobs currently running on the server that were initiated by the current user. They can be identified by the time and date of execution and the name of the associated .jip file. By clicking the red ✖ next to the entry you can terminate the associated job.

![Job section](./assets/images/RunningJobsSection.png)

### NODE SUMMARY

In this section you will find an overview of the nodes detected in the associated .jip file. This can be used as a debugging tool to see whether the JIPipe pipeline was constructed according to the [pipeline design constraints](#pipeline-design-constraints) and JIPipeRunner therefore automatically detects the right amount of nodes.

![Node summary section](./assets/images/NodeSummarySection.png)

### INPUT NODE CONFIGURATION

If the JIPipe pipeline follows the [pipeline design constraints](#pipeline-design-constraints), this section will allow to enter the IDs of the datasets that contain the input images. On clicking the input field, a scrollable dropdown menu will be shown that lists all available datasets according to your OMERO group. You can simply click a listed dataset to add it to the input field. You can also search for a specific dataset by typing its name into the input field. To remove an entry from the input field, click the ✖ next to the ID.

![Input node config section](./assets/images/InputNodeConfigSection.png)

### OUTPUT NODE CONFIGURATION

When checking **Enable output config** in the [file selection](#file-selection), the output configuration becomes available. Here you can choose a pre-existing project (the same way as selecting the input dataset ID) that you want to save the generated output dataset to and give the dataset a custom name. The name can be entered in plain text when unchecking **Input as expression**, otherwise an [expression as described in the JIPipe documentation](https://jipipe.hki-jena.de/documentation/expressions.html) can be entered. 

![Output node config section](./assets/images/OutputConfigSection.png)

### PARAMETER CONFIGURATION

This section contains the input fields of the parameters that are defined as reference parameters within the .jip file. Depending on the node and parameter type that is referenced, the input fields accept integers, floats or strings as input. When hovering the **?** the plugin will display a tooltip with the description of the respective parameter (if it was set in the [project overview](https://jipipe.hki-jena.de/documentation/project-overview.html)).

Below this section you will find the **Start JIPipeRunner** button to execute the selected .jip file.

![Parameter config section](./assets/images/ParameterConfigSection.png)

### LOG WINDOW

Below the button that starts the pipeline execution, you will find the log window. During execution, the window will livestream the JIPipe logfile. This can be used to check on the current progress of the execution or to debug problems within the workflow. 

![Log window section](./assets/images/LogWindowSection.png)

## Pipeline design constraints

Since OMERO relies on custom objects rather than a standard filesystem, there are certain constraints in the way the plugin can handle file I/O. To ensure that a JIPipe workflow is compatible with the plugin, it needs to adhere to the design constraints given here.

### Handling login credentials

While you are already logged in when you are working with the plugin in OMERO, there is no functionality (for security reasons) to provide your login credentials to JIPipe. However, since the plugin relies on the JIPipe OMERO nodes that require valid login credentials to work, you need to set your credentials manually within the JIPipe project. Refer to the [official JIPipe OMERO integration page](https://jipipe.hki-jena.de/documentation/omero-integration.html) for more information.

### Input nodes

In the [input node configuration section](#input-node-configuration), JIPipeRunner will only allow you to change the Dataset IDs entry of the "Define dataset IDs" nodes. Therefore, it is crucial that all relevant input that is connected to your workflow uses the following node structure:

<p align="center">
  <img src="./assets/images/InputStructure.png" style="height:300px"/>
</p>


### Reference parameter configuration

To prevent the display of all possible node parameters of a pipeline within the plugin, the creator of the pipeline must specify the parameters that should be changeable as reference parameters in the [project overview](https://jipipe.hki-jena.de/documentation/project-overview.html) within JIPipe. If none are specified, the [parameter configuration section](#parameter-configuration) will be empty and the pipeline can only be executed as is.

![Reference Parameters](./assets/images/ReferenceParameters.png)

### Output nodes

When executed with an unchecked **Enable output config**, JIPipeRunner will automatically create a new project within the OMERO database called "JIPipeResults" or use a pre-existing project with the same name. Otherwise, the user input in the [output node configuration section](#output-node-configuration) will be used.

For a pipeline to store its results in a dataset within a project, it is crucial that the output that should be stored in OMERO is connected to the following node structure within the pipeline: 

<p align="center">
  <img src="./assets/images/OutputStructure.png" style="height:300px"/>
</p>

Note that the upload node needs to be connected to the output and that there are different upload nodes depending on the output type. You don't actually have to change any of the parameters within these nodes, as JIPipeRunner will fill them for you.