# [Choice] Ubuntu version (use hirsute or bionic on local arm64/Apple Silicon): hirsute, focal, bionic
ARG VARIANT="focal"
FROM mcr.microsoft.com/vscode/devcontainers/base:${VARIANT}

RUN apt-get update -y && apt-get install -y \
    # common stuff
    git \
    wget \
    gnupg \
    apt-transport-https \
    ca-certificates \
    apt-utils \
    curl \
    jq \
    # Additional dependencies for user management
    sudo

# ========================================================================================================
# Update repository signing keys
# --------------------------------------------------------------------------------------------------------
# Hyperledger
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 9692C00E657DDE61 && \
    # Sovrin
    apt-key adv --keyserver keyserver.ubuntu.com --recv-keys CE7709D068DB5E88
# ========================================================================================================

# Add repository sources
RUN echo "deb  https://hyperledger.jfrog.io/artifactory/indy focal dev"  >> /etc/apt/sources.list && \
    echo "deb http://security.ubuntu.com/ubuntu bionic-security main"  >> /etc/apt/sources.list && \
    echo "deb https://repo.sovrin.org/deb bionic master" >> /etc/apt/sources.list && \
    echo "deb https://repo.sovrin.org/sdk/deb bionic master" >> /etc/apt/sources.list

RUN apt-get update -y && apt-get install -y \
    # Python
    python3-pip \
    python3-nacl \
    # rocksdb python wrapper
    rocksdb=5.8.8 \
    libgflags-dev \
    libsnappy-dev \
    zlib1g-dev \
    libbz2-dev \
    liblz4-dev \
    # zstd, fpm, and dependencies
    zstd \
    ruby \
    ruby-dev \
    rubygems \
    gcc \
    make \
    # Indy Node and Plenum dependencies
    libssl1.0.0 \
    ursa=0.3.2-1 \
    # Indy SDK
    libindy=1.15.0~1625-bionic

# Move libursa.so
RUN mv /usr/lib/ursa/* /usr/lib && rm -rf /usr/lib/ursa

# Python packages
RUN pip3 install -U setuptools==50.3.2 'pyzmq==22.3.0' Cython==0.29.36

# Clone and install Indy Node
RUN git clone https://github.com/hyperledger/indy-node.git /opt/indy-node && \
    cd /opt/indy-node && \
    pip3 install -e .

# Install indy-plenum and fpm
RUN pip3 install indy-plenum && \
    gem install --no-document rake dotenv:2.8.1 fpm:1.14.2


# # Create and set ownership for all required directories
# RUN mkdir -p /opt/indy-network /etc/indy /var/lib/indy/keys /var/lib/indy/ledger /var/lib/indy/genesis /var/lib/indy/log /var/lib/indy/plugins /var/lib/indy/node_info && \
#     groupadd -r indyuser && \
#     useradd -r -g indyuser -d /home/indyuser -s /bin/bash -m indyuser && \
#     echo "indyuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/indyuser && \
#     chown -R indyuser:indyuser /opt/indy-network /etc/indy /var/lib/indy

# Copy entrypoint script and Python script files
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

COPY indy-network-files/*.py /opt/indy-network/
RUN chmod +x /usr/local/bin/entrypoint.sh

# Cleanup
RUN apt-get -y autoremove && \
    rm -rf /var/lib/apt/lists/*


# # Switch to indyuser
# USER indyuser


# Set the entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]