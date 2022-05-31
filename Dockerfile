FROM continuumio/miniconda3:4.11.0

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
