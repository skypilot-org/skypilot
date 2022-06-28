FROM public.ecr.aws/a9w6z7w5/sky:latest

# Initialize conda for root user, install ssh and other local dependencies
RUN conda init && \
    apt update -y && \
    apt install openssh-server pciutils nano -y && \
    rm -rf /var/lib/apt/lists/* && \
    pip install ray[default]==1.10

# Setup SSH and generate hostkeys
RUN mkdir -p /var/run/sshd && \
    sed -i 's/PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed 's@session\s*required\s*pam_loginuid.so@session optional pam_loginuid.so@g' -i /etc/pam.d/sshd && \
    cd /etc/ssh/ && \
    ssh-keygen -A

# Setup new user named sky and add to sudoers. Also add /opt/conda/bin to sudo path.
RUN useradd -m -s /bin/bash sky && \
    echo "sky ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    echo 'Defaults        secure_path="/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' > /etc/sudoers.d/sky

# Switch user and set WORKDIR
USER sky
WORKDIR /home/sky

# Initialize conda for sky user
RUN conda init
