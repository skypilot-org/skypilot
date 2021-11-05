# Service Catalog

The Sky supports 3 major clouds: AWS, Azure and GCP.

This module provides information including the instance type offerings, their pricing and data transfer costs. It also provides functions to query these information, and to select the most suitable instance types based on resource requirements.

- `data/{aws,azure,gcp}.csv`: the prefilled offerings and pricing list files.
- `probes/{aws,azure,gcp}_probe.py`: each file is a standalone script that queries the cloud APIs to produce the data files.
- `{aws,azure,gcp}_catalog.py`: Singleton-classes that load the data files and provide functions to query for instance offerings based on resource requirements. Used by `clouds/{aws,azure,gcp}.py`.
