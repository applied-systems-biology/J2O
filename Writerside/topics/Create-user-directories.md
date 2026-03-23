# Create user directories

J2O utilizes JIPipe user directories to facilitate saving processed data to OMERO and using OMERO image data as input for custom scripts or expression nodes.

> To create user directories, navigate to the project overview and click the "Directories" button and select "Add new directory".

## Output directories

To store processed data back to OMERO you will need to create at least one output user directory. To mark a directory as output, include "output" in the key you set for the directory. These output directories will then need to be used in the [export nodes](Export-nodes.md).

> Output directories are used to store both image and non-image data to OMERO

## Input directories

To use OMERO image data as input in custom scripts and JIPipe expression nodes, you need at least one input directory. To mark a directory as input, include "input" in the key you set for the directory.
