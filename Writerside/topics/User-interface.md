# User interface

If installed correctly, the J2O tab can be found in the right panel of the OMERO webclient:

<p align="center">
  <img src="RightPanelTab.png" alt="TabPanel.png"/>
</p>

Below you will find a detailed explanation of the interface sections you will see when opening the plugin.

## FILE SELECTION

J2O will load its content dynamically depending on the file you select at the top of this section. When checking **Enable output config**, the [output node configuration](#output-node-configuration) will be accessible.

<p align="center">
  <img src="FileSelectionSection.png" alt="FileSelectionSection.png"/>
</p>

J2O will automatically offer you all .jip files and RO-Crates that are accessible by your OMERO groups. If you have none available, you can upload files as attachments to any of your projects or datasets. To do so, select a dataset or project and go to <b>General → Attachments</b> in the right panel. Click the <b>+</b> to attach a file. To ensure compatibility, be sure to follow the [*Preparing JIPipe workflows* section](WorkflowDesign.md).

<p align="center">
  <img src="AttachFiles.png" alt="Attach_JIP_File.png"/>
</p>

## RUNNING JOBS
In this section you will find a list of all the JIPipe jobs currently running on the server that were initiated by the current user. They can be identified by the time and date of execution and the name of the associated workflow file. By clicking the ✖ next to the entry, you can terminate the associated job after confirming this action.

![Job section](RunningJobsSection.png)

You can also click on the name of a workflow to open a pop-up window that will display a live stream of its logfile:

![Live log pop-up](LogPopUp.png)

## NODE SUMMARY
In this section you will find an overview of the nodes detected in the associated workflow file. This can be used as a debugging tool to see whether the JIPipe pipeline was constructed correctly according to the [*Preparing JIPipe workflows* section](WorkflowDesign.md) and J2O therefore automatically detects the right amount of nodes.

![Node summary section](NodeSummarySection.png)

## INPUT NODE CONFIGURATION
This section allows you to enter the IDs of the OMERO objects that you want to use as input for specific nodes. Clicking the input field will open a scrollable dropdown menu that lists all available objects available to your OMERO group. You can simply click a listed object to add it to the input field. You can also search for a specific object by typing its name into the input field. To remove an entry from the input field, click the ✖ next to the ID.

![Input node config section](InputNodeConfigSection.png)

## OUTPUT NODE CONFIGURATION
When checking **Enable output config** in the [file selection](#file-selection), the output configuration becomes available. Here, you can choose a destination project for each of your exporter nodes. The data that is being exported by the node will be saved to the selected project as a dataset. Below the project input, you can enter the name you want the saved dataset to have. 

![Output node config section](OutputConfigSection.png)

>If you don't enter anything, J2O will default to a project called "JipipeResultsDefault". The images will be saved within that project in a dataset named after the workflow file and the start time of execution.

## PARAMETER CONFIGURATION
This section contains the input fields of the parameters that are defined as reference parameters within the workflow file. Starting with JIPipe 6, nodes with a predefined set of valid options will have a dropdown menu to choose a value from. Other nodes will accept strings, integers or floats as input depending on the node type. When hovering the **?**, the plugin will display a tooltip with the description of the respective parameter if it was set in the workflow's [project overview](https://jipipe.hki-jena.de/documentation/project-overview.html).

Below this section you will find the **Start J2O** button to execute the selected workflow file with all the changes you made in the webclient.

![Parameter config section](ParameterConfigSection.png)

## LOG WINDOW
Below the start button, you will find the log window. The window will livestream the JIPipe logfile of the most recently started workflow execution. This can be used to check on the progress of the execution or to debug problems within the workflow. Any additional information will also be displayed here. For example, if an error occurs a helpful message will tell you what went wrong.

![Log window section](LogWindowSection.png)

The log window will only ever display the content of the log file from the most recent job started by the user. To review old log files or to inspect errors of jobs that ran in the background, you can find the log files under the attachment tab of the output dataset. By clicking on the file, an automated download will be started.

![Old log files](FindLogFiles.png)

> If you have multiple jobs running you can always inspect the log livestream of a specific job by clicking its name in the [*Running Jobs* section](#running-jobs).