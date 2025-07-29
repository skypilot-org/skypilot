# Stage 1: Install Google Cloud SDK using APT
FROM python:3.10-slim AS gcloud-apt-install

RUN apt-get update && \
    apt-get install -y curl gnupg lsb-release && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update && \
    apt-get install --no-install-recommends -y \
        google-cloud-cli \
        google-cloud-cli-gke-gcloud-auth-plugin && \
    apt-get clean && rm -rf /usr/lib/google-cloud-sdk/platform/bundledpythonunix \
    /var/lib/apt/lists/*

# Stage 2: Main image
FROM python:3.10-slim

# Copy Google Cloud SDK from Stage 1
COPY --from=gcloud-apt-install /usr/lib/google-cloud-sdk /opt/google-cloud-sdk

# Set environment variable
ENV PATH="/opt/google-cloud-sdk/bin:$PATH"

# Detect architecture
ARG TARGETARCH

# Control Next.js basePath for staging deployments
ARG NEXT_BASE_PATH=/dashboard

# Control installation method - default to install from source
ARG INSTALL_FROM_SOURCE=true

# Install system packages
RUN apt-get update -y && \
    apt-get install --no-install-recommends -y \
        git gcc rsync sudo patch openssh-server \
        pciutils nano fuse socat netcat-openbsd curl tini autossh jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

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
# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ~/.local/bin/uv pip install --prerelease allow azure-cli --system && \
    if [ "$INSTALL_FROM_SOURCE" = "true" ]; then \
        echo "Installing NPM and Node.js for dashboard build" && \
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
        apt-get install -y nodejs && \
        npm install -g npm@latest; \
    fi && \
    ~/.local/bin/uv cache clean && \
    rm -rf ~/.cache/pip ~/.cache/uv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Add source code
COPY . /skypilot

# Install SkyPilot and set up dashboard based on installation method
RUN cd /skypilot && \
    if [ "$INSTALL_FROM_SOURCE" = "true" ]; then \
        echo "Installing from source in editable mode" && \
        ~/.local/bin/uv pip install -e ".[all]" --system && \
        echo "Building dashboard" && \
        npm --prefix sky/dashboard install && \
        NEXT_BASE_PATH=${NEXT_BASE_PATH} npm --prefix sky/dashboard run build; \
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
    fi && \
    # Cleanup all caches to reduce the image size
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
