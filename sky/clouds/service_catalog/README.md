# Service Catalog

This module provides information for clouds supported by SkyPilot, including the instance type offerings, their pricing and data transfer costs. It also provides functions to query these information, and to select the most suitable instance types based on resource requirements. Primarily used by the Clouds module.

- `data_fetchers/fetch_{aws,azure}.py`: each file is a standalone script that queries the cloud APIs to produce the pricing list files.
- `data_fetchers/fetch_gcp.py`: A script that generates the GCP catalog based by crawling GCP websites.
- `{aws,azure,gcp}_catalog.py`: Singleton-classes that load the data files and provide functions to query for instance offerings based on resource requirements.
