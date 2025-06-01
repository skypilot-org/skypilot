# Use the latest version with Python 3.10
FROM continuumio/miniconda3:23.3.1-0

# Detect architecture
ARG TARGETARCH

# Install dependencies
RUN conda install -c conda-forge google-cloud-sdk && \
    gcloud components install gke-gcloud-auth-plugin --quiet && \
    find /opt/conda -name 'gke-gcloud-auth-plugin' -type f -exec ln -s {} /usr/local/bin/gke-gcloud-auth-plugin \; && \
    # Install system packages
    apt-get update -y && \
    apt-get install --no-install-recommends -y \
        git gcc rsync sudo patch openssh-server \
        pciutils nano fuse socat netcat-openbsd curl rsync vim tini autossh jq && \
    rm -rf /var/lib/apt/lists/* && \
    # Install kubectl based on architecture
    ARCH=${TARGETARCH:-$(case "$(uname -m)" in \
        "x86_64") echo "amd64" ;; \
        "aarch64") echo "arm64" ;; \
        *) echo "$(uname -m)" ;; \
    esac)} && \
    curl -LO "https://dl.k8s.io/release/v1.31.6/bin/linux/$ARCH/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl && \
    curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | NEBIUS_INSTALL_FOLDER=/usr/local/bin bash && \
    # Install uv and skypilot
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ~/.local/bin/uv pip install --prerelease allow azure-cli --system && \
    ~/.local/bin/uv pip install skypilot[all]==0.10.0 --system && \
    # Cleanup all caches to reduce the image size
    conda clean -afy && \
    ~/.local/bin/uv cache clean && \
    rm -rf ~/.cache/pip ~/.cache/uv
