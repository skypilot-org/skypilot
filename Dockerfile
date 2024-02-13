# Use the latest version with Python 3.10
FROM continuumio/miniconda3:23.3.1-0

# Install dependencies
RUN conda install -c conda-forge google-cloud-sdk && \
    apt update -y && \
    apt install rsync -y && \
    rm -rf /var/lib/apt/lists/*

COPY . /sky/

WORKDIR /sky/

# Install sky
RUN cd /sky/ && \
    pip install ".[all]"
