FROM continuumio/miniconda3:4.11.0

COPY . /sky/

WORKDIR /sky/

# Install dependencies
RUN pip install google-api-python-client && \
    conda install -c conda-forge google-cloud-sdk && \
    apt update -y && \
    apt install rsync -y && \
    rm -rf /var/lib/apt/lists/*

# Install sky
RUN cd /sky/ && \
    pip install ".[all]"
