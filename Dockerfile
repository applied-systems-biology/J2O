FROM openmicroscopy/omero-web-standalone:5
USER root

# Install JIPipeRunner
WORKDIR /opt
RUN /opt/omero/web/venv3/bin/pip install git+https://asb-git.hki-jena.de/MWank/OMERO_JIPipe_Plugin.git

# Install tools and Java runtime for Fiji
RUN dnf install -y java-17-openjdk-headless xorg-x11-server-Xvfb && dnf clean all

# ---- INSTALL ALL DIFFERENT JIPIPE VERSIONS ------------
RUN mkdir JIPipe_Installations

# MAJOR VERSION 4
RUN mkdir /opt/JIPipe_Installations/4
WORKDIR /opt/JIPipe_Installations/4

# ---- INSTALL FIJI ----
RUN wget https://downloads.micron.ox.ac.uk/fiji_update/mirrors/fiji-stable/fiji-stable-linux64-jdk.zip && \
    unzip fiji-stable-linux64-jdk.zip && rm fiji-stable-linux64-jdk.zip && \
    chmod +x Fiji.app/ImageJ-linux64

# ---- SETUP UPDATE SITES ----
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "3D ImageJ Suite" "https://sites.imagej.net/Tboudier/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "clij" "https://sites.imagej.net/clij/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "clij2" "https://sites.imagej.net/clij2/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "IJ-OpenCV-Plugins" "https://sites.imagej.net/IJ-OpenCV/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "IJPB-plugins" "https://sites.imagej.net/IJPB-plugins/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "ImageScience" "https://sites.imagej.net/ImageScience/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "Multi-Template-Matching" "https://sites.imagej.net/Multi-Template-Matching/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "OMERO 5.5-5.6" "https://sites.imagej.net/OMERO-5.5-5.6/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "N5" "https://sites.imagej.net/N5/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update update

# ---- INSTALL JIPipe ----
RUN mkdir Fiji.app/plugins/JIPipe
WORKDIR /opt/Fiji.app/plugins/JIPipe
RUN wget https://github.com/applied-systems-biology/jipipe/releases/download/pom-jipipe-4.1.0/JIPipe-4.1.0.zip && \
    unzip JIPipe-4.1.0.zip && rm JIPipe-4.1.0.zip

# RESET WORKDIR
WORKDIR /opt

# MAJOR VERSION 5
RUN mkdir /opt/JIPipe_Installations/5
WORKDIR /opt/JIPipe_Installations/5

# ---- INSTALL FIJI ----
RUN wget https://downloads.micron.ox.ac.uk/fiji_update/mirrors/fiji-stable/fiji-stable-linux64-jdk.zip && \
    unzip fiji-stable-linux64-jdk.zip && rm fiji-stable-linux64-jdk.zip && \
    chmod +x Fiji.app/ImageJ-linux64

# ---- SETUP UPDATE SITES ----
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "3D ImageJ Suite" "https://sites.imagej.net/Tboudier/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "clij" "https://sites.imagej.net/clij/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "clij2" "https://sites.imagej.net/clij2/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "IJ-OpenCV-Plugins" "https://sites.imagej.net/IJ-OpenCV/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "IJPB-plugins" "https://sites.imagej.net/IJPB-plugins/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "ImageScience" "https://sites.imagej.net/ImageScience/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "Multi-Template-Matching" "https://sites.imagej.net/Multi-Template-Matching/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "OMERO 5.5-5.6" "https://sites.imagej.net/OMERO-5.5-5.6/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update add-update-site "N5" "https://sites.imagej.net/N5/"
RUN xvfb-run Fiji.app/ImageJ-linux64 --headless --update update

# ---- INSTALL JIPipe ----
RUN mkdir Fiji.app/plugins/JIPipe
WORKDIR /opt/Fiji.app/plugins/JIPipe
RUN wget https://github.com/applied-systems-biology/jipipe/releases/download/pom-jipipe-5.3.0/JIPipe-5.3.0.zip && \
    unzip JIPipe-5.3.0.zip && rm JIPipe-5.3.0.zip

# RESET WORKDIR
WORKDIR /opt

# ---- INSTALL PLUGIN DEPENDENCIES ----
COPY requirements.txt /tmp/requirements.txt
RUN /opt/omero/web/venv3/bin/pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

ENTRYPOINT[]

CMD ["/opt/omero/web/venv3/bin/celery", "-A", "JIPipePlugin", "worker", "--loglevel=info", "-E"]
