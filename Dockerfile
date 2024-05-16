# Use the latest version with Python 3.10
FROM continuumio/miniconda3:23.3.1-0

# Install dependencies
RUN conda install -c conda-forge google-cloud-sdk && \
    apt update -y && \
    apt install rsync vim -y && \
    rm -rf /var/lib/apt/lists/*

# Install sky
RUN pip install --no-cache-dir "skypilot[all]==0.5.0"
