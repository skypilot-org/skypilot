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
    ~/.local/bin/uv pip install --prerelease allow azure-cli --system

# Add source code
COPY . /skypilot-src

# Install SkyPilot
# If triggered from release/nightly build pipeline, install from wheel file
# Otherwise install from source in editable mode
RUN cd /skypilot-src && \
    if ls dist/skypilot-*.whl 1> /dev/null 2>&1; then \
        echo "Installing from wheel file" && \
        WHEEL_FILE=$(ls dist/skypilot-*.whl) && \
        ~/.local/bin/uv pip install "${WHEEL_FILE}[all]" --system; \
    else \
        echo "Installing in editable mode" && \
        ~/.local/bin/uv pip install -e ".[all]" --system; \
    fi


RUN sky -v && \
    sky api info

# Set up Node.js and build dashboard (only when installing from source)
RUN if [ ! -f /skypilot-src/dist/skypilot-*.whl ]; then \
        echo "Setting up Node.js and building dashboard" && \
        curl -fsSL https://deb.nodesource.com/setup_23.x | bash - && \
        apt-get update && \
        apt-get install -y nodejs && \
        cd /skypilot-src/sky/dashboard && \
        npm ci && \
        npm run build; \
    else \
        echo "Skipping dashboard build as wheel file exists"; \
    fi

# Cleanup all caches to reduce the image size
RUN conda clean -afy && \
    ~/.local/bin/uv cache clean && \
    rm -rf ~/.cache/pip ~/.cache/uv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
