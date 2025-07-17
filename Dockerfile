# Use the latest version with Python 3.10
FROM continuumio/miniconda3:23.3.1-0

# Detect architecture
ARG TARGETARCH

# Control installation method - default to install from source
ARG INSTALL_FROM_SOURCE=true

# Install Google Cloud SDK (least likely to change)
RUN conda install -c conda-forge google-cloud-sdk

# Install GKE auth plugin
RUN gcloud components install gke-gcloud-auth-plugin --quiet && \
    find /opt/conda -name 'gke-gcloud-auth-plugin' -type f -exec ln -s {} /usr/local/bin/gke-gcloud-auth-plugin \;

# Install system packages
RUN apt-get update -y && \
    apt-get install --no-install-recommends -y \
        git gcc rsync sudo patch openssh-server \
        pciutils nano fuse socat netcat-openbsd curl rsync vim tini autossh jq && \
    rm -rf /var/lib/apt/lists/*

# Install kubectl based on architecture
RUN ARCH=${TARGETARCH:-$(case "$(uname -m)" in \
        "x86_64") echo "amd64" ;; \
        "aarch64") echo "arm64" ;; \
        *) echo "$(uname -m)" ;; \
    esac)} && \
    curl -LO "https://dl.k8s.io/release/v1.31.6/bin/linux/$ARCH/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

# Install Nebius CLI
RUN curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | NEBIUS_INSTALL_FOLDER=/usr/local/bin bash

# Add source code
COPY . /skypilot

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ~/.local/bin/uv pip install --prerelease allow azure-cli --system

# Install SkyPilot and set up dashboard based on installation method
RUN cd /skypilot && \
    if [ "$INSTALL_FROM_SOURCE" = "true" ]; then \
        echo "Installing from source in editable mode" && \
        ~/.local/bin/uv pip install -e ".[all]" --system && \
        echo "Installing NPM and Node.js for dashboard build" && \
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
        apt-get install -y nodejs && \
        npm install -g npm@latest && \
        echo "Building dashboard" && \
        npm --prefix sky/dashboard install && npm --prefix sky/dashboard run build; \
    else \
        echo "Installing from wheel file" && \
        WHEEL_FILE=$(ls dist/*skypilot*.whl 2>/dev/null | head -1) && \
        if [ -z "$WHEEL_FILE" ]; then \
            echo "Error: No wheel file found in /skypilot/dist/" && \
            ls -la /skypilot/dist/ && \
            exit 1; \
        fi && \
        ~/.local/bin/uv pip install "${WHEEL_FILE}[all]" --system && \
        echo "Skipping dashboard build for wheel installation"; \
    fi

# Cleanup all caches to reduce the image size
RUN conda clean -afy && \
    ~/.local/bin/uv cache clean && \
    rm -rf ~/.cache/pip ~/.cache/uv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Remove source code if installed from wheel (not needed for wheel installs)
    if [ "$INSTALL_FROM_SOURCE" != "true" ]; then \
        echo "Removing source code (wheel installation)" && \
        rm -rf /skypilot; \
    else \
        echo "Keeping source code (editable installation)"; \
    fi
