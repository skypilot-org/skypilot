# Use the latest version with Python 3.10
FROM continuumio/miniconda3:23.3.1-0

# Install dependencies
RUN conda install -c conda-forge google-cloud-sdk && \
    gcloud components install gke-gcloud-auth-plugin --quiet && \
    find /opt/conda -name 'gke-gcloud-auth-plugin' -type f -exec ln -s {} /usr/local/bin/gke-gcloud-auth-plugin \; && \
    # Install system packages
    apt-get update -y && \
    apt-get install --no-install-recommends -y \
        git gcc rsync sudo patch openssh-server \
        pciutils nano fuse socat netcat curl rsync vim && \
    rm -rf /var/lib/apt/lists/* && \
    # Install kubectl
    curl -LO "https://dl.k8s.io/release/v1.28.11/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl && \
    # Install uv and skypilot
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ~/.local/bin/uv pip install --prerelease=allow skypilot-nightly[all] --system && \
    # Cleanup all caches to reduce the image size
    conda clean -afy && \
    rm -rf ~/.cache/pip ~/.cache/uv
