# Summary

JIPipe to OMERO (J2O) is a plugin for [omero-web](https://github.com/ome/omero-web) that makes it possible to run [JIPipe](https://jipipe.hki-jena.de/) workflows directly on the server that is hosting the OMERO database. This eliminates the need for users to share their data and workflows outside of OMERO and greatly reduces the data traffic.

The complete documentation has been moved to [the J2O documentation website](https://applied-systems-biology.github.io/J2O-Documentation/). For legacy documentation (prior to the JIPipe 6.0.0 integration) check [the legacy documentation folder (LegacyDocs)](LegacyDocs).

## Features
Frontend-features include:

- Dynamic single page application
- Smooth OMERO integration
- Job management section
- Configurable I/O
- Error resistant UI design using TomSelect
- Customizable JIPipe tooltips
- Live log streaming and log archiving

Backend features include:

- Celery task queue
- Redis distributed caching
- Job status tracking
- Directory management with automated cleanup
- RO-Crate support
- JIPipe containers

## Requirements
- Python 3.10
- omero-web
- Django
- podman
- Celery 
- redis

## License & Attribution

Copyright (c) 2026 Marius Wank

This project is licensed under the **MIT License**.  
See the [license file](LICENSE.txt) for details.

### Authors & Affiliation

Marius Wank, Ruman Gerst, Marc Thilo Figge

Research Group Applied Systems Biology - Head: Prof. Dr. Marc Thilo Figge  
https://www.leibniz-hki.de/en/applied-systems-biology.html  
HKI-Center for Systems Biology of Infection  
Leibniz Institute for Natural Product Research and Infection Biology - Hans Knöll Institute (HKI)  
Adolf-Reichwein-Straße 23, 07745 Jena, Germany

---

## Dependencies & Third-Party Tools

This software makes use of the following third-party components:

- **Tom Select** (UI select widget)  
  Licensed under the [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0)

---

## JIPipe Dependency Notice

This plugin is designed to work with **JIPipe**, developed by **Ruman Gerst and Zoltán Csereynes**.

JIPipe is **not included** in this plugin’s distribution and must be installed separately.  
Please visit [https://jipipe.org](https://jipipe.org) for installation and licensing information.