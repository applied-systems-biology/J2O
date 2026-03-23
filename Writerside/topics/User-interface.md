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

J2O will automatically offer you all .jip files and RO-Crates that are accessible by your OMERO groups. If you have none available, you can upload files as attachments to any of your projects or datasets. To do so, select a dataset or project and go to <b>General → Attachments</b> in the right panel. Click the <b>+</b> to attach a file. To ensure compatibility, be sure that it adheres to the <a href="#pipeline-design-constraints">pipeline design constraints</a>.

<p align="center">
  <img src="AttachFiles.png" alt="Attach_JIP_File.png"/>
</p>

## RUNNING JOBS
In this section you will find a list of all the JIPipe jobs currently running on the server that were initiated by the current user. They can be identified by the time and date of execution and the name of the associated workflow file. By clicking the ✖ next to the entry, you can terminate the associated job after confirming this action.

![Job section](RunningJobsSection.png)

You can also click on the name of a workflow to open a pop-up window that will display a live stream of its logfile:

![Live log pop-up](LogPopUp.png)

## NODE SUMMARY
In this section you will find an overview of the nodes detected in the associated .jip file. This can be used as a debugging tool to see whether the JIPipe pipeline was constructed correctly according to the [preparing JIPipe workflows section](WorkflowDesign.md) and JIPipeRunner therefore automatically detects the right amount of nodes.

![Node summary section](NodeSummarySection.png)

## INPUT NODE CONFIGURATION
This section will allow to enter the IDs of the datasets that contain the input images. On clicking the input field, a scrollable dropdown menu will be shown that lists all available datasets according to your OMERO group. You can simply click a listed dataset to add it to the input field. You can also search for a specific dataset by typing its name into the input field. To remove an entry from the input field, click the ✖ next to the ID.

![Input node config section](InputNodeConfigSection.png)

## OUTPUT NODE CONFIGURATION
When checking **Enable output config** in the [file selection](#file-selection), the output configuration becomes available. Here you can choose a pre-existing project (the same way as selecting the input dataset ID) that you want to save the generated output dataset to and give the dataset a custom name. If you don't enter anything (either when not checking **Enable output config** or when you are not changing the placeholders) the outputs of your pipeline will be saved in a project called "JipipeResultsDefault". The images will be saved within that project in a dataset named after the .jip file and the start time of execution (e.g. FolderListTest.jip@01-09-2025_15:07:41).

![Output node config section](OutputConfigSection.png)

## PARAMETER CONFIGURATION
This section contains the input fields of the parameters that are defined as reference parameters within the .jip file. Nodes with a predefined set of valid options will have a dropdown menu to choose from. Other nodes will accept strings, integers or floats as input depending on the node type. When hovering the **?** the plugin will display a tooltip with the description of the respective parameter (if it was set in the [project overview](https://jipipe.hki-jena.de/documentation/project-overview.html)).

Below this section you will find the **Start JIPipeRunner** button to execute the selected .jip file.

![Parameter config section](ParameterConfigSection.png)

## LOG WINDOW
Below the button that starts the pipeline execution, you will find the log window. During execution, the window will livestream the JIPipe logfile. This can be used to check on the current progress of the execution or to debug problems within the workflow. Any additional information will also be displayed here. For example, if an error occurs a helpful message will tell you what went wrong.

![Log window section](LogWindowSection.png)

The log window will only ever display the content of the log file from the most recent job started by the user. To review old log files or to inspect errors of jobs that ran in the background, you can find the log files under the attachment tab of the output dataset. By clicking on the file, an automated download will be started.

![Old log files](FindLogFiles.png)