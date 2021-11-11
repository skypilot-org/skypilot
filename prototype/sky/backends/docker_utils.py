"""Utilities for docker image generation."""

from typing import Optional, Union, List

from sky import logging

logger = logging.init_logger(__name__)

DOCKERFILE_TEMPLATE = f"""
FROM {}
COPY . /app
RUN make /app
CMD python /app/app.py
"""

def create_dockerfile(output_path: str,
                      base_image: str,
                      setup_commands: str,
                      copy_paths: List[str]):

