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


# Stage 2: Process the source code for INSTALL_FROM_SOURCE
FROM python:3.10-slim AS process-source

# Control installation method - default to install from source
ARG INSTALL_FROM_SOURCE=true

COPY . /skypilot

RUN cd /skypilot && \
    if [ "$INSTALL_FROM_SOURCE" != "true" ]; then \
        echo "Removing source code (wheel installation)" && \
        # Retain an /skypilot/dist dir to keep the compatibility in stage 3 and reduce the final image size
        mv /skypilot/dist /dist.backup && cd .. && rm -rf /skypilot && mkdir /skypilot && mv /dist.backup /skypilot/dist; \
    else \
        echo "Keeping source code and record commit sha (editable installation)" && \
        apt-get update -y && \
        apt-get install --no-install-recommends -y git && \
        apt-get clean && rm -rf /var/lib/apt/lists/* && \
        python -c "import setup; setup.replace_commit_hash()" && \
        # Remove .git dir to reduce the final image size
        rm -rf .git; \
    fi


# Stage 3: Main image
FROM python:3.10-slim

ARG INSTALL_FROM_SOURCE=true

# Copy Google Cloud SDK from Stage 1
COPY --from=gcloud-apt-install /usr/lib/google-cloud-sdk /opt/google-cloud-sdk

# Set environment variable
ENV PATH="/opt/google-cloud-sdk/bin:$PATH"

# Detect architecture
ARG TARGETARCH

# Control Next.js basePath for staging deployments
ARG NEXT_BASE_PATH=/dashboard

# Install system packages
RUN apt-get update -y && \
    apt-get install --no-install-recommends -y \
        git gcc rsync sudo patch openssh-server \
        pciutils nano fuse socat netcat-openbsd curl tini autossh jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install the session manager plugin for AWS CLI.
RUN curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb" && \
sudo dpkg -i session-manager-plugin.deb && \
rm session-manager-plugin.deb

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
COPY --from=process-source /skypilot /skypilot

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
    # Remove the empty /skypilot dir for backward compatibility
    if [ "$INSTALL_FROM_SOURCE" != "true" ]; then \
        rm -rf /skypilot; \
    fi
