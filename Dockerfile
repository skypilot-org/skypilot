FROM continuumio/miniconda3:4.11.0

# Install dependencies, sudo and ensure sudo path includes /opt/conda/bin/python3 and uninstall default python3
RUN conda install -c conda-forge google-cloud-sdk && \
    apt update -y && \
    apt install rsync sudo -y && \
    rm -rf /var/lib/apt/lists/* && \
    apt remove -y python3


COPY . /sky/

WORKDIR /sky/

# Install sky
RUN cd /sky/ && \
    pip install ".[all]"
